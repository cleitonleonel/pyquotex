"""Unit tests for ProxyConfig."""
from pyquotex.utils.proxy_config import ProxyConfig


def test_resolve_url_hits_dns_override():
    cfg = ProxyConfig(dns_overrides={"qxbroker.com": "1.2.3.4"})
    url, headers = cfg.resolve_url("https://qxbroker.com/api/v1/test")
    assert url == "https://1.2.3.4/api/v1/test"
    assert headers == {"Host": "qxbroker.com"}


def test_resolve_url_misses_unknown_host():
    cfg = ProxyConfig(dns_overrides={"qxbroker.com": "1.2.3.4"})
    url, headers = cfg.resolve_url("https://other.example/path")
    assert url == "https://other.example/path"
    assert headers == {}


def test_resolve_url_preserves_port():
    cfg = ProxyConfig(dns_overrides={"host.com": "9.9.9.9"})
    url, _ = cfg.resolve_url("https://host.com:8443/api")
    assert url == "https://9.9.9.9:8443/api"


def test_resolve_url_preserves_userinfo():
    cfg = ProxyConfig(dns_overrides={"host.com": "9.9.9.9"})
    url, headers = cfg.resolve_url("https://u:p@host.com/api")
    assert url == "https://u:p@9.9.9.9/api"
    assert headers["Host"] == "host.com"


def test_resolve_url_no_overrides_passthrough():
    cfg = ProxyConfig(url="http://proxy:8080")
    url, headers = cfg.resolve_url("https://anything.com/x")
    assert url == "https://anything.com/x"
    assert headers == {}


def test_scheme_and_is_socks():
    assert ProxyConfig().scheme is None
    assert ProxyConfig().is_socks is False
    assert ProxyConfig(url="http://p:1").scheme == "http"
    assert ProxyConfig(url="socks5://p:1").is_socks is True
    assert ProxyConfig(url="socks5h://p:1").is_socks is True


def test_from_dict_none_returns_none():
    assert ProxyConfig.from_dict(None) is None


def test_from_dict_empty_returns_none():
    # Empty dict is falsy; documented behaviour is "no usable config".
    assert ProxyConfig.from_dict({}) is None


def test_from_dict_accepts_proxy_alias():
    cfg = ProxyConfig.from_dict({"proxy": "http://x:1"})
    assert cfg is not None
    assert cfg.url == "http://x:1"


def test_from_dict_full():
    cfg = ProxyConfig.from_dict({
        "url": "socks5://p:1",
        "verify_ssl": False,
        "dns_overrides": {"a.com": "1.1.1.1"},
        "extra_headers": {"X-Test": "1"},
    })
    assert cfg is not None
    assert cfg.is_socks
    assert cfg.verify_ssl is False
    assert cfg.dns_overrides == {"a.com": "1.1.1.1"}
    assert cfg.extra_headers == {"X-Test": "1"}


def test_merge_headers_combines_extras():
    cfg = ProxyConfig(extra_headers={"X-A": "1"})
    merged = cfg.merge_headers({"X-B": "2"})
    assert merged == {"X-A": "1", "X-B": "2"}


def test_merge_headers_request_overrides_extras():
    cfg = ProxyConfig(extra_headers={"X-A": "1"})
    merged = cfg.merge_headers({"X-A": "override"})
    assert merged["X-A"] == "override"
