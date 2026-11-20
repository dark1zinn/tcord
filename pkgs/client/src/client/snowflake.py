"""Discord snowflake helpers.

A snowflake is a 64-bit id whose upper 42 bits are milliseconds since the Discord epoch.
"""

from datetime import UTC, datetime

DISCORD_EPOCH_MS = 1_420_070_400_000
_MAX_DIGITS = 20


def is_snowflake(value: str) -> bool:
    return 0 < len(value) <= _MAX_DIGITS and value.isdigit()


def snowflake_time(snowflake: str) -> datetime:
    """Creation time encoded in ``snowflake`` (UTC)."""
    milliseconds = (int(snowflake) >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def time_snowflake(moment: datetime) -> str:
    """Smallest snowflake created at ``moment``; useful as a ``before``/``after`` cursor."""
    milliseconds = int(moment.timestamp() * 1000) - DISCORD_EPOCH_MS
    return str(max(milliseconds, 0) << 22)
