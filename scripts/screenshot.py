"""Regenerate the README screenshot in docs/.

    uv run scripts/screenshot.py

A made-up session is played through the same UI code irk uses for real ones, and the coloured
terminal output that comes out is drawn as an SVG.
"""

import contextlib
import html
import io
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from unittest import mock

from irk import ui as irk_ui
from irk.ui import UI, paint

OUT = Path(__file__).resolve().parent.parent / "docs" / "hero.svg"

PROMPT = "\u276f"  # a shell-prompt chevron
COMMAND = "irk libera"
STATUS = [
    "watching for links to github.com, arxiv.org",
    "logging links to /home/adam/irk/libera.log",
    "connecting to irc.libera.chat:6697 (tls)",
    "authenticated as adam",
    "connected as adam",
    "joined #python",
    "joined #rust",
    "joined #linux",
]
LINKS = [
    ("21:14", "#python", "alice", "https://github.com/astral-sh/uv/releases/tag/0.12.21"),
    ("21:17", "#rust", "ferris", "https://github.com/rust-lang/rust/pull/147210"),
    ("21:31", "#python", "guido_fan", "https://arxiv.org/abs/1706.03762"),
    ("21:40", "#linux", "tux", "https://gist.github.com/tux/8f3a1c2d9e4b5a6f7081"),
    ("21:52", "#rust", "alice", "https://github.com/tokio-rs/tokio/issues/7104"),
    ("22:05", "#linux", "penguin", "https://github.com/torvalds/linux/commit/9c0e4a1"),
]

BACKGROUND, FOREGROUND, MUTED = "#101219", "#e2e4ec", "#7f8496"
FILLS = {
    "0": FOREGROUND,
    "2": MUTED,
    "31": "#f2506e",
    "32": "#1fbf8f",
    "33": "#eb9a12",
    "34": "#7c83f7",
    "35": "#a871f7",
    "36": "#56b6c2",
    "91": "#ff6e88",
    "92": "#46d6aa",
    "93": "#fab83c",
    "94": "#989eff",
    "95": "#be92ff",
    "96": "#78d0dc",
}
FONT = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', monospace"
FONT_SIZE, CELL_WIDTH, LINE_HEIGHT = 20, 12.2, 27
PADDING, TITLE_BAR = 26, 46
SGR = re.compile(r"\x1b\[(\d+)m")


def session() -> list[str]:
    """The terminal output of a made-up session, escape codes and all."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        ui = UI(channel_width=max(len(channel) for _, channel, _, _ in LINKS))
        ui.pretty = ui.colour_out = ui.tty_err = ui.colour_err = True
        print(f"{paint(PROMPT, '32')} {COMMAND}")
        for text in STATUS:
            ui.status(text)
        for time, channel, nick, url in LINKS:
            clock = mock.Mock(now=lambda time=time: datetime.strptime(time, "%H:%M"))
            with mock.patch.object(irk_ui, "datetime", clock):
                ui.link(channel, nick, url)
    return output.getvalue().splitlines()


def spans(line: str) -> Iterator[tuple[int, str, str]]:
    """Split a line into (column, text, fill) runs, one per colour."""
    column, fill = 0, FOREGROUND
    for index, part in enumerate(SGR.split(line)):
        if index % 2:
            fill = FILLS[part]
        else:
            if part.strip():
                yield column, part, fill
            column += len(part)


def draw(lines: list[str]) -> str:
    columns = max(len(SGR.sub("", line)) for line in lines)
    width = round(2 * PADDING + columns * CELL_WIDTH)
    height = TITLE_BAR + len(lines) * LINE_HEIGHT + PADDING
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" rx="12" fill="{BACKGROUND}"/>',
        *(
            f'<circle cx="{PADDING + 4 + 24 * index}" cy="{TITLE_BAR // 2}" r="7" fill="{fill}"/>'
            for index, fill in enumerate(("#ff5f57", "#febc2e", "#28c840"))
        ),
        f'<g font-family="{FONT}" font-size="{FONT_SIZE}">',
    ]
    for row, line in enumerate(lines):
        y = TITLE_BAR + row * LINE_HEIGHT + FONT_SIZE
        for column, text, fill in spans(line):
            # textLength pins each run to the character grid, whichever font ends up being used.
            x, length = PADDING + column * CELL_WIDTH, len(text) * CELL_WIDTH
            content = html.escape(text).replace(" ", "&#160;")
            place = f'x="{x:.1f}" y="{y}" textLength="{length:.1f}"'
            svg.append(f'<text {place} fill="{fill}">{content}</text>')
    return "\n".join([*svg, "</g>", "</svg>", ""])


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(draw(session()))
    print(f"wrote {OUT}")
