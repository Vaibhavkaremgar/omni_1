from __future__ import annotations

import csv
import io
import re
import hashlib
import jwt
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.enums import CampaignStatus, ContactStatus
from app.services.auth import AuthenticatedUser
from app.core.config import get_settings

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_MIME = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def _normalize_phone(raw: str) -> str:
    value = raw.strip()
    had_plus = value.startswith("+")
    digits = re.sub(r"\D", "", value)
    if value.startswith("00"):
        digits = digits[2:]
    elif not had_plus and len(digits) == 10 and digits[0] in "6789":
        digits = "91" + digits
    return "+" + digits


def _is_valid_e164(phone: str) -> bool:
    return bool(E164_RE.match(phone))


def _upload_cipher() -> Fernet:
    key = hashlib.sha256(get_settings().auth_secret_key.encode()).digest()
    import base64
    return Fernet(base64.urlsafe_b64encode(key))


def _make_import_token(campaign_id: UUID, tenant_id: UUID, rows: list[dict]) -> str:
    import json, base64
    encrypted_rows = _upload_cipher().encrypt(json.dumps(rows, separators=(",", ":")).encode()).decode()
    return jwt.encode({"purpose": "campaign_import", "campaign_id": str(campaign_id), "tenant_id": str(tenant_id), "rows": encrypted_rows, "exp": datetime.now(timezone.utc) + timedelta(minutes=15)}, get_settings().auth_secret_key, algorithm="HS256")


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [row for row in reader]


def _parse_xlsx(content: bytes) -> list[dict[str, str]]:
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:
        raise HTTPException(status_code=422, detail="XLSX support requires openpyxl.") from exc
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(cell).strip().lower() if cell is not None else "" for cell in rows[0]]
    result = []
    for row in rows[1:]:
        result.append({headers[i]: (str(cell).strip() if cell is not None else "") for i, cell in enumerate(row)})
    return result

def _parse_xls(content: bytes) -> list[dict[str, str]]:
    try:
        import xlrd  # type: ignore
    except ImportError as exc:
        raise HTTPException(status_code=422, detail="XLS support requires xlrd.") from exc
    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_index(0)
    if sheet.nrows == 0:
        return []
    headers = [str(v).strip().lower() for v in sheet.row_values(0)]
    return [{headers[i]: str(v).strip() for i, v in enumerate(sheet.row_values(row)) if i < len(headers) and headers[i]} for row in range(1, sheet.nrows)]


def _find_column(row: dict[str, str], candidates: list[str]) -> str:
    for key in candidates:
        if key in row and row[key].strip():
            return row[key].strip()
    return ""


def _process_rows(raw_rows: list[dict[str, str]]) -> dict[str, Any]:
    valid: list[dict[str, str]] = []
    invalid: list[dict[str, Any]] = []
    seen_phones: set[str] = set()
    duplicates = 0

    for i, row in enumerate(raw_rows):
        phone_raw = _find_column(row, ["phone", "phone_number", "mobile", "cell", "telephone", "number"])
        if not phone_raw:
            invalid.append({"row": i + 2, "reason": "Missing phone number", "data": row})
            continue
        phone = _normalize_phone(phone_raw)
        if not _is_valid_e164(phone):
            invalid.append({"row": i + 2, "reason": f"Invalid phone number: {phone_raw}", "data": row})
            continue
        if phone in seen_phones:
            duplicates += 1
            continue
        seen_phones.add(phone)
        normalized = {re.sub(r"[^a-z0-9]+", "_", k.strip().lower()).strip("_"): (v or "").strip() for k, v in row.items()}
        valid.append({
            "phone_number": phone,
            "original_phone_number": phone_raw,
            "first_name": normalized.get("first_name") or normalized.get("firstname") or normalized.get("first"),
            "last_name": normalized.get("last_name") or normalized.get("lastname") or normalized.get("last"),
            "email": normalized.get("email") or normalized.get("email_address"),
            "customer_data": {k: v for k, v in normalized.items() if k not in {"phone", "mobile", "mobile_number", "phone_number", "contact", "contact_number", "cell", "telephone", "number"}},
        })

    return {
        "total_rows": len(raw_rows),
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "duplicate_count": duplicates,
        "valid": valid,
        "invalid_rows": invalid,
    }


class UploadPreview(BaseModel):
    total_rows: int
    valid_count: int
    invalid_count: int
    duplicate_count: int
    valid_sample: list[dict]
    invalid_rows: list[dict]
    import_token: str  # opaque base64 payload for confirm step


class ImportResult(BaseModel):
    imported: int
    skipped_duplicates: int


def _get_campaign(campaign_id: UUID, tenant_id: UUID, db: Session) -> Campaign:
    campaign = db.scalar(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id)
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.post("/{campaign_id}/contacts/upload-preview", response_model=UploadPreview)
async def upload_contacts_preview(
    campaign_id: UUID,
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadPreview:
    """Parse and validate an uploaded CSV/XLSX. Returns a preview without committing."""
    _get_campaign(campaign_id, current_user.tenant.id, db)

    content_type = (file.content_type or "").lower().split(";")[0].strip()
    filename = (file.filename or "").lower()
    is_xlsx = filename.endswith(".xlsx") or content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    is_xls = filename.endswith(".xls") or content_type == "application/vnd.ms-excel"
    is_csv = filename.endswith(".csv") or content_type in {"text/csv", "application/csv"}

    if not is_csv and not is_xlsx and not is_xls:
        raise HTTPException(status_code=422, detail="Only CSV, XLS, and XLSX files are supported.")

    raw_content = await file.read()
    if len(raw_content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 5 MB limit.")
    if len(raw_content) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    try:
        raw_rows = _parse_xlsx(raw_content) if is_xlsx else (_parse_xls(raw_content) if is_xls else _parse_csv(raw_content))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to parse file: {exc}") from exc

    if not raw_rows:
        raise HTTPException(status_code=422, detail="File contains no data rows.")

    result = _process_rows(raw_rows)

    # Encode valid rows as a simple JSON token for the confirm step
    token_data = {
        "token": _make_import_token(campaign_id, current_user.tenant.id, result["valid"]),
    }

    return UploadPreview(
        total_rows=result["total_rows"],
        valid_count=result["valid_count"],
        invalid_count=result["invalid_count"],
        duplicate_count=result["duplicate_count"],
        valid_sample=result["valid"][:10],
        invalid_rows=result["invalid_rows"][:20],
        import_token=token_data["token"],
    )


class ConfirmImport(BaseModel):
    import_token: str


@router.post("/{campaign_id}/contacts/upload-confirm", response_model=ImportResult, status_code=status.HTTP_201_CREATED)
def upload_contacts_confirm(
    campaign_id: UUID,
    payload: ConfirmImport,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportResult:
    """Commit a previously previewed upload. Validates token tenant/campaign match."""
    try:
        token_data = jwt.decode(payload.import_token, get_settings().auth_secret_key, algorithms=["HS256"])
        if token_data.get("purpose") != "campaign_import":
            raise ValueError("wrong token purpose")
        import json
        rows = json.loads(_upload_cipher().decrypt(token_data["rows"].encode()).decode())
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid import token.") from exc

    # Security: verify token matches authenticated tenant and campaign
    if str(token_data.get("tenant_id")) != str(current_user.tenant.id):
        raise HTTPException(status_code=403, detail="Import token does not match your account.")
    if str(token_data.get("campaign_id")) != str(campaign_id):
        raise HTTPException(status_code=422, detail="Import token does not match this campaign.")

    campaign = _get_campaign(campaign_id, current_user.tenant.id, db)

    rows: list[dict] = rows if isinstance(rows, list) else []
    if not rows:
        raise HTTPException(status_code=422, detail="No valid contacts in import token.")

    # Fetch existing phone numbers for this campaign to detect duplicates
    existing_phones = set(
        db.scalars(
            select(CampaignContact.normalized_phone).where(CampaignContact.campaign_id == campaign.id)
        ).all()
    )

    imported = 0
    skipped = 0
    for row in rows:
        phone = row.get("phone_number", "")
        if not phone or phone in existing_phones:
            skipped += 1
            continue
        contact = CampaignContact(
            tenant_id=current_user.tenant.id,
            campaign_id=campaign.id,
            phone_number=phone,
            normalized_phone=phone,
            first_name=row.get("first_name") or None,
            last_name=row.get("last_name") or None,
            email=row.get("email") or None,
            customer_data=row.get("customer_data") or {},
            status=ContactStatus.pending.value,
        )
        db.add(contact)
        existing_phones.add(phone)
        imported += 1

    db.commit()
    if imported == 0:
        raise HTTPException(status_code=409, detail="This import has already been applied or contains no new contacts.")
    return ImportResult(imported=imported, skipped_duplicates=skipped)
