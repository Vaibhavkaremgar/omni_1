from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base, utc_now
from app.models import AIEmployee, AIEmployeeVersion, Call, CreditWallet, PhoneNumber, Tenant
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.campaign_execution_slot import CampaignExecutionSlot
from app.models.enums import CallStatus, CampaignStatus, ContactStatus, EmployeeStatus, NumberStatus
from app.services.campaign_execution import (
    CampaignExecutionService,
    poll_campaign_active_calls,
    recover_stale_contacts,
    recover_stale_slots,
)
from app.services.campaign_scheduler import CampaignScheduler
from app.integrations.omnidimension.exceptions import OmniDimensionNetworkError


@pytest.fixture()
def polling_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant = Tenant(name="Polling", slug="polling")
    db.add(tenant)
    db.flush()
    db.add(CreditWallet(tenant_id=tenant.id, balance_credits=500, currency="INR", status="active"))
    employee = AIEmployee(
        tenant_id=tenant.id, name="Agent", purpose="Test", call_type="outbound",
        llm_provider="OpenAI", llm_model="test", language="English",
        creation_mode="chat", status=EmployeeStatus.published.value,
    )
    db.add(employee)
    db.flush()
    version = AIEmployeeVersion(
        tenant_id=tenant.id, employee_id=employee.id, version_number=1,
        status="published", configuration={}, provider_name="omnidimension",
        provider_agent_id="17",
    )
    db.add(version)
    db.flush()
    employee.published_version = version
    phone = PhoneNumber(
        tenant_id=tenant.id, e164_number="+15550001111", provider_name="omnidimension",
        provider_phone_number_id="23", status=NumberStatus.active.value,
    )
    db.add(phone)
    db.flush()
    campaign = Campaign(
        tenant_id=tenant.id, name="Sequential", employee_id=employee.id,
        phone_number_id=phone.id, status=CampaignStatus.running.value,
        concurrency=1, max_attempts=1, retry_enabled=False,
    )
    db.add(campaign)
    db.flush()
    contacts = [
        CampaignContact(
            tenant_id=tenant.id, campaign_id=campaign.id,
            phone_number=f"+1555000111{index}", normalized_phone=f"+1555000111{index}",
            status=ContactStatus.pending.value,
        )
        for index in (2, 3)
    ]
    db.add_all(contacts)
    db.commit()
    try:
        yield db, tenant, campaign, contacts
    finally:
        db.close()


class FakeOmniProvider:
    def __init__(self, statuses: list[str] | None = None, *, fail_poll: bool = False):
        self.statuses = list(statuses or [])
        self.fail_poll = fail_poll
        self.dispatches: list[str] = []
        self.live_checks = 0
        self.line_checks = 0

    def dispatch(self, *, to_number, **_kwargs):
        self.dispatches.append(to_number)
        number = len(self.dispatches)
        return SimpleNamespace(
            provider_call_id=None,
            provider_request_id=f"bulk-{number}",
            provider_bulk_call_id=f"bulk-{number}",
            provider_line_id=None,
            status="queued",
        )

    def get_bulk_call_live_status(self, bulk_call_id):
        self.live_checks += 1
        if self.fail_poll:
            raise OmniDimensionNetworkError("offline")
        return {"campaign_status": "in_progress", "bulk_call_id": bulk_call_id}

    def get_bulk_call_lines(self, bulk_call_id):
        self.line_checks += 1
        status = self.statuses.pop(0) if self.statuses else "in_progress"
        return [{
            "line_id": f"line-{bulk_call_id}",
            "call_id": f"call-{bulk_call_id}",
            "call_status": status,
        }]


def install_provider(monkeypatch, provider: FakeOmniProvider):
    import app.services.campaign_execution as execution

    monkeypatch.setattr(execution, "OmniDimensionClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(execution, "OmniDimensionCallProvider", lambda _client: provider)


def active_slots(db, campaign):
    return db.scalars(select(CampaignExecutionSlot).where(
        CampaignExecutionSlot.campaign_id == campaign.id,
        CampaignExecutionSlot.released_at.is_(None),
    )).all()


def test_start_poll_complete_then_start_next_contact(polling_db, monkeypatch):
    db, tenant, campaign, contacts = polling_db
    provider = FakeOmniProvider(["in_progress", "completed"])
    install_provider(monkeypatch, provider)
    service = CampaignExecutionService()

    first_call = service.dispatch_next_pending(db, campaign.id, tenant.id)
    assert first_call is not None
    assert contacts[0].status == ContactStatus.in_progress.value
    assert contacts[0].provider_bulk_call_id == "bulk-1"
    assert len(active_slots(db, campaign)) == 1

    assert poll_campaign_active_calls(db, campaign) == 0
    assert service.dispatch_next_pending(db, campaign.id, tenant.id) is None
    assert len(provider.dispatches) == 1

    contacts[0].status_checked_at = None
    db.commit()
    assert poll_campaign_active_calls(db, campaign) == 1
    second_call = service.dispatch_next_pending(db, campaign.id, tenant.id)
    assert second_call is not None
    assert len(provider.dispatches) == 2
    assert contacts[0].status == ContactStatus.completed.value
    assert contacts[1].status == ContactStatus.in_progress.value
    assert len(active_slots(db, campaign)) == 1


@pytest.mark.parametrize(
    ("provider_status", "contact_status"),
    [
        ("failed", ContactStatus.failed.value),
        ("busy", ContactStatus.busy.value),
        ("no-answer", ContactStatus.no_answer.value),
    ],
)
def test_terminal_failure_status_releases_slot_and_next_contact_starts(
    polling_db, monkeypatch, provider_status, contact_status,
):
    db, tenant, campaign, contacts = polling_db
    provider = FakeOmniProvider([provider_status])
    install_provider(monkeypatch, provider)
    service = CampaignExecutionService()
    service.dispatch_next_pending(db, campaign.id, tenant.id)

    assert poll_campaign_active_calls(db, campaign) == 1
    assert contacts[0].status == contact_status
    assert service.dispatch_next_pending(db, campaign.id, tenant.id) is not None
    assert len(provider.dispatches) == 2


def test_timeout_releases_slot_and_marks_contact(polling_db, monkeypatch):
    db, tenant, campaign, contacts = polling_db
    provider = FakeOmniProvider()
    install_provider(monkeypatch, provider)
    import app.services.campaign_execution as execution
    monkeypatch.setattr(execution, "get_settings", lambda: SimpleNamespace(
        minimum_call_balance_inr=0,
        omni_call_status_poll_interval_seconds=1,
        omni_call_status_max_wait_seconds=30,
    ))
    service = CampaignExecutionService()
    call = service.dispatch_next_pending(db, campaign.id, tenant.id)
    contacts[0].polling_started_at = utc_now() - timedelta(seconds=31)
    db.commit()

    assert poll_campaign_active_calls(db, campaign) == 1
    assert contacts[0].status == ContactStatus.timed_out.value
    assert call.status == CallStatus.timed_out.value
    assert not active_slots(db, campaign)


@pytest.mark.parametrize("mode", ["provider_error", "unknown_status", "missing_line"])
def test_uncertain_status_keeps_slot_and_blocks_next(polling_db, monkeypatch, mode):
    db, tenant, campaign, contacts = polling_db
    provider = FakeOmniProvider(["mystery"], fail_poll=mode == "provider_error")
    if mode == "missing_line":
        provider.get_bulk_call_lines = lambda _bulk_call_id: []
    install_provider(monkeypatch, provider)
    service = CampaignExecutionService()
    service.dispatch_next_pending(db, campaign.id, tenant.id)

    assert poll_campaign_active_calls(db, campaign) == 0
    assert contacts[0].status == ContactStatus.in_progress.value
    assert len(active_slots(db, campaign)) == 1
    assert service.dispatch_next_pending(db, campaign.id, tenant.id) is None
    assert len(provider.dispatches) == 1


def test_worker_restart_polls_existing_active_call_without_redispatch(polling_db, monkeypatch):
    db, tenant, campaign, contacts = polling_db
    provider = FakeOmniProvider(["completed"])
    install_provider(monkeypatch, provider)
    CampaignExecutionService().dispatch_next_pending(db, campaign.id, tenant.id)
    assert len(provider.dispatches) == 1

    # A new service/worker uses only durable database state to resume polling.
    assert poll_campaign_active_calls(db, campaign) == 1
    assert contacts[0].status == ContactStatus.completed.value
    assert len(provider.dispatches) == 1


def test_duplicate_workers_never_create_two_active_calls(polling_db, monkeypatch):
    db, tenant, campaign, _contacts = polling_db
    provider = FakeOmniProvider()
    install_provider(monkeypatch, provider)
    worker_a = CampaignExecutionService()
    worker_b = CampaignExecutionService()

    first = worker_a.dispatch_next_pending(db, campaign.id, tenant.id)
    second = worker_b.dispatch_next_pending(db, campaign.id, tenant.id)
    assert first is not None
    assert second is None
    assert len(provider.dispatches) == 1
    assert len(active_slots(db, campaign)) == 1


def test_scheduler_poll_completion_dispatches_next_in_same_tick(polling_db, monkeypatch):
    db, tenant, campaign, contacts = polling_db
    provider = FakeOmniProvider(["completed"])
    install_provider(monkeypatch, provider)
    CampaignExecutionService().dispatch_next_pending(db, campaign.id, tenant.id)

    scheduler = CampaignScheduler(interval_seconds=1)
    assert scheduler.run_once(db) == 1
    assert contacts[0].status == ContactStatus.completed.value
    assert contacts[1].status == ContactStatus.in_progress.value
    assert len(provider.dispatches) == 2


def test_cancelled_campaign_finishes_active_call_but_does_not_dispatch_next(polling_db, monkeypatch):
    db, tenant, campaign, contacts = polling_db
    provider = FakeOmniProvider(["completed"])
    install_provider(monkeypatch, provider)
    CampaignExecutionService().dispatch_next_pending(db, campaign.id, tenant.id)
    campaign.status = CampaignStatus.cancelled.value
    db.commit()

    scheduler = CampaignScheduler(interval_seconds=1)
    assert scheduler.run_once(db) == 0
    assert contacts[0].status == ContactStatus.completed.value
    assert contacts[1].status == ContactStatus.pending.value
    assert len(provider.dispatches) == 1


def test_campaign_completes_only_after_last_contact_is_terminal(polling_db, monkeypatch):
    db, tenant, campaign, contacts = polling_db
    db.delete(contacts[1])
    db.commit()
    provider = FakeOmniProvider(["completed"])
    install_provider(monkeypatch, provider)
    CampaignExecutionService().dispatch_next_pending(db, campaign.id, tenant.id)

    assert campaign.status == CampaignStatus.running.value
    assert poll_campaign_active_calls(db, campaign) == 1
    assert campaign.status == CampaignStatus.completed.value


def test_recovery_never_requeues_or_releases_an_unresolved_active_call(polling_db, monkeypatch):
    db, tenant, campaign, contacts = polling_db
    provider = FakeOmniProvider()
    install_provider(monkeypatch, provider)
    CampaignExecutionService().dispatch_next_pending(db, campaign.id, tenant.id)
    contacts[0].claimed_at = utc_now() - timedelta(hours=1)
    slot = active_slots(db, campaign)[0]
    slot.reserved_at = utc_now() - timedelta(hours=1)
    db.commit()

    assert recover_stale_contacts(db, lease_seconds=1) == 0
    assert recover_stale_slots(db, lease_seconds=1) == 0
    assert contacts[0].status == ContactStatus.in_progress.value
    assert len(active_slots(db, campaign)) == 1
