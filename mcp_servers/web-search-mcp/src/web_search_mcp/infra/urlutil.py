"""URL canonicalization helpers."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_DROP_QUERY_KEYS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "spm",
        "from",
        "from_source",
        "ref",
        "ref_src",
    }
)


def canonicalize_url(raw: str) -> str:
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw.strip()
    if not parsed.scheme or not parsed.netloc:
        return raw.strip()
    q = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        lk = k.strip().lower()
        if not lk:
            continue
        if lk in _DROP_QUERY_KEYS or lk.startswith("utm_"):
            continue
        q.append((k, v))
    q.sort(key=lambda kv: (kv[0], kv[1]))
    new_query = urlencode(q, doseq=True)
    cleaned = parsed._replace(fragment="", query=new_query)
    return urlunparse(cleaned)


def host_of(raw_url: str) -> str:
    try:
        return (urlparse(raw_url).hostname or "").lower()
    except ValueError:
        return ""
