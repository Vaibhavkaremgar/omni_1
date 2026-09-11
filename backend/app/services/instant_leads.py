from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
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

logger = logging.getLogger(__name__)


def row_fingerprint(row: dict[str, str]) -> str:
    identity = {key: (row.get(key) or "").strip().casefold() for key in ("phone_number", "first_name", "last_name", "email")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _parse_source(source: InstantLeadSource) -> list[dict[str, str]]:
    raw = _parse_xlsx(source.content) if source.filename.lower().endswith(".xlsx") else _parse_csv(source.content)
    return _process_rows(raw)["valid"]


class InstantLeadService:
    def __init__(self, call_service: InstantCallService | None = None):
        self.call_service = call_service or InstantCallService(OmniDimensionCallProvider(OmniDimensionClient(get_settings())))

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
        rows = _parse_source(source)
        imported = 0
        for index, row in enumerate(rows, start=2):
            fingerprint = row_fingerprint(row)
            existing = db.scalar(select(InstantLeadRow).where(InstantLeadRow.source_id == source.id, InstantLeadRow.fingerprint == fingerprint))
            if existing is not None and existing.processed:
                continue
            if existing is None:
                existing = InstantLeadRow(source_id=source.id, tenant_id=tenant_id, fingerprint=fingerprint, row_number=index, processed=False)
                db.add(existing)
                db.flush()
            if existing.lead_id is None:
                lead = Lead(tenant_id=tenant_id, first_name=row.get("first_name") or None, last_name=row.get("last_name") or None,
                            email=row.get("email") or None, phone_number=row["phone_number"], source="instant_leads")
                db.add(lead)
                db.flush()
                existing.lead_id = lead.id
            request = InstantCallRequest(employee_id=source.employee_id, phone_number_id=source.phone_number_id,
                destination_phone_number=row["phone_number"], customer_name=" ".join(x for x in (row.get("first_name"), row.get("last_name")) if x) or None,
                lead_id=existing.lead_id)
            self.call_service.dispatch(db, tenant_id, request)
            existing.processed = True
            imported += 1
            db.commit()
        source.last_success_at = now
        source.last_imported_count = imported
        source.last_status = f"checked successfully; {imported} new row(s) processed"
        db.commit()
        db.refresh(source)
        return self.status(source, imported)

    @staticmethod
    def status(source: InstantLeadSource, imported: int = 0) -> dict[str, Any]:
        return {"id": str(source.id), "filename": source.filename, "enabled": source.enabled,
                "last_checked_at": source.last_checked_at, "last_success_at": source.last_success_at,
                "last_status": source.last_status, "new_rows": imported}


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
                        InstantLeadService().check(db, source.id, source.tenant_id)
                    except Exception:
                        db.rollback()
                        logger.exception("Instant Leads monitoring cycle failed source=%s", source.id)
            finally:
                db.close()
