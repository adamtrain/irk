"""The slice of the IRC wire protocol irk needs: parsing, encoding and SASL PLAIN."""

from base64 import b64encode
from dataclasses import dataclass

SASL_CHUNK = 400


@dataclass(frozen=True, slots=True)
class Message:
    command: str
    params: tuple[str, ...] = ()
    source: str = ""

    @property
    def nick(self) -> str:
        return self.source.partition("!")[0]


def parse(line: str) -> Message:
    """Parse one line, already stripped of its CRLF. Garbage parses to an empty command."""
    if line.startswith("@"):  # IRCv3 message tags: irk requests none, so skip any we are sent
        line = line.partition(" ")[2].lstrip(" ")
    source = ""
    if line.startswith(":"):
        source, _, line = line[1:].partition(" ")
    head, has_trailing, trailing = line.partition(" :")
    command, *params = head.split() or [""]
    if has_trailing:
        params.append(trailing)
    return Message(command.upper(), tuple(params), source)


def encode(command: str, *params: str) -> bytes:
    """Serialise a message. Only the last parameter may be empty or contain spaces."""
    *middle, last = command, *params
    if any(char in word for word in (*middle, last) for char in "\r\n\0"):
        raise ValueError("IRC messages cannot contain CR, LF or NUL")
    if any(not word or " " in word or word.startswith(":") for word in middle):
        raise ValueError("only the last parameter may be empty, contain spaces or start with ':'")
    if params and (not last or " " in last or last.startswith(":")):
        last = f":{last}"
    return " ".join((*middle, last)).encode() + b"\r\n"


def sasl_plain(username: str, password: str) -> list[str]:
    """The AUTHENTICATE payloads for SASL PLAIN, in the 400-byte chunks IRCv3 requires."""
    payload = b64encode(f"{username}\0{username}\0{password}".encode()).decode()
    chunks = [payload[i : i + SASL_CHUNK] for i in range(0, len(payload), SASL_CHUNK)]
    if len(chunks[-1]) == SASL_CHUNK:  # a full final chunk needs an explicit terminator
        chunks.append("+")
    return chunks
