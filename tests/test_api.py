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
    """Test that the QuotexAPI instance initializes the state properly."""
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


def test_connection_state_enums():
    """Test that Enums are correctly initialized and legacy properties work."""
    from pyquotex.global_value import WebsocketStatus, AuthStatus
    state = ConnectionState()

    assert state.status == WebsocketStatus.DISCONNECTED
    assert state.auth_status == AuthStatus.NOT_AUTHENTICATED

    # Check legacy compatibility
    assert state.check_websocket_if_connect == 0
    assert state.check_accepted_connection == False
    assert state.check_rejected_connection == False

    # Update state and verify
    state.status = WebsocketStatus.CONNECTED
    assert state.check_websocket_if_connect == 1

    state.auth_status = AuthStatus.AUTHENTICATED
    assert state.check_accepted_connection == True

    state.auth_status = AuthStatus.FAILED
    assert state.check_rejected_connection == True
