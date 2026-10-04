"""The client against a scripted IRC server on localhost, over real sockets and real TLS."""

import asyncio
import contextlib
import signal
import ssl
import sys
from base64 import b64decode
from collections.abc import Iterable
from pathlib import Path

import pytest
import trustme

from irk import IrkError, cli, client
from irk.config import Config, Sasl
from irk.protocol import parse
from irk.ui import UI

HOST = "127.0.0.1"
PASSWORD = "hunter2"


class RecordingUI(UI):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str]] = []
        self.statuses: list[str] = []
        self.warnings: list[str] = []
        self.wire: list[str] = []

    def link(self, channel: str, nick: str, url: str) -> None:
        self.links.append((channel, nick, url))

    def status(self, text: str) -> None:
        self.statuses.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)

    def debug(self, arrow: str, line: str) -> None:
        self.wire.append(f"{arrow} {line}")


class FakeServer:
    """Just enough of an ircd to register a client, check its SASL login and talk at it.

    After a JOIN it sends `chatter`, then a PING. The client's PONG proves it has read all of the
    chatter, so that is when the server hangs up.

    `cookie` makes it hold registration until the client answers a PING, as some ircds do.
    `keepalives` makes it sit quietly instead, answering that many client PINGs before hanging up.
    `silent` makes it sit quietly and answer nothing at all.
    """

    GOODBYE = "that is all"

    def __init__(
        self,
        *,
        caps: str | None = "sasl=PLAIN,EXTERNAL",
        password: str = PASSWORD,
        taken: Iterable[str] = (),
        chatter: Iterable[str] = (),
        cookie: str | None = None,
        keepalives: int = 0,
        silent: bool = False,
    ) -> None:
        self.caps = caps  # None plays an old server that has never heard of CAP
        self.account = f"adam\0adam\0{password}".encode()
        self.taken = set(taken)
        self.chatter = tuple(chatter)
        self.cookie = cookie
        self.keepalives = keepalives
        self.silent = silent
        self.connections = 0
        self.received: list[str] = []

    @property
    def commands(self) -> list[str]:
        return [line.split()[0] for line in self.received]

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def send(line: str) -> None:
            writer.write(f"{line}\r\n".encode())

        self.connections += 1
        nick = payload = ""
        negotiating = welcomed = False
        verified = self.cookie is None
        pings = 0
        while raw := await reader.readline():
            line = raw.decode().rstrip("\r\n")
            self.received.append(line)
            msg = parse(line)
            match msg.command, msg.params:
                case "CAP", _ if self.caps is None:
                    send(":fake 421 * CAP :Unknown command")
                case "CAP", ["LS", "302"]:
                    negotiating = True
                    send(":fake CAP * LS * :account-notify away-notify multi-prefix")
                    send(f":fake CAP * LS :{self.caps}")
                case "CAP", ["REQ", caps]:
                    send(f":fake CAP * ACK :{caps}")
                case "CAP", ["END"]:
                    negotiating = False
                case "AUTHENTICATE", ["PLAIN"]:
                    send("AUTHENTICATE +")
                case "AUTHENTICATE", [chunk]:
                    payload += "" if chunk == "+" else chunk
                    if len(chunk) < 400:
                        if b64decode(payload) == self.account:
                            send(f":fake 900 {nick} {nick}!u@h adam :You are now logged in as adam")
                            send(f":fake 903 {nick} :SASL authentication successful")
                        else:
                            send(f":fake 904 {nick} :SASL authentication failed")
                case "NICK", [wanted] if wanted in self.taken:
                    send(f":fake 433 * {wanted} :Nickname is already in use.")
                case "NICK", [wanted]:
                    nick = wanted
                    if self.cookie:
                        send(f"PING :{self.cookie}")
                case "JOIN", [channels]:
                    for channel in channels.split(","):
                        send(f":{nick}!u@h JOIN {channel}")
                        send(f":fake 353 {nick} = {channel} :{nick} @alice bob")
                    for chat in self.chatter:
                        send(chat)
                    if not self.silent and not self.keepalives:
                        send(f"PING :{self.GOODBYE}")
                case "PONG", [token] if token == self.cookie:
                    verified = True
                case "PONG", [self.GOODBYE]:
                    break
                case "PING", [token] if not self.silent:
                    send(f":fake PONG fake :{token}")
                    pings += 1
                    if pings == self.keepalives:
                        break
            if nick and verified and not negotiating and not welcomed:
                welcomed = True
                send(f":fake 001 {nick} :Welcome to the Fake IRC Network, {nick}")
        writer.close()


@pytest.fixture
def tls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ssl.SSLContext:
    """A server-side TLS context whose CA the client trusts by way of SSL_CERT_FILE."""
    ca = trustme.CA()
    ca.cert_pem.write_to_path(tmp_path / "ca.pem")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ca.pem"))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert(HOST).configure_cert(context)
    return context


def connect(
    server: FakeServer,
    ui: UI | None = None,
    *,
    tls: ssl.SSLContext | None = None,
    sasl: Sasl | None = None,
    domains: tuple[str, ...] = (),
) -> None:
    """Run the client against `server` until the connection ends, which always raises."""

    async def scenario() -> None:
        listener = await asyncio.start_server(server.serve, HOST, 0, ssl=tls)
        config = Config(
            host=HOST,
            port=listener.sockets[0].getsockname()[1],
            tls=tls is not None,
            nick="adam",
            username="adam",
            realname="Adam T",
            channels=("#links", "#more"),
            domains=domains,
            sasl=sasl,
        )
        async with listener, asyncio.timeout(5):
            await client.run(config, ui or RecordingUI())

    asyncio.run(scenario())


def test_logs_in_with_sasl_over_tls_and_reports_wanted_links(tls: ssl.SSLContext) -> None:
    server = FakeServer(
        chatter=[
            ":alice!a@h PRIVMSG #links :have you seen https://github.com/astral-sh/uv?",
            ":bob!b@h PRIVMSG #links :or https://gitlab.com/nope and https://example.org/nope",
            ":bob!b@h PRIVMSG #more :\x0304https://gist.github.com/bob/1\x03, https://youtu.be/xyz.",
            ":carol!c@h PRIVMSG adam :psst https://github.com/private/message",
            ":bot!b@h NOTICE #links :new release (https://github.com/astral-sh/ruff/releases)",
            ":fake NOTICE adam :read the rules at https://github.com/fake/rules",
            ":alice!a@h PRIVMSG #links :just chatting",
        ]
    )
    ui = RecordingUI()
    sasl = Sasl("adam", PASSWORD)
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server, ui, tls=tls, sasl=sasl, domains=("github.com", "youtu.be"))

    assert ui.links == [
        ("#links", "alice", "https://github.com/astral-sh/uv"),
        ("#more", "bob", "https://gist.github.com/bob/1"),
        ("#more", "bob", "https://youtu.be/xyz"),
        ("#links", "bot", "https://github.com/astral-sh/ruff/releases"),
    ]
    assert ui.statuses[0].startswith(f"connecting to {HOST}:")
    assert ui.statuses[0].endswith("(tls)")
    assert ui.statuses[1:] == [
        "authenticated as adam",
        "connected as adam",
        "joined #links",
        "joined #more",
    ]
    assert ui.warnings == []
    assert server.received == [
        "CAP LS 302",
        "NICK adam",
        "USER adam 0 * :Adam T",
        "CAP REQ sasl",
        "AUTHENTICATE PLAIN",
        "AUTHENTICATE YWRhbQBhZGFtAGh1bnRlcjI=",
        "CAP END",
        "JOIN #links,#more",
        "PONG :that is all",
    ]


def test_logs_the_links_it_reports_and_no_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(
        chatter=[
            ":alice!a@h PRIVMSG #links :https://github.com/astral-sh/uv and https://gitlab.com/no",
            ":carol!c@h PRIVMSG adam :psst https://github.com/private/message",
            ":bob!b@h PRIVMSG #more :https://example.org/no then https://youtu.be/xyz",
        ]
    )
    ui = UI()
    ui.log_to(tmp_path / "links.log")
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server, ui, domains=("github.com", "youtu.be"))
    wanted = ["https://github.com/astral-sh/uv", "https://youtu.be/xyz"]
    logged = (tmp_path / "links.log").read_text().splitlines()
    assert [line.split(" ", 1)[1] for line in logged] == wanted
    assert capsys.readouterr().out.splitlines() == wanted


def test_debug_shows_the_raw_authentication_exchange_without_the_password(
    tls: ssl.SSLContext,
) -> None:
    ui = RecordingUI()
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(FakeServer(), ui, tls=tls, sasl=Sasl("adam", PASSWORD))
    authentication = ui.wire[: ui.wire.index("→ CAP END") + 1]
    assert authentication == [
        "→ CAP LS 302",
        "→ NICK adam",
        "→ USER adam 0 * :Adam T",
        "← :fake CAP * LS * :account-notify away-notify multi-prefix",
        "← :fake CAP * LS :sasl=PLAIN,EXTERNAL",
        "→ CAP REQ sasl",
        "← :fake CAP * ACK :sasl",
        "→ AUTHENTICATE PLAIN",
        "← AUTHENTICATE +",
        "→ AUTHENTICATE ****",
        "← :fake 900 adam adam!u@h adam :You are now logged in as adam",
        "← :fake 903 adam :SASL authentication successful",
        "→ CAP END",
    ]
    trace = "\n".join(ui.wire)
    assert PASSWORD not in trace
    assert "YWRhbQBhZGFtAGh1bnRlcjI=" not in trace
    assert ui.wire[-2:] == ["← PING :that is all", "→ PONG :that is all"]


@pytest.mark.parametrize("length", [290, 1000])
def test_long_passwords_are_sent_in_chunks(tls: ssl.SSLContext, length: int) -> None:
    password = "p" * length
    server = FakeServer(password=password)
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server, tls=tls, sasl=Sasl("adam", password))
    assert server.commands.count("AUTHENTICATE") > 2
    assert "JOIN" in server.commands


def test_wrong_password_stops_before_joining(tls: ssl.SSLContext) -> None:
    server = FakeServer()
    with pytest.raises(IrkError, match="rejected the SASL login for 'adam'"):
        connect(server, tls=tls, sasl=Sasl("adam", "wrong"))
    assert "JOIN" not in server.commands
    assert server.commands[-1] == "QUIT"


@pytest.mark.parametrize("caps", ["multi-prefix server-time", "sasl=EXTERNAL,SCRAM-SHA-256", None])
def test_never_joins_unauthenticated_when_sasl_is_configured(
    tls: ssl.SSLContext, caps: str | None
) -> None:
    server = FakeServer(caps=caps)
    with pytest.raises(IrkError, match="server does not support SASL"):
        connect(server, tls=tls, sasl=Sasl("adam", PASSWORD))
    assert "AUTHENTICATE" not in server.commands
    assert "JOIN" not in server.commands


def test_anonymous_plain_text_with_nick_in_use() -> None:
    server = FakeServer(
        taken={"adam", "adam_"},
        chatter=[":alice!a@h PRIVMSG #links :any link will do: http://example.org/x"],
    )
    ui = RecordingUI()
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server, ui)
    assert ui.links == [("#links", "alice", "http://example.org/x")]
    assert "connected as adam__" in ui.statuses
    assert "AUTHENTICATE" not in server.commands


def test_gives_up_when_every_nick_is_taken() -> None:
    server = FakeServer(taken={"adam", "adam_", "adam__", "adam___"})
    with pytest.raises(IrkError, match="'adam' is already in use"):
        connect(server)


def test_works_with_a_server_that_has_never_heard_of_cap() -> None:
    server = FakeServer(caps=None)
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server)
    assert "JOIN" in server.commands


def test_join_failures_and_kicks_are_reported() -> None:
    server = FakeServer(
        chatter=[
            ":fake 473 adam #secret :Cannot join channel (+i) - you must be invited",
            ":op!o@h KICK #links adam :no bots",
            ":op!o@h KICK #links someone-else :bye",
        ]
    )
    ui = RecordingUI()
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server, ui)
    assert ui.warnings == [
        "cannot join #secret: Cannot join channel (+i) - you must be invited",
        "kicked from #links by op: no bots",
    ]


def test_server_error_ends_the_connection() -> None:
    server = FakeServer(chatter=["ERROR :Closing Link: 127.0.0.1 (Banned)"])
    with pytest.raises(ConnectionError, match="Banned"):
        connect(server)


def test_answers_a_ping_the_server_sends_before_it_will_register_us() -> None:
    server = FakeServer(cookie="0xC00C1E")
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server)
    assert server.received.index("PONG 0xC00C1E") < server.received.index("JOIN #links,#more")


def test_answers_a_ping_in_the_middle_of_sasl(tls: ssl.SSLContext) -> None:
    server = FakeServer(cookie="0xC00C1E")
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server, tls=tls, sasl=Sasl("adam", PASSWORD))
    assert server.received.index("PONG 0xC00C1E") < server.received.index("CAP END")
    assert "JOIN" in server.commands


def test_echoes_whatever_token_a_ping_carries() -> None:
    server = FakeServer(
        chatter=[
            "PING irc.example",
            ":fake PING :two words",
            "PING :",
            "PING :stray\0 controls\r are dropped",
            "ping :lowercase",
        ]
    )
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server)
    assert server.received[-6:] == [
        "PONG irc.example",
        "PONG :two words",
        "PONG :",
        "PONG :stray controls are dropped",
        "PONG lowercase",
        "PONG :that is all",
    ]


def test_keeps_a_quiet_connection_alive_by_pinging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "IDLE_TIMEOUT", 0.05)
    server = FakeServer(keepalives=3)
    with pytest.raises(ConnectionError, match="closed by server"):
        connect(server)
    assert server.received[-3:] == ["PING irk"] * 3


def test_pings_a_quiet_server_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "IDLE_TIMEOUT", 0.05)
    server = FakeServer(silent=True)
    with pytest.raises(ConnectionError, match="ping timeout"):
        connect(server)
    assert server.received[-2:] == ["PING irk", "QUIT"]


def test_untrusted_certificate_is_fatal_rather_than_retried() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    trustme.CA().issue_cert(HOST).configure_cert(context)
    server = FakeServer()

    async def scenario() -> None:
        listener = await asyncio.start_server(server.serve, HOST, 0, ssl=context)
        port = listener.sockets[0].getsockname()[1]
        config = Config(HOST, port, True, "adam", "adam", "adam", ("#links",), (), None)
        async with listener, asyncio.timeout(5):
            await cli.watch(config, RecordingUI())

    with pytest.raises(IrkError, match="cannot verify the certificate"):
        asyncio.run(scenario())
    assert server.received == []


def test_reconnects_after_losing_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "MIN_BACKOFF", 0.01)
    server = FakeServer()
    ui = RecordingUI()

    async def scenario() -> None:
        listener = await asyncio.start_server(server.serve, HOST, 0)
        port = listener.sockets[0].getsockname()[1]
        config = Config(HOST, port, False, "adam", "adam", "adam", ("#links",), (), None)
        async with listener, asyncio.timeout(5):
            watching = asyncio.create_task(cli.watch(config, ui))
            while server.commands.count("JOIN") < 3:  # noqa: ASYNC110
                await asyncio.sleep(0.01)
            watching.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watching

    asyncio.run(scenario())
    assert server.connections >= 3
    assert ui.warnings[:2] == ["connection closed by server"] * 2
    assert ui.statuses.count("reconnecting in 0.01s") >= 1


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_logs_out_properly_however_it_is_stopped(
    tls: ssl.SSLContext, tmp_path: Path, signum: signal.Signals
) -> None:
    """Ctrl-C, `kill` and a closed terminal each end the real command with a QUIT."""
    server = FakeServer(silent=True)
    config = tmp_path / "irk.toml"
    config.touch(mode=0o600)

    async def scenario() -> tuple[int | None, str]:
        listener = await asyncio.start_server(server.serve, HOST, 0, ssl=tls)
        port = listener.sockets[0].getsockname()[1]
        config.write_text(
            f'host = "{HOST}"\nport = {port}\nnick = "adam"\nchannels = ["#links"]\n'
            f'[sasl]\npassword = "{PASSWORD}"\n'
        )
        async with listener, asyncio.timeout(10):
            irk = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "irk", str(config), stderr=asyncio.subprocess.PIPE
            )
            while "JOIN" not in server.commands:  # noqa: ASYNC110
                await asyncio.sleep(0.01)
            irk.send_signal(signum)
            _, stderr = await irk.communicate()
        return irk.returncode, stderr.decode()

    code, stderr = asyncio.run(scenario())
    assert server.received[-2:] == ["JOIN #links", "QUIT"]
    assert stderr.splitlines()[-1] == "irk: disconnected"
    assert code == 130
