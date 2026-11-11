"""SSRF / domain safety checks, ported from the former web_fetch builtin tool.

The service is the network egress point now, so these guards live here and
run both before fetching and after each redirect hop.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_SUSPICIOUS_DOMAIN_PATTERNS = (
    "tempmail.com",
    "guerrillamail.com",
    "mailinator.com",
    "throwaway.email",
    "login-",
    "-login.",
    "secure-",
    "-secure.",
    "verify-",
    "-verify.",
    "account-",
    "-account.",
    "paypal-",
    "-paypal.",
    "apple-",
    "-apple.",
    "google-",
    "-google.",
    "amazon-",
    "-amazon.",
    "microsoft-",
    "-microsoft.",
)

_KNOWN_SAFE_DOMAINS = {
    "github.com",
    "gitlab.com",
    "stackoverflow.com",
    "wikipedia.org",
    "mozilla.org",
    "google.com",
    "microsoft.com",
    "apple.com",
    "amazon.com",
    "cloudflare.com",
    "vercel.com",
    "netlify.com",
    "heroku.com",
    "digitalocean.com",
    "aws.amazon.com",
    "azure.microsoft.com",
    "cloud.google.com",
}


def is_internal_host(host: str) -> bool:
    """True for loopback/private/link-local/unspecified addresses (IPv4 + IPv6)."""
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_unspecified


def check_url_safety(
    url: str, *, allowed_schemes: list[str], allowed_domains: list[str]
) -> str | None:
    """Return an error message when the URL must not be fetched, else None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "invalid url"

    host = (parsed.hostname or "").lower()
    if not host:
        return "missing hostname"

    if not parsed.scheme:
        return "missing scheme"
    if parsed.scheme not in allowed_schemes:
        return f"scheme {parsed.scheme!r} not in allowlist {allowed_schemes!r}"

    domain_allow = {d.lower() for d in allowed_domains}
    if is_internal_host(host):
        if domain_allow and host in domain_allow:
            return None
        return f"host {host!r} is internal/private address"

    if domain_allow:
        if host in domain_allow:
            return None
        for d in domain_allow:
            if host.endswith(f".{d}"):
                extra = host[: -(len(d) + 1)]
                extra_levels = extra.count(".") + 1 if extra else 0
                if extra_levels <= 3:
                    return None
        return f"host {host!r} not in allowed_domains"

    if host in _KNOWN_SAFE_DOMAINS:
        return None
    for safe_domain in _KNOWN_SAFE_DOMAINS:
        if host.endswith(f".{safe_domain}"):
            extra = host[: -(len(safe_domain) + 1)]
            extra_levels = extra.count(".") + 1 if extra else 0
            if extra_levels <= 3:
                return None
            break

    for pattern in _SUSPICIOUS_DOMAIN_PATTERNS:
        if pattern in host:
            logger.warning("suspicious domain pattern %r in %s", pattern, host)
            return f"suspicious domain pattern: {pattern}"

    if len(host) > 253:
        return "domain name too long"
    if host.count(".") > 5:
        return f"domain has too many subdomains ({host.count('.')})"
    return None


def upgrade_scheme(url: str, *, allowed_schemes: list[str]) -> str:
    """Upgrade http:// to https:// when https is allowed (ported behaviour)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if parsed.scheme == "http" and "https" in allowed_schemes:
        return "https://" + url[len("http://") :]
    return url
