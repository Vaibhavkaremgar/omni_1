from datetime import datetime, timezone

from app.models.campaign import Campaign
from app.services.campaign_execution import next_calling_window_time


def test_callback_after_calling_window_shifts_to_next_asia_kolkata_window():
    campaign = Campaign(
        tenant_id=None,
        name="window",
        employee_id=None,
        timezone="Asia/Kolkata",
        calling_window_start="09:00",
        calling_window_end="18:00",
    )
    requested = datetime(2026, 9, 19, 16, 30, tzinfo=timezone.utc)  # 22:00 IST
    effective = next_calling_window_time(campaign, requested)
    assert effective.astimezone(timezone.utc) == datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)


def test_callback_before_calling_window_shifts_to_same_day_start():
    campaign = Campaign(
        tenant_id=None,
        name="window",
        employee_id=None,
        timezone="Asia/Kolkata",
        calling_window_start="09:00",
        calling_window_end="18:00",
    )
    requested = datetime(2026, 9, 19, 1, 30, tzinfo=timezone.utc)  # 07:00 IST
    effective = next_calling_window_time(campaign, requested)
    assert effective.astimezone(timezone.utc) == datetime(2026, 9, 19, 3, 30, tzinfo=timezone.utc)


def test_callback_inside_calling_window_is_unchanged():
    campaign = Campaign(
        tenant_id=None,
        name="window",
        employee_id=None,
        timezone="Asia/Kolkata",
        calling_window_start="09:00",
        calling_window_end="18:00",
    )
    requested = datetime(2026, 9, 19, 7, 30, tzinfo=timezone.utc)  # 13:00 IST
    assert next_calling_window_time(campaign, requested) == requested
