"""
Public, unauthenticated endpoints for the requester-facing web form
(PRD 4.1). No login required — anyone on site can submit a work order.

Spam/abuse protection, per the PRD's "basic spam/abuse protection (rate
limiting, honeypot field, or lightweight CAPTCHA)":
  - a honeypot field ("website") that real users never see or fill in,
    checked before anything else
  - a per-IP submission rate limit (see app/rate_limit.py)

Deliberately does NOT reuse the authenticated /work-orders router: the
public surface needs a narrower, name-validated set of fields and its own
abuse controls, and keeping it separate means a bug here can't accidentally
loosen anything on the authenticated LOC/tech endpoints.

SMS/email sending is now wired up (Phase 25, PRD §17#6-7) — see
app/notifications.py, called from crud.py at submission and at the
Work In Progress / Closed transitions.

Correction (8/2/26): notify_preference (an opt-in email/text/both
selector) was a leftover from an earlier design — it was deliberately
removed from the Submit WO form when POC contact was added, in favor
of anchoring on the requester's phone number (now required) as the
single notification identifier, with email captured for the requester
only (not for a selection of notification channels). See the PRD's
§17#6 entry for the corrected, current design — do not reintroduce a
preference selector here without checking that decision first.
"""
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from .. import crud, models, rate_limit, schemas
from ..database import get_db
from ..auth import create_access_token

router = APIRouter(prefix="/public", tags=["public"])

# Enhancement backlog Phase 16 (PRD §14#29): local disk storage doesn't
# survive a redeploy on Render's default ephemeral filesystem — confirmed
# in production 7/29/26, every attachment uploaded before a redeploy went
# missing after it. Fixed via a Render persistent disk (see render.yaml's
# `disk` block), not a code change: UPLOAD_DIR is now set via the
# UPLOAD_DIR env var to the disk's mount path, which IS durable across
# restarts/redeploys — this default ("uploads", a relative path) only
# still applies to local dev / any environment that doesn't set the env
# var. A persistent disk only works with a single service instance (no
# horizontal autoscaling) — not a constraint this app has today, but if
# that ever changes, swap this for object storage (S3-compatible, e.g.
# Cloudflare R2 for its no-egress-fee pricing) and store the resulting
# URL in WOAttachment.file_url same as now — nothing else in the model
# needs to change either way.
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_FILES_PER_SUBMISSION = 5
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
# Enhancement backlog Phase 10 (PRD §13#9): PDFs are common for things
# like a packing list or a formal transportation request — added
# alongside the original photo-only allowlist rather than replacing it.
ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf",
}


@router.get("/assets", response_model=list[schemas.PublicAssetOut])
def list_public_assets(db: Session = Depends(get_db)):
    """Powers the location typeahead on the public form. No auth, and
    deliberately returns less than the authenticated /assets endpoint."""
    return db.query(models.Asset).order_by(models.Asset.name).all()


@router.get("/locations/tree", response_model=list[schemas.LocationNode])
def get_public_location_tree(db: Session = Depends(get_db)):
    """Nested asset hierarchy for the public request form's location
    picker (PRD 4.2a). Same shape as the authenticated /locations/tree —
    no PII or internal fields involved either way, so nothing to narrow
    here unlike list_public_assets above.
    """
    return crud.build_location_tree(db, include_inactive=False)


@router.get("/request-types", response_model=List[str])
def get_public_request_types(db: Session = Depends(get_db)):
    """Bug fix, enhancement backlog Phase 5 (PRD §14#20): the "kind of
    issue" dropdown on the Submit WO form used to hardcode a fixed set
    of work-type strings. Request types are admin-editable (PRD 4.5c,
    `/admin/request-types`) — if an admin renames, deactivates, or adds
    to that list (a real, existing feature), the hardcoded dropdown
    silently drifted out of sync with what `_validate_work_type` (see
    crud.py) actually accepts, and every submission using one of the
    stale hardcoded values would fail with a 400 "invalid or inactive
    request type" — exactly the "Submit WO is failing" symptom this was
    found chasing down. This endpoint is now the single source of truth
    both sides read from: the dropdown, and the validator."""
    return [rt.name for rt in crud.list_request_types(db, include_inactive=False)]


@router.post("/work-orders", response_model=schemas.PublicWorkOrderConfirmation, status_code=201)
async def submit_public_work_order(
    request: Request,
    requester_name: str = Form(...),
    requester_email: Optional[str] = Form(None),
    requester_phone: Optional[str] = Form(None),
    poc_is_requester: str = Form("true"),  # 'true' | 'false' — HTML forms send strings, not booleans
    poc_name: Optional[str] = Form(None),
    poc_phone: Optional[str] = Form(None),
    asset_id: int = Form(...),
    work_type: str = Form(""),
    description: str = Form(...),
    priority: str = Form(...),
    notify_preference: Optional[str] = Form(None),  # 'email' | 'text' | 'both'
    # Enhancement backlog Phase 15 (PRD §13#14): optional geo pin-drop.
    # Sent as strings by the form (like everything else here) since it's
    # a plain multipart POST, not JSON — parsed to float below.
    latitude: Optional[str] = Form(None),
    longitude: Optional[str] = Form(None),
    website: Optional[str] = Form(None),  # honeypot — see module docstring
    files: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"

    if website:
        # A real requester never sees or fills this field. Rather than
        # reject outright (which teaches a bot to stop filling it), return
        # a plausible-looking success without touching the database.
        return schemas.PublicWorkOrderConfirmation(wo_number="00000")

    retry_after = rate_limit.check_public_submission_limit(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many requests from this connection. Please wait a few minutes and try again.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    requester_name = requester_name.strip()
    description = description.strip()
    requester_email = (requester_email or "").strip()
    requester_phone = (requester_phone or "").strip()
    if not requester_name:
        raise HTTPException(400, "name is required")
    if not description:
        raise HTTPException(400, "description is required")
    if not requester_email:
        raise HTTPException(400, "email is required")
    if not requester_phone:
        raise HTTPException(400, "phone is required")
    # Accept the literal strings a plain HTML form submits ('true'/'false');
    # anything else defaults to True (requester is the POC) rather than
    # rejecting the submission over a malformed flag.
    poc_is_requester_bool = (poc_is_requester or "true").strip().lower() != "false"
    poc_name = (poc_name or "").strip() or None
    poc_phone = (poc_phone or "").strip() or None
    if not poc_is_requester_bool and (not poc_name or not poc_phone):
        raise HTTPException(400, "poc_name and poc_phone are required when the requester is not the point of contact")
    if priority not in schemas.PRIORITIES:
        raise HTTPException(400, f"invalid priority: {priority}")
    crud._validate_work_type(db, work_type)  # request_types table (PRD 4.5c), not the old hardcoded tuple
    if notify_preference and notify_preference not in schemas.NOTIFY_PREFERENCES:
        raise HTTPException(400, f"invalid notify_preference: {notify_preference}")
    if notify_preference in ("text", "both") and not (requester_phone or "").strip():
        raise HTTPException(400, "a phone number is required to receive text updates")
    if notify_preference in ("email", "both") and not (requester_email or "").strip():
        raise HTTPException(400, "an email is required to receive email updates")
    if len(files) > MAX_FILES_PER_SUBMISSION:
        raise HTTPException(400, f"attach at most {MAX_FILES_PER_SUBMISSION} photos")

    # Enhancement backlog Phase 15 (PRD §13#14): blank/missing means "no
    # pin," not an error — the requester skipped the optional step.
    lat_val = None
    lng_val = None
    if latitude and longitude:
        try:
            lat_val = float(latitude)
            lng_val = float(longitude)
        except ValueError:
            raise HTTPException(400, "invalid coordinates")

    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name=requester_name,
            requester_email=requester_email or None,
            requester_phone=requester_phone or None,
            poc_is_requester=poc_is_requester_bool,
            poc_name=poc_name,
            poc_phone=poc_phone,
            asset_id=asset_id,
            work_type=work_type,
            description=description,
            priority=priority,
            notify_preference=notify_preference or None,
            latitude=lat_val,
            longitude=lng_val,
        ),
    )

    for f in files:
        if not f.filename:
            continue
        if f.content_type not in ALLOWED_CONTENT_TYPES:
            continue  # skip anything that isn't a photo rather than fail the whole submission
        contents = await f.read()
        if len(contents) > MAX_FILE_BYTES:
            continue
        ext = os.path.splitext(f.filename)[1][:10]
        safe_name = f"{wo.wo_number}-{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_DIR, safe_name), "wb") as out:
            out.write(contents)
        crud.add_attachment(db, wo, f"/uploads/{safe_name}")

    rate_limit.record_public_submission(client_ip)
    return schemas.PublicWorkOrderConfirmation(wo_number=wo.wo_number)


@router.get("/work-orders/lookup", response_model=List[schemas.PublicWorkOrderStatus])
def lookup_public_work_orders(phone: str, request: Request, search: Optional[str] = None,
                               db: Session = Depends(get_db)):
    """Enhancement backlog Phase 1 (PRD §13#4): status lookup is now
    anchored on phone number instead of a WO-number + email compound key
    — either the original requester's phone or a delegated POC's phone
    (WorkOrder.poc_phone) matches, and this returns every WO tied to that
    number rather than requiring the requester to already know a specific
    WO number. Digit-only matching (see crud._digits_only) so formatting
    differences don't cause false negatives.

    Enhancement backlog Phase 2 (PRD §13#5): `search`, if given, further
    narrows those results to WOs whose description or location contains
    the given text — replaces the old "search by exact WO number" idea
    with something a requester can actually use without already knowing
    a WO number.

    Trade-off worth noting: a phone number alone is now sufficient to see
    every WO tied to it (no second factor like the old email pairing) —
    that's the explicit intent of this enhancement (a POC who wasn't the
    original submitter needs to be able to look things up with just their
    own phone), but it does mean phone numbers are somewhat less "secret"
    here than the old wo_number+email pair was. Rate-limited below for
    basic abuse protection in place of a second factor.
    """
    client_ip = request.client.host if request.client else "unknown"
    retry_after = rate_limit.check_public_lookup_limit(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many lookups from this connection. Please wait a few minutes and try again.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    rate_limit.record_public_lookup(client_ip)

    digits = crud._digits_only(phone)
    if len(digits) < 7:
        raise HTTPException(400, "enter a valid phone number")

    wos = crud.lookup_work_orders_by_phone(db, digits, search=search)
    if not wos:
        raise HTTPException(404, "no work orders found for that phone number")
    return wos


@router.get("/settings", response_model=schemas.SettingsOut)
def get_public_settings(db: Session = Depends(get_db)):
    """Enhancement backlog Phase 4 (PRD §15#1): no auth required — this
    is how the (unauthenticated) Submit WO / status-lookup page knows
    which time zone to display dates in, same as the authenticated
    screens read via GET /admin/settings. Read-only; only an admin can
    change the value (see app/routers/admin.py)."""
    return crud.get_all_settings(db)


# ---------------------------------------------------------------------------
# Enhancement backlog Phase 20 (PRD §17#15): public "management" dashboard —
# a shareable link, no sign-in required, for leadership/stakeholders who
# just want the trend picture without needing an account.
#
# Deliberately aggregate-only, mirroring this whole router's existing
# "public surface stays narrower than the authenticated one" principle
# (see the module docstring above): these wrap the exact same
# crud.get_kpis/get_breakdowns/get_daily_trend functions the authenticated
# /dashboard/* endpoints use, but with NO filter params exposed at all (no
# status/priority/location/team/search — every one of those could be used
# to slice toward something identifiable) and a fixed scope (the overall
# system view, not audience-scoped) — there's nothing here to configure,
# on purpose. None of these three functions have ever returned WO-level
# detail (no requester name/phone/email, no description text) — they're
# pure counts/aggregates — so reusing them as-is is safe; the real
# access-control decision here is "no login," not "different data."
#
# Rate-limited the same way the phone-based status lookup is (per-IP,
# app/rate_limit.py) — a public, no-auth GET endpoint is exactly the kind
# of thing that can get scraped/hammered, and there's no requester action
# (like submitting a real work order) forcing a natural pace here the way
# there is on the submission endpoint.
# ---------------------------------------------------------------------------

def _check_public_dashboard_rate_limit(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    retry_after = rate_limit.check_public_lookup_limit(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many requests from this connection. Please wait a few minutes and try again.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    rate_limit.record_public_lookup(client_ip)


@router.get("/dashboard/kpis", response_model=schemas.KPIOut)
def public_dashboard_kpis(request: Request, db: Session = Depends(get_db)):
    _check_public_dashboard_rate_limit(request)
    return crud.get_kpis(db, scope="main")


@router.get("/dashboard/breakdowns", response_model=schemas.BreakdownOut)
def public_dashboard_breakdowns(request: Request, db: Session = Depends(get_db)):
    _check_public_dashboard_rate_limit(request)
    return crud.get_breakdowns(db, scope="main")


@router.get("/dashboard/trend")
def public_dashboard_trend(
    request: Request,
    days: int = 14,
    db: Session = Depends(get_db),
):
    _check_public_dashboard_rate_limit(request)
    # Bounded rather than trusting the query param outright — same
    # min/max the authenticated /dashboard/trend endpoint enforces
    # (app/routers/dashboard.py), kept here explicitly since this one
    # can't lean on FastAPI's Query(..., le=90) the authenticated route
    # uses without pulling in Query for one parameter.
    days = max(1, min(days, 90))
    return crud.get_daily_trend(db, scope="main", days=days)


# ---------------------------------------------------------------------------
# Enhancement backlog Phase 21 (PRD §17#10): Task Worker PIN login flow.
# Unauthenticated by necessity (the worker hasn't logged in yet) — but
# every response here is deliberately minimal: team names, worker names,
# and (only on successful login) a JWT. No PINs, no WO data, nothing
# else is ever exposed through this flow. Rate-limited the same way the
# phone-based status lookup is, same reasoning as the public dashboard
# endpoints above — an unauthenticated endpoint that accepts a guessable
# secret (a 4-digit PIN) absolutely needs a brute-force backstop.
# ---------------------------------------------------------------------------

@router.get("/worker-login/teams", response_model=list[schemas.TeamOut])
def worker_login_teams(db: Session = Depends(get_db)):
    return db.query(models.Team).filter(models.Team.is_active == True).order_by(models.Team.name).all()  # noqa: E712


@router.get("/worker-login/workers", response_model=list[schemas.TaskWorkerOut])
def worker_login_workers(team_id: int, db: Session = Depends(get_db)):
    """Names only, for the worker-login page's 'pick who you are' step
    — never includes a PIN or anything else. A worker still needs to
    know their own PIN to actually log in; this just saves them from
    typing their own name exactly right."""
    return crud.list_task_workers(db, team_id)


@router.post("/worker-login")
def worker_login(request: Request, payload: schemas.WorkerLoginRequest, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    retry_after = rate_limit.check_public_lookup_limit(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts from this connection. Please wait a few minutes and try again.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    rate_limit.record_public_lookup(client_ip)

    worker = crud.verify_worker_login(db, payload.worker_id, payload.pin)
    if not worker:
        raise HTTPException(401, "incorrect PIN")
    return {"access_token": create_access_token(worker.id), "token_type": "bearer"}
