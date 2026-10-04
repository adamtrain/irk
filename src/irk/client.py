"""One connection to an IRC server: register, authenticate, join, and report links."""

import asyncio
import contextlib
import ssl
from collections.abc import AsyncIterator

from irk import IrkError, links
from irk.config import Config
from irk.protocol import Message, encode, parse, sasl_plain
from irk.ui import UI

CONNECT_TIMEOUT = 30
# Seconds of silence before we ping the server, and then again before we give up on it.
IDLE_TIMEOUT = 120
CLOSE_TIMEOUT = 2
MAX_NICK_RETRIES = 3
# No IRC line can contain these. Dropping strays means what we echo back, like a PING token,
# is always something we can send.
UNSENDABLE = str.maketrans("", "", "\r\n\0")


async def run(config: Config, ui: UI) -> None:
    """Connect and handle messages until the connection drops, which raises OSError."""
    scheme = "tls" if config.tls else "plain text"
    ui.status(f"connecting to {config.host}:{config.port} ({scheme})")
    context = ssl.create_default_context() if config.tls else None
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            reader, writer = await asyncio.open_connection(config.host, config.port, ssl=context)
    except TimeoutError:
        raise ConnectionError("connection timed out") from None
    client = Client(config, ui, writer)
    try:
        client.register()
        async for line in client.lines(reader):
            client.handle(parse(line))
    finally:
        await client.close()


class Client:
    def __init__(self, config: Config, ui: UI, writer: asyncio.StreamWriter) -> None:
        self.config = config
        self.ui = ui
        self.writer = writer
        self.nick = config.nick
        self.caps: dict[str, str] = {}
        self.authenticated = False

    def send(self, command: str, *params: str, secret: bool = False) -> None:
        line = encode(command, *params)
        self.writer.write(line)
        self.ui.debug("→", f"{command} ****" if secret else line.decode().rstrip("\r\n"))

    def register(self) -> None:
        # Opening with CAP LS makes the server hold registration open until our CAP END,
        # which is what gives SASL room to happen before we are let in.
        self.send("CAP", "LS", "302")
        self.send("NICK", self.nick)
        self.send("USER", self.config.username, "0", "*", self.config.realname)

    async def lines(self, reader: asyncio.StreamReader) -> AsyncIterator[str]:
        """Yield each line the server sends, pinging it whenever it goes quiet."""
        pinged = False
        while True:
            try:
                async with asyncio.timeout(IDLE_TIMEOUT):
                    raw = await reader.readline()
            except TimeoutError:
                if pinged:
                    raise ConnectionError("ping timeout") from None
                pinged = True
                self.send("PING", "irk")
                continue
            except ValueError:
                raise ConnectionError("server sent an overlong line") from None
            if not raw:
                raise ConnectionError("connection closed by server")
            pinged = False
            if line := raw.decode(errors="replace").translate(UNSENDABLE).strip(" "):
                self.ui.debug("←", line)
                yield line

    def handle(self, msg: Message) -> None:
        match msg.command, msg.params:
            case "PING", token:
                # Servers ping to check on us, and some will not finish registration until
                # they get their token back, so this has to work at any point.
                self.send("PONG", *token)

            case "CAP", [_, "LS", "*", caps]:
                self.caps.update(_parse_caps(caps))
            case "CAP", [_, "LS", caps]:
                self.caps.update(_parse_caps(caps))
                self.request_sasl()
            case "CAP", [_, "ACK", _]:
                self.send("AUTHENTICATE", "PLAIN")
            case "CAP", [_, "NAK", _]:
                raise IrkError("server refused to enable SASL")
            case "AUTHENTICATE", ["+"] if sasl := self.config.sasl:
                for chunk in sasl_plain(sasl.username, sasl.password):
                    self.send("AUTHENTICATE", chunk, secret=True)
            case "903", _ if sasl := self.config.sasl:
                self.authenticated = True
                self.ui.status(f"authenticated as {sasl.username}")
                self.send("CAP", "END")
            case "902" | "904" | "905" | "906", [*_, reason] if sasl := self.config.sasl:
                raise IrkError(f"server rejected the SASL login for {sasl.username!r}: {reason}")

            case "001", [nick, *_]:
                if self.config.sasl and not self.authenticated:
                    # A server that ignores CAP lets us in unauthenticated. Don't go along with it.
                    raise IrkError("server does not support SASL")
                self.nick = nick
                self.ui.status(f"connected as {nick}")
                self.send("JOIN", ",".join(self.config.channels))
            case "432", [*_, reason]:
                raise IrkError(f"server rejected the nick {self.nick!r}: {reason}")
            case "433" | "437", _:
                if len(self.nick) - len(self.config.nick) >= MAX_NICK_RETRIES:
                    raise IrkError(f"the nick {self.config.nick!r} is already in use")
                self.nick += "_"
                self.send("NICK", self.nick)
            case "NICK", [nick] if msg.nick == self.nick:
                self.nick = nick

            case "JOIN", [channel, *_] if msg.nick == self.nick:
                self.ui.status(f"joined {channel}")
            case "403" | "405" | "471" | "473" | "474" | "475" | "477", [_, channel, reason]:
                self.ui.warn(f"cannot join {channel}: {reason}")
            case "KICK", [channel, nick, *reason] if nick == self.nick:
                self.ui.warn(f"kicked from {channel} by {msg.nick}: {' '.join(reason)}")

            case "PRIVMSG" | "NOTICE", [target, text] if target.startswith(("#", "&")):
                for url in links.extract(text):
                    if links.matches(url, self.config.domains):
                        self.ui.link(target, msg.nick, url)

            case "ERROR", [reason]:
                raise ConnectionError(reason)

    def request_sasl(self) -> None:
        """Act on the server's full capability list: start SASL if configured, else move on."""
        if not self.config.sasl:
            self.send("CAP", "END")
            return
        mechanisms = self.caps.get("sasl")
        if mechanisms is None:
            raise IrkError("server does not support SASL")
        if mechanisms and "PLAIN" not in mechanisms.split(","):
            raise IrkError(f"server does not support SASL PLAIN, only {mechanisms}")
        self.send("CAP", "REQ", "sasl")

    async def close(self) -> None:
        with contextlib.suppress(OSError):
            if not self.writer.is_closing():
                self.send("QUIT")
            self.writer.close()
            async with asyncio.timeout(CLOSE_TIMEOUT):
                await self.writer.wait_closed()


def _parse_caps(caps: str) -> dict[str, str]:
    """Turn `sasl=PLAIN,EXTERNAL cap-notify` into {"sasl": "PLAIN,EXTERNAL", "cap-notify": ""}."""
    return {name: value for name, _, value in (cap.partition("=") for cap in caps.split())}
