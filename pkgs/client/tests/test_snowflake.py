from datetime import UTC, datetime

import pytest
from client.snowflake import is_snowflake, snowflake_time, time_snowflake


def test_known_snowflake_time() -> None:
    # Discord's documented example id.
    assert snowflake_time("175928847299117063") == datetime(
        2016, 4, 30, 11, 18, 25, 796000, tzinfo=UTC
    )


def test_round_trip_cursor() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    assert snowflake_time(time_snowflake(moment)) == moment


def test_time_before_epoch_clamps_to_zero() -> None:
    assert time_snowflake(datetime(2000, 1, 1, tzinfo=UTC)) == "0"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("175928847299117063", True), ("", False), ("12a", False), ("1" * 21, False)],
)
def test_is_snowflake(value: str, expected: bool) -> None:
    assert is_snowflake(value) is expected
