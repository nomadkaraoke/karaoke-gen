"""
First-touch signup attribution (UTM tags + referrer) for gen website signups.

The frontend (``frontend/lib/attribution.ts``) records the first landing that
carried ``utm_*`` params or came from an external site, and sends it with the
magic-link request. When that request creates the account, the cleaned values
are stored on the user as ``signup_attribution`` and summarised into
``signup_source`` so admins can compare channels (signups -> jobs -> paid).

Everything here is client-supplied, so values are whitelisted, length-capped
and stripped of control characters; nothing is trusted beyond "a label".
"""
import re
from typing import Dict, Mapping, Optional
from urllib.parse import urlparse

ATTRIBUTION_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "referrer",
    "landing_path",
    "captured_at",
)
_MAX_VALUE_LEN = 200
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

# Gen itself: a referrer from these hosts is in-app navigation, not acquisition.
_GEN_HOSTS = ("gen.nomadkaraoke.com", "api.nomadkaraoke.com")

# Referrer domains folded into one channel label each, so "m.youtube.com" and
# "youtu.be" count as the same source as utm_source=youtube.
_REFERRER_ALIASES = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "google.com": "google",
    "bing.com": "bing",
    "duckduckgo.com": "duckduckgo",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
    "reddit.com": "reddit",
    "t.co": "twitter",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "tiktok.com": "tiktok",
    "chatgpt.com": "chatgpt",
    "fiverr.com": "fiverr",
    "karaokenerds.com": "karaokenerds",
    # Our sister sites count as channels (cross-product attribution)
    "decide.nomadkaraoke.com": "decide",
    "sing.nomadkaraoke.com": "kjbox",
    "kjbox.cc": "kjbox",
    "requests.nomadkaraoke.com": "requests_board",
    "nomadkaraoke.com": "website",
}


def _clean(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = _CONTROL_CHARS_RE.sub("", value).strip()
    return value[:_MAX_VALUE_LEN] or None


def _referrer_domain(referrer: Optional[str]) -> Optional[str]:
    """Bare host of a referrer URL or host ("https://www.youtube.com/" -> "youtube.com")."""
    if not referrer:
        return None
    try:
        host = urlparse(referrer if "//" in referrer else f"//{referrer}").hostname or ""
    except ValueError:  # e.g. a malformed bracketed IPv6 host
        return None
    host = host.lower()
    for prefix in ("www.", "m.", "l.", "lm.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host or None


def _is_gen_host(host: str) -> bool:
    return host in _GEN_HOSTS or host in ("localhost", "127.0.0.1")


def _referrer_channel(host: str) -> str:
    for domain, label in _REFERRER_ALIASES.items():
        if host == domain or host.endswith("." + domain):
            return label
    # google.co.uk, google.de, ...
    if re.match(r"^google\.[a-z.]+$", host):
        return "google"
    return host


def clean_attribution(raw: Optional[Mapping[str, object]]) -> Optional[Dict[str, str]]:
    """Whitelist + sanitise a client-supplied attribution dict.

    Returns None when nothing useful remains (no utm tag and no external referrer),
    so plain direct signups store nothing.
    """
    if not raw or not isinstance(raw, Mapping):
        return None
    cleaned: Dict[str, str] = {}
    for key in ATTRIBUTION_KEYS:
        value = _clean(raw.get(key))
        if value:
            cleaned[key] = value

    host = _referrer_domain(cleaned.get("referrer"))
    if host and not _is_gen_host(host):
        cleaned["referrer"] = host
    else:
        cleaned.pop("referrer", None)

    for key in ("utm_source", "utm_medium", "utm_campaign"):
        if key in cleaned:
            cleaned[key] = cleaned[key].lower()

    if not any(k in cleaned for k in ("utm_source", "utm_medium", "utm_campaign", "referrer")):
        return None
    return cleaned


def derive_signup_source(attribution: Optional[Mapping[str, str]]) -> Optional[str]:
    """One channel label for a cleaned attribution: utm_source, else the referrer's channel."""
    if not attribution:
        return None
    if attribution.get("utm_source"):
        return attribution["utm_source"][:64]
    host = attribution.get("referrer")
    if host:
        return _referrer_channel(host)[:64]
    return None
