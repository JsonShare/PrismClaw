from __future__ import annotations

from web_search_mcp.infra.security import check_url_safety, is_internal_host, upgrade_scheme
from web_search_mcp.infra.urlutil import canonicalize_url


def test_internal_hosts_blocked() -> None:
    assert is_internal_host("127.0.0.1")
    assert is_internal_host("10.1.2.3")
    assert is_internal_host("192.168.0.1")
    assert is_internal_host("169.254.169.254")
    assert is_internal_host("::1")
    assert is_internal_host("localhost")
    assert not is_internal_host("example.com")


def test_check_url_safety_basic() -> None:
    assert (
        check_url_safety("https://example.com/p", allowed_schemes=["https"], allowed_domains=[])
        is None
    )
    assert check_url_safety("http://example.com", allowed_schemes=["https"], allowed_domains=[])
    assert check_url_safety("https://10.0.0.5/x", allowed_schemes=["https"], allowed_domains=[])
    assert check_url_safety(
        "https://paypal-verify.example.com",
        allowed_schemes=["https"],
        allowed_domains=[],
    )
    assert check_url_safety("ftp://example.com", allowed_schemes=["https"], allowed_domains=[])


def test_domain_allowlist() -> None:
    assert (
        check_url_safety(
            "https://docs.internal.corp/x",
            allowed_schemes=["https"],
            allowed_domains=["internal.corp"],
        )
        is None
    )
    assert check_url_safety(
        "https://evil.example.org",
        allowed_schemes=["https"],
        allowed_domains=["internal.corp"],
    )


def test_internal_host_allowed_when_listed() -> None:
    assert (
        check_url_safety(
            "https://intranet.internal:8080/search",
            allowed_schemes=["https"],
            allowed_domains=["intranet.internal"],
        )
        is None
    )


def test_upgrade_scheme() -> None:
    assert upgrade_scheme("http://example.com", allowed_schemes=["https"]).startswith("https://")
    assert upgrade_scheme("http://example.com", allowed_schemes=["http"]) == "http://example.com"


def test_canonicalize_strips_tracking() -> None:
    url = "https://example.com/a?utm_source=x&id=2&utm_medium=feed"
    assert canonicalize_url(url) == "https://example.com/a?id=2"
    assert canonicalize_url("not-a-url") == "not-a-url"
