from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.campaign_upload import _parse_csv, _parse_xlsx, _process_rows
from app.models.ai_employee import AIEmployee
from app.models.instant_lead_source import InstantLeadRow, InstantLeadSource
from app.models.lead import Lead
from app.models.phone_number import PhoneNumber
from app.models.tenant import Tenant
from app.services.instant_calls import InstantCallService
from app.schemas.call import InstantCallRequest
from app.integrations.omnidimension import OmniDimensionCallProvider, OmniDimensionClient
from app.core.config import get_settings
from app.models.integration_connection import IntegrationConnection
from app.services.integrations import IntegrationService
from app.integrations.google_sheets import GoogleSheetsAdapter, GoogleSheetsError

logger = logging.getLogger(__name__)


def row_fingerprint(row: dict[str, str]) -> str:
    phone = (row.get("phone_number") or "").strip().casefold()
    email = (row.get("email") or "").strip().casefold()
    identity = {"phone_number": phone, "email": email}
    if not phone or not email:
        identity.update({key: (row.get(key) or "").strip().casefold() for key in ("first_name", "last_name", "company")})
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _parse_source(source: InstantLeadSource, db: Session | None = None) -> list[dict[str, str]]:
    if source.source_type == "google_sheet":
        if db is None or not source.spreadsheet_id or not source.sheet_name:
            if source.content is None: raise GoogleSheetsError("Google Sheet source is missing spreadsheet and tab configuration.")
        else:
            conn = db.scalar(select(IntegrationConnection).where(IntegrationConnection.tenant_id == source.tenant_id, IntegrationConnection.integration_key == (source.integration_key or "google_sheets")))
            if conn is None: raise GoogleSheetsError("Google Sheets OAuth connection is missing.")
            return GoogleSheetsAdapter().read(conn, source.spreadsheet_id, source.sheet_name, IntegrationService())
    if source.content is None: return []
    raw = _parse_xlsx(source.content) if source.filename.lower().endswith(".xlsx") else _parse_csv(source.content)
    return _process_rows(raw)["valid"]


class InstantLeadService:
    _locks: dict[str, threading.Lock] = {}
    def __init__(self, call_service: InstantCallService | None = None):
        self.call_service = call_service or InstantCallService(OmniDimensionCallProvider(OmniDimensionClient(get_settings())))

    @staticmethod
    def _local_now(source: InstantLeadSource) -> datetime:
        tz_name = source.timezone or getattr(get_settings(), "timezone", None) or "UTC"
        try: tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError: tz = timezone.utc
        return datetime.now(tz)

    @classmethod
    def _within_hours(cls, source: InstantLeadSource) -> bool:
        hours = source.working_hours or {}
        start, end = hours.get("start"), hours.get("end")
        if not start or not end: return True
        now = cls._local_now(source).time()
        try:
            from datetime import time
            begin = time.fromisoformat(start); finish = time.fromisoformat(end)
        except ValueError: return False
        return begin <= now <= finish if begin <= finish else now >= begin or now <= finish

    @classmethod
    def _daily_calls(cls, db: Session, source: InstantLeadSource) -> int:
        from app.models.call import Call
        local = cls._local_now(source)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        end = start + timedelta(days=1)
        calls = db.scalars(select(Call).where(Call.tenant_id == source.tenant_id, Call.created_at >= start, Call.created_at < end)).all()
        return sum(1 for call in calls if (call.dispatch_metadata or {}).get("instant_source_id") == str(source.id))

    def check(self, db: Session, source_id: UUID, tenant_id: UUID) -> dict[str, Any]:
        source = db.scalar(select(InstantLeadSource).where(InstantLeadSource.id == source_id, InstantLeadSource.tenant_id == tenant_id))
        if source is None:
            raise ValueError("Instant Leads source not found.")
        now = datetime.now(timezone.utc)
        source.last_checked_at = now
        tenant = db.get(Tenant, tenant_id)
        if tenant is None or not tenant.instant_leads_enabled:
            source.last_status = "disabled by tenant Instant Leads setting"
            db.commit()
            return self.status(source, 0)
        if not source.enabled:
            source.last_status = "disabled"
            db.commit()
            return self.status(source, 0)
        lock = self._locks.setdefault(str(source.id), threading.Lock())
        if not lock.acquire(blocking=False):
            return self.status(source, result={"records_checked": 0, "new_leads": 0, "calls_queued": 0, "skipped_duplicates": 0, "failures": ["sync already running"]})
        rows = _parse_source(source, db)
        imported = checked = calls_queued = skipped = 0
        failures: list[str] = []
        try:
          for index, row in enumerate(rows, start=2):
            checked += 1
            fingerprint = row_fingerprint(row)
            existing = db.scalar(select(InstantLeadRow).where(InstantLeadRow.source_id == source.id, InstantLeadRow.fingerprint == fingerprint))
            if existing is not None and existing.processed:
                skipped += 1
                continue
            if existing is None:
                existing = InstantLeadRow(source_id=source.id, tenant_id=tenant_id, fingerprint=fingerprint, row_number=index, processed=False, external_record_id=row.get("external_id"))
                db.add(existing); db.flush()
            if existing.lead_id is None:
                phone = re.sub(r"[\s().-]", "", row.get("phone_number", ""))
                lead = Lead(tenant_id=tenant_id, first_name=row.get("first_name") or None, last_name=row.get("last_name") or None,
                            email=row.get("email") or None, phone_number=phone or None, source=f"instant_leads:{source.source_type}", profile_data=row)
                db.add(lead); db.flush(); existing.lead_id = lead.id; imported += 1
            if not re.fullmatch(r"\+[1-9]\d{6,14}", re.sub(r"[\s().-]", "", row.get("phone_number", ""))):
                existing.status = "skipped"; existing.processed = True; failures.append("missing or invalid phone number"); db.commit(); continue
            if not source.auto_call:
                existing.status = "new"; existing.processed = True; continue
            if not self._within_hours(source):
                existing.status = "deferred_hours"; existing.processed = False; failures.append("deferred outside working hours"); db.commit(); continue
            if source.daily_call_limit is not None and self._daily_calls(db, source) >= source.daily_call_limit:
                existing.status = "deferred_limit"; existing.processed = False; failures.append("deferred because daily call limit was reached"); db.commit(); continue
            request = InstantCallRequest(employee_id=source.employee_id, phone_number_id=source.phone_number_id,
                destination_phone_number=re.sub(r"[\s().-]", "", row["phone_number"]), customer_name=" ".join(x for x in (row.get("first_name"), row.get("last_name")) if x) or row.get("name") or None,
                lead_id=existing.lead_id)
            try:
                call = self.call_service.dispatch(db, tenant_id, request)
                if hasattr(call, "dispatch_metadata"):
                    call.dispatch_metadata = {**(call.dispatch_metadata or {}), "instant_source_id": str(source.id)}
                db.commit()
                existing.status = "queued"; existing.processed = True; calls_queued += 1
            except Exception as exc:
                existing.status = "failed"; failures.append(str(exc)[:200])
            db.commit()
        finally:
          lock.release()
        source.last_success_at = now
        source.last_imported_count = imported
        source.last_result = {"records_checked": checked, "new_leads": imported, "calls_queued": calls_queued, "skipped_duplicates": skipped, "failures": failures}
        source.last_status = f"checked successfully; {imported} new lead(s), {calls_queued} call(s) queued"
        db.commit()
        db.refresh(source)
        return self.status(source, imported, source.last_result)

    @staticmethod
    def status(source: InstantLeadSource, imported: int = 0, result: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"id": str(source.id), "filename": source.filename, "enabled": source.enabled,
                "last_checked_at": source.last_checked_at, "last_success_at": source.last_success_at,
                "last_status": source.last_status, "new_rows": imported, "last_result": result or source.last_result,
                "source_type": source.source_type, "name": source.name, "frequency_minutes": source.frequency_minutes,
                "auto_call": source.auto_call, "daily_call_limit": source.daily_call_limit, "working_hours": source.working_hours, "timezone": source.timezone}


class InstantLeadMonitor:
    def __init__(self, session_factory, interval_seconds: int = 60):
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="instant-leads-monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.wait(self.interval_seconds):
            db = self.session_factory()
            try:
                sources = db.scalars(select(InstantLeadSource).where(InstantLeadSource.enabled.is_(True))).all()
                for source in sources:
                    try:
                        if source.last_checked_at and datetime.now(timezone.utc) < source.last_checked_at + timedelta(minutes=source.frequency_minutes or 5):
                            continue
                        InstantLeadService().check(db, source.id, source.tenant_id)
                    except Exception:
                        db.rollback()
                        logger.exception("Instant Leads monitoring cycle failed source=%s", source.id)
            finally:
                db.close()
