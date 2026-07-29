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

TODO: notify_preference is captured and stored on the WO here, but nothing
actually sends anything yet — there's no email/SMS provider wired in. The
PRD calls for "email/SMS to requester on submission and on close, at
minimum." Once a provider is chosen (SES/SendGrid for email, Twilio or
similar for SMS), the send calls belong right after crud.create_work_order
below (submission) and in the status-change endpoint in
app/routers/work_orders.py (close) — both already have everything they need
(requester_email/phone, notify_preference) to decide what to send and where.
When wiring that up: text/call updates should go to requester_phone AND, if
poc_is_requester is False, to poc_phone as well — email stays scoped to
requester_email only (no poc_email field exists). See
NJ2026_Work_Order_System_PRD.md sections 4.1/5 for the recipient logic.
"""
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from .. import crud, models, rate_limit, schemas
from ..database import get_db

router = APIRouter(prefix="/public", tags=["public"])

# Local disk storage is fine for a single-process, ~2-week event deployment.
# If this ever needs to survive across instances/restarts reliably, swap
# this for object storage (S3-compatible) and store the resulting URL in
# WOAttachment.file_url same as now — nothing else in the model needs to change.
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_FILES_PER_SUBMISSION = 5
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


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
