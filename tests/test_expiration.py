import pytest
from datetime import datetime, timezone
import time
from pyquotex.expiration import (
    timestamp_to_date,
    get_timestamp_days_ago
)


def test_timestamp_to_date():
    """Test conversion of timestamp to UTC date str."""
    # Create an arbitrary UTC timestamp
    dt = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    ts = dt.timestamp()
    
    formatted = timestamp_to_date(ts)
    assert isinstance(formatted, datetime)
    assert formatted.year == 2030


def test_get_timestamp_days_ago():
    """Test getting timestamp N days in the past."""
    now = time.time()
    days_ago = get_timestamp_days_ago(5)
    
    # Difference should be ~5 days (5 * 24 * 3600)
    expected_diff = 5 * 24 * 3600
    actual_diff = now - days_ago
    
    # Allow for small execution delays
    assert abs(actual_diff - expected_diff) < 2
