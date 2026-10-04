import pytest

from irk.links import extract, matches


@pytest.mark.parametrize(
    ("text", "urls"),
    [
        ("no links here", []),
        ("look: https://github.com/astral-sh/uv", ["https://github.com/astral-sh/uv"]),
        ("http://a.example and HTTPS://B.example/x", ["http://a.example", "HTTPS://B.example/x"]),
        ("twice https://a.example twice https://a.example", ["https://a.example"]),
        ("Nice, https://a.example/x.", ["https://a.example/x"]),
        ("really?! https://a.example/x?!", ["https://a.example/x"]),
        ("(see https://a.example/x)", ["https://a.example/x"]),
        ("(see https://a.example/x).", ["https://a.example/x"]),
        ("<https://a.example/x>", ["https://a.example/x"]),
        ('"https://a.example/x"', ["https://a.example/x"]),
        ("[docs](https://a.example/x)", ["https://a.example/x"]),
        (
            "https://en.wikipedia.org/wiki/Rust_(programming_language)",
            ["https://en.wikipedia.org/wiki/Rust_(programming_language)"],
        ),
        ("https://a.example/?q=1&r=2#frag", ["https://a.example/?q=1&r=2#frag"]),
        ("\x02bold\x02 \x0304,01https://a.example/x\x03 \x1fend", ["https://a.example/x"]),
        ("\x01ACTION likes https://a.example/x\x01", ["https://a.example/x"]),
        ("ftp://a.example https:// https://.", []),
    ],
)
def test_extract(text: str, urls: list[str]) -> None:
    assert [url for url in extract(text) if matches(url, ())] == urls


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com",
        "https://github.com/astral-sh/ruff",
        "http://GitHub.com/astral-sh/ruff",
        "https://gist.github.com/someone/abc",
        "https://github.com:443/x",
        "https://github.com./x",
        "https://user@github.com/x",
    ],
)
def test_matches(url: str) -> None:
    assert matches(url, ("example.org", "github.com"))


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/x",
        "https://notgithub.com/x",
        "https://github.com.evil.example/x",
        "https://github.com@evil.example/x",
        "https://evil.example/github.com",
        "https://evil.example/?next=https://github.com",
        "https://[not-an-ip/x",
    ],
)
def test_does_not_match(url: str) -> None:
    assert not matches(url, ("example.org", "github.com"))


def test_no_domains_matches_any_link() -> None:
    assert matches("https://anything.example/x", ())
