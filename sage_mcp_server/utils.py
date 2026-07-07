import re
from datetime import datetime, timedelta, timezone
from typing import Tuple, Union

import pandas as pd

from .models import TimeRange


def safe_timestamp_format(timestamp) -> str:
    """Safely format a timestamp to an ISO8601 string."""
    try:
        if timestamp is None:
            return "N/A"
        if pd.isna(timestamp):
            return "N/A"
        if isinstance(timestamp, (pd.Timestamp, datetime)):
            return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        return str(timestamp)
    except Exception:
        return str(timestamp)


def _utcnow() -> datetime:
    """Timezone-aware UTC now (datetime.utcnow is deprecated in 3.12+)."""
    return datetime.now(timezone.utc)


def parse_time_range(time_range: Union[str, TimeRange]) -> Tuple[str, str]:
    """Return (start, end) as ISO8601 strings.

    If ``time_range`` is an ISO timestamp, use it as start and add 1h for end.
    If ``time_range`` is a relative shorthand like ``-5m``, ``-1h``, ``-2d``,
    convert to explicit start/end.
    """
    if hasattr(time_range, "value"):
        time_range = str(time_range)

    time_range = time_range or "-30m"

    if "T" in time_range and "Z" in time_range:
        try:
            start_time = datetime.strptime(time_range, "%Y-%m-%dT%H:%M:%SZ")
            end_time = start_time + timedelta(hours=1)
            return (
                start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        except Exception:
            return time_range, ""

    match = re.match(r"-(\d+)([smhd])", time_range)
    now = _utcnow().replace(tzinfo=None)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "s":
            delta = timedelta(seconds=amount)
        elif unit == "m":
            delta = timedelta(minutes=amount)
        elif unit == "h":
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(days=amount)
        start = (now - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        return start, end

    return time_range, ""
