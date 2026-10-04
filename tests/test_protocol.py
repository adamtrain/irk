from base64 import b64decode

import pytest

from irk.protocol import Message, encode, parse, sasl_plain


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("PING :abc", Message("PING", ("abc",))),
        (
            ":irc.example 001 adam :Welcome to IRC",
            Message("001", ("adam", "Welcome to IRC"), "irc.example"),
        ),
        (
            ":a!b@c PRIVMSG #chan :see https://x.org :)",
            Message("PRIVMSG", ("#chan", "see https://x.org :)"), "a!b@c"),
        ),
        (":a!b@c JOIN #chan", Message("JOIN", ("#chan",), "a!b@c")),
        (
            ":irc.example CAP * LS * :sasl=PLAIN multi-prefix",
            Message("CAP", ("*", "LS", "*", "sasl=PLAIN multi-prefix"), "irc.example"),
        ),
        (
            "@time=2026-10-03T21:00:00Z;msgid=1 :a!b@c PRIVMSG #chan :hi",
            Message("PRIVMSG", ("#chan", "hi"), "a!b@c"),
        ),
        ("privmsg  #chan   :spaced   out", Message("PRIVMSG", ("#chan", "spaced   out"))),
        ("PRIVMSG #chan :", Message("PRIVMSG", ("#chan", ""))),
        (":only.a.source", Message("", (), "only.a.source")),
    ],
)
def test_parse(line: str, expected: Message) -> None:
    assert parse(line) == expected


def test_nick_comes_from_the_source() -> None:
    assert parse(":alice!~a@host PRIVMSG #chan :hi").nick == "alice"
    assert parse(":irc.example NOTICE * :hi").nick == "irc.example"


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("QUIT",), b"QUIT\r\n"),
        (("NICK", "adam"), b"NICK adam\r\n"),
        (("CAP", "LS", "302"), b"CAP LS 302\r\n"),
        (("USER", "adam", "0", "*", "Adam T"), b"USER adam 0 * :Adam T\r\n"),
        (("PONG", ":)"), b"PONG ::)\r\n"),
        (("PRIVMSG", "#chan", ""), b"PRIVMSG #chan :\r\n"),
    ],
)
def test_encode(args: tuple[str, ...], expected: bytes) -> None:
    assert encode(*args) == expected


@pytest.mark.parametrize(
    "args",
    [
        ("NICK", "adam\r\nJOIN #evil"),
        ("JOIN", "#a\n"),
        ("USER", "two words", "0", "*", "real name"),
        ("USER", "", "0", "*", "real name"),
        ("JOIN", ":colon", "key"),
    ],
)
def test_encode_refuses_to_build_a_malformed_line(args: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match=r"IRC messages|last parameter"):
        encode(*args)


def test_sasl_plain_payload() -> None:
    assert [b64decode(chunk) for chunk in sasl_plain("adam", "hunter2")] == [b"adam\0adam\0hunter2"]


@pytest.mark.parametrize(
    ("password_length", "chunk_lengths"),
    [
        (1, [16]),
        (287, [396]),
        (288, [400, 1]),  # base64 padding fills the chunk, so it needs the "+" terminator
        (290, [400, 1]),  # 300 bytes is exactly 400 characters of base64
        (291, [400, 4]),
        (590, [400, 400, 1]),
    ],
)
def test_sasl_plain_chunking(password_length: int, chunk_lengths: list[int]) -> None:
    password = "p" * password_length
    chunks = sasl_plain("adam", password)
    assert [len(chunk) for chunk in chunks] == chunk_lengths
    payload = "".join(chunk for chunk in chunks if chunk != "+")
    assert b64decode(payload) == f"adam\0adam\0{password}".encode()
    assert (chunks[-1] == "+") == (len(payload) % 400 == 0)
