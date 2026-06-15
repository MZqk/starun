import hashlib
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import DailyUsage


def hash_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_date(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def get_daily_usage(
    session: Session,
    settings: Settings,
    client_id: str,
    request_ip: str,
    *,
    now: datetime | None = None,
) -> tuple[date, int, int]:
    usage_date = utc_date(now)
    used = session.scalar(
        select(DailyUsage.count).where(
            DailyUsage.date == usage_date,
            DailyUsage.client_id_hash == hash_identity(client_id),
            DailyUsage.ip_hash == hash_identity(request_ip),
        )
    )
    ip_used = session.scalar(
        select(func.coalesce(func.sum(DailyUsage.count), 0)).where(
            DailyUsage.date == usage_date,
            DailyUsage.ip_hash == hash_identity(request_ip),
        )
    )
    count = max(used or 0, ip_used or 0)
    return usage_date, count, max(settings.daily_task_limit - count, 0)
