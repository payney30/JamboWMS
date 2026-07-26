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


@router.post("/work-orders", response_model=schemas.PublicWorkOrderConfirmation, status_code=201)
async def submit_public_work_order(
    request: Request,
    requester_name: str = Form(...),
    requester_email: Optional[str] = Form(None),
    requester_phone: Optional[str] = Form(None),
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
        return schemas.PublicWorkOrderConfirmation(wo_number="WO-00000")

    retry_after = rate_limit.check_public_submission_limit(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many requests from this connection. Please wait a few minutes and try again.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    requester_name = requester_name.strip()
    description = description.strip()
    if not requester_name:
        raise HTTPException(400, "name is required")
    if not description:
        raise HTTPException(400, "description is required")
    if priority not in schemas.PRIORITIES:
        raise HTTPException(400, f"invalid priority: {priority}")
    if work_type not in schemas.WORK_TYPES:
        raise HTTPException(400, f"invalid work type: {work_type}")
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


@router.get("/work-orders/lookup", response_model=schemas.PublicWorkOrderStatus)
def lookup_public_work_order(wo_number: str, email: str, db: Session = Depends(get_db)):
    """PRD 4.1's optional status-lookup page: check a WO by number + the
    email it was submitted with, no login. Requires both to match and
    returns the same 404 either way a mismatch happens, so this can't be
    used to enumerate WO numbers or confirm an email was used to submit one."""
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == wo_number.strip()).first()
    if not wo or not wo.requester_email or wo.requester_email.strip().lower() != email.strip().lower():
        raise HTTPException(404, "no matching work order found")
    return wo
