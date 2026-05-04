"""Unit tests for the Multilogin client wrapper."""
import pytest

from pyquotex.utils.multilogin import (
    DEFAULT_CLOUD_URL,
    DEFAULT_LOCAL_URL,
    MultiloginClient,
    MultiloginConfig,
    MultiloginProfile,
)


def test_resolved_base_url_v1_default():
    cfg = MultiloginConfig(profile_id="x", api="v1")
    assert cfg.resolved_base_url() == DEFAULT_LOCAL_URL


def test_resolved_base_url_v3_default():
    cfg = MultiloginConfig(profile_id="x", api="v3")
    assert cfg.resolved_base_url() == DEFAULT_CLOUD_URL


def test_resolved_base_url_override_strips_trailing_slash():
    cfg = MultiloginConfig(profile_id="x", api="v3", base_url="https://my.host/")
    assert cfg.resolved_base_url() == "https://my.host"


def test_extract_proxy_from_full_dict():
    proxy = MultiloginClient._extract_proxy({
        "proxy": {
            "host": "p.com",
            "port": 8080,
            "type": "socks5",
            "user": "u",
            "pass": "p",
        }
    })
    assert proxy == "socks5://u:p@p.com:8080"


def test_extract_proxy_from_address_only():
    proxy = MultiloginClient._extract_proxy({
        "proxy": {"address": "q.com", "port": 3128}
    })
    assert proxy == "http://q.com:3128"


def test_extract_proxy_from_raw_string():
    proxy = MultiloginClient._extract_proxy({
        "proxy": "http://direct.example:8888"
    })
    assert proxy == "http://direct.example:8888"


def test_extract_proxy_missing_returns_none():
    assert MultiloginClient._extract_proxy({}) is None


def test_extract_proxy_partial_missing_port_returns_none():
    assert MultiloginClient._extract_proxy({"proxy": {"host": "p.com"}}) is None


def test_extract_proxy_dict_no_credentials_drops_userinfo():
    proxy = MultiloginClient._extract_proxy({
        "proxy": {"host": "p.com", "port": 1080, "type": "http"}
    })
    assert proxy == "http://p.com:1080"


def test_to_proxy_config_returns_proxy_config():
    profile = MultiloginProfile(
        profile_id="x", proxy_url="http://p:1", user_agent="UA"
    )
    pc = profile.to_proxy_config()
    assert pc is not None
    assert pc.url == "http://p:1"


def test_to_proxy_config_returns_none_when_no_proxy():
    profile = MultiloginProfile(profile_id="x")
    assert profile.to_proxy_config() is None


@pytest.mark.asyncio
async def test_v3_start_profile_requires_folder_id():
    cfg = MultiloginConfig(profile_id="abc", api="v3")
    client = MultiloginClient(cfg)
    try:
        with pytest.raises(ValueError):
            await client.start_profile()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_constructor_accepts_proxy_kwarg():
    cfg = MultiloginConfig(profile_id="abc", api="v1")
    client = MultiloginClient(cfg, proxy="http://corp-proxy:8080")
    try:
        # Just verify construction doesn't blow up; the proxy is honoured
        # by httpx when the request is actually made.
        assert client._client is not None
    finally:
        await client.close()
