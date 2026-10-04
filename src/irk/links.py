"""Finding links in IRC messages and deciding whether they point at a wanted domain."""

import re
from collections.abc import Collection
from urllib.parse import urlsplit

# mIRC-style bold/italic/colour codes, which would otherwise end up glued to a URL.
FORMATTING = re.compile(
    r"\x03(?:\d{1,2}(?:,\d{1,2})?)?"
    r"|\x04(?:[0-9a-f]{6}(?:,[0-9a-f]{6})?)?"
    r"|[\x02\x0f\x11\x16\x1d\x1e\x1f]",
    re.IGNORECASE,
)
URL = re.compile(r"https?://[^\s<>\"\x00-\x1f\x7f-\x9f]+", re.IGNORECASE)
BRACKETS = {")": "(", "]": "[", "}": "{"}


def extract(text: str) -> list[str]:
    """Every distinct http(s) URL in a message, in the order they appear."""
    urls = (_trim(match.group()) for match in URL.finditer(FORMATTING.sub("", text)))
    return list(dict.fromkeys(urls))


def matches(url: str, domains: Collection[str]) -> bool:
    """Whether a URL's host is one of `domains` or a subdomain of one. No domains means any."""
    try:
        host = (urlsplit(url).hostname or "").rstrip(".")
    except ValueError:
        return False
    if not host:
        return False
    return not domains or any(host == d or host.endswith(f".{d}") for d in domains)


def _trim(url: str) -> str:
    """Drop the sentence punctuation and unbalanced closing brackets that trail URLs in prose."""
    while url:
        last = url[-1]
        if last in ".,;:!?'" or (last in BRACKETS and url.count(last) > url.count(BRACKETS[last])):
            url = url[:-1]
        else:
            break
    return url
