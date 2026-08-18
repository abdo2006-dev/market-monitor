"""Shared Cairo market-cycle policy for daily catalog evidence.

The morning recovery has one business meaning across daily surfaces: until the
08:47 Africa/Cairo recovery opportunity, yesterday's complete catalog remains
current; afterwards today's complete catalog is required.  Search and Export
both consume this policy rather than maintaining their own clock vocabulary.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


CAIRO = ZoneInfo("Africa/Cairo")
MORNING_RECOVERY_CUTOFF = time(hour=8, minute=47)


def required_market_cycle_date(now: datetime) -> date:
    """Return the catalog date required by the current Cairo market cycle."""
    local_now = aware_utc(now).astimezone(CAIRO)
    if local_now.timetz().replace(tzinfo=None) < MORNING_RECOVERY_CUTOFF:
        return local_now.date() - timedelta(days=1)
    return local_now.date()


def aware_utc(value: datetime) -> datetime:
    """Normalize legacy-naive timestamps as UTC at the domain boundary."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
