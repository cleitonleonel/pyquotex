import pytest
from pyquotex.api import QuotexAPI
from pyquotex.global_value import ConnectionState


def test_connection_state_independent():
    """Test that two ConnectionState instances are independent."""
    state1 = ConnectionState()
    state2 = ConnectionState()
    
    state1.SSID = "token1"
    state2.SSID = "token2"
    
    assert state1.SSID == "token1"
    assert state2.SSID == "token2"
    assert state1.SSID != state2.SSID


def test_quotex_api_instantiation():
    """Test that QuotexAPI instance initializes state properly."""
    api = QuotexAPI(
        "qxbroker.com", "test@test.com", "password", None, "en", 1
    )
    
    # Assert connection state exists
    assert hasattr(api, 'state')
    assert isinstance(api.state, ConnectionState)


def test_multiple_quotex_instances():
    """Test that multiple instances don't share underlying state."""
    api1 = QuotexAPI("host1", "test1@test.com", "pwd1", None, "en", 1)
    api2 = QuotexAPI("host2", "test2@test.com", "pwd2", None, "en", 1)
    
    api1.state.SSID = "token1"
    api2.state.SSID = "token2"
    
    assert api1.state.SSID == "token1"
    assert api2.state.SSID == "token2"
    assert api1.state.SSID != api2.state.SSID
