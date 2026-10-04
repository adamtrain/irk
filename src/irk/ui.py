"""Everything irk reports: links on stdout and in the log, status on stderr."""

import os
import sys
import zlib
from datetime import datetime
from pathlib import Path

from irk import IrkError

DIM, YELLOW, RED = "2", "33", "31"
NAME_COLOURS = ("31", "32", "33", "34", "35", "36", "91", "92", "93", "94", "95", "96")
# The nick column starts at the classic 9-character nick limit and widens as longer ones appear.
MIN_NICK_WIDTH, MAX_NICK_WIDTH = 9, 16


def clean(text: str) -> str:
    """Drop control characters, so nothing a server sends can drive the terminal."""
    return "".join(char for char in text if char.isprintable())


def paint(text: str, colour: str) -> str:
    return f"\x1b[{colour}m{text}\x1b[0m"


class UI:
    def __init__(self, *, quiet: bool = False, debug: bool = False, channel_width: int = 0) -> None:
        self.quiet = quiet
        self.verbose = debug
        self.channel_width = channel_width
        self.nick_width = MIN_NICK_WIDTH
        self.log: Path | None = None
        colour = "NO_COLOR" not in os.environ
        # Piped output is for other programs: bare URLs, one per line.
        self.pretty = sys.stdout.isatty()
        self.colour_out = self.pretty and colour
        self.tty_err = sys.stderr.isatty()
        self.colour_err = self.tty_err and colour

    def log_to(self, path: Path) -> None:
        """Keep a record of every link in `path`, adding to whatever is already there."""
        self.log = path
        self._record("")  # find out now, not at the first link, if the log cannot be written

    def link(self, channel: str, nick: str, url: str) -> None:
        line = clean(url)
        self._record(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {line}\n")
        if self.pretty:
            channel, nick = clean(channel), clean(nick)
            self.channel_width = max(self.channel_width, len(channel))
            self.nick_width = min(max(self.nick_width, len(nick)), MAX_NICK_WIDTH)
            columns = (
                self._out(f"{datetime.now():%H:%M}", DIM),
                self._out(f"{channel:<{self.channel_width}}", _colour_for(channel)),
                self._out(f"{nick:<{self.nick_width}}", _colour_for(nick)),
                line,
            )
            line = "  ".join(columns)
        try:
            print(line, flush=True)
        except BrokenPipeError:
            # Whoever we were piped into has gone (`irk | head`), so there is nothing left to do.
            # Point stdout at /dev/null so the interpreter's exit-time flush doesn't complain.
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            raise SystemExit(0) from None

    def status(self, text: str) -> None:
        if not self.quiet:
            self._note("●", DIM, text)

    def warn(self, text: str) -> None:
        self._note("▲", YELLOW, text, label="warning: ")

    def error(self, text: str) -> None:
        self._note("✗", RED, text, label="error: ")

    def debug(self, arrow: str, line: str) -> None:
        if self.verbose:
            print(self._err(f"{arrow} {clean(line)}", DIM), file=sys.stderr)

    def interrupted(self) -> None:
        """Tidy up after Ctrl-C, which leaves a `^C` on the current line."""
        if self.tty_err:
            sys.stderr.write("\r")
        self.status("disconnected")

    def _record(self, text: str) -> None:
        if not self.log:
            return
        try:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            # Reopened for every line, so the log survives being rotated or deleted under us.
            with self.log.open("a", encoding="utf-8") as file:
                file.write(text)
        except OSError as exc:
            raise IrkError(f"cannot write to the log {self.log}: {exc.strerror}") from exc

    def _note(self, symbol: str, colour: str, text: str, *, label: str = "") -> None:
        text = clean(text)
        if self.tty_err:
            line = f"{self._err(symbol, colour)} {self._err(text, DIM)}"
        else:
            line = f"irk: {label}{text}"
        print(line, file=sys.stderr)

    def _out(self, text: str, colour: str) -> str:
        return paint(text, colour) if self.colour_out else text

    def _err(self, text: str, colour: str) -> str:
        return paint(text, colour) if self.colour_err else text


def _colour_for(name: str) -> str:
    """A colour that stays the same for a given channel or nick from run to run."""
    return NAME_COLOURS[zlib.crc32(name.lower().encode()) % len(NAME_COLOURS)]
