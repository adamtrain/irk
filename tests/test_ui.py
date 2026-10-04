import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from irk import IrkError
from irk.ui import UI


def test_piped_output_is_bare_urls(capsys: pytest.CaptureFixture[str]) -> None:
    ui = UI()
    ui.status("joined #links")
    ui.link("#links", "alice", "https://a.example/x")
    ui.warn("cannot join #secret: invite only")
    ui.error("SASL authentication failed")
    out, err = capsys.readouterr()
    assert out == "https://a.example/x\n"
    assert err.splitlines() == [
        "irk: joined #links",
        "irk: warning: cannot join #secret: invite only",
        "irk: error: SASL authentication failed",
    ]


def test_quiet_keeps_warnings_but_drops_status(capsys: pytest.CaptureFixture[str]) -> None:
    ui = UI(quiet=True)
    ui.status("joined #links")
    ui.warn("kicked from #links")
    assert capsys.readouterr().err == "irk: warning: kicked from #links\n"


def test_debug_is_opt_in(capsys: pytest.CaptureFixture[str]) -> None:
    UI().debug("→", "NICK adam")
    assert capsys.readouterr().err == ""
    UI(debug=True).debug("→", "NICK adam")
    assert capsys.readouterr().err == "→ NICK adam\n"


def test_terminal_output_is_aligned_columns(capsys: pytest.CaptureFixture[str]) -> None:
    ui = UI(channel_width=len("#links-and-more"))
    ui.pretty = True
    ui.link("#links", "alice", "https://a.example/1")
    ui.link("#links-and-more", "bartholomew", "https://a.example/2")
    ui.link("#links", "bob", "https://a.example/3")
    ui.link("#links", "a-nick-longer-than-the-column", "https://a.example/4")
    ui.link("#links", "bob", "https://a.example/5")
    lines = [re.sub(r"^\d\d:\d\d", "HH:MM", line) for line in capsys.readouterr().out.splitlines()]
    assert lines == [
        "HH:MM  #links           alice      https://a.example/1",
        "HH:MM  #links-and-more  bartholomew  https://a.example/2",
        "HH:MM  #links           bob          https://a.example/3",
        "HH:MM  #links           a-nick-longer-than-the-column  https://a.example/4",
        "HH:MM  #links           bob               https://a.example/5",
    ]


def test_colour_is_stable_per_name(capsys: pytest.CaptureFixture[str]) -> None:
    ui = UI()
    ui.pretty = ui.colour_out = True
    ui.link("#links", "alice", "https://a.example/1")
    ui.link("#links", "alice", "https://a.example/2")
    first, second = capsys.readouterr().out.splitlines()
    assert "\x1b[" in first
    assert first.replace("/1", "/2") == second


def test_control_characters_from_the_network_never_reach_the_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ui = UI(debug=True)
    ui.pretty = True
    ui.link("#li\x1b]0;pwned\x07nks", "al\x1b[2Jice", "https://a.example/\x1b[31m")
    ui.status("joined \x1b[31m#links\r")
    ui.debug("←", ":x NOTICE * :\x1b[2J\x07")
    out, err = capsys.readouterr()
    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f]", out + err)


def test_log_gets_a_timestamped_line_per_link(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "links.log"
    ui = UI()
    ui.log_to(log)
    assert log.read_text() == ""

    ui.link("#links", "alice", "https://a.example/1")
    ui.link("#links", "bob", "https://a.example/2")
    ui.link("#links", "carol", "https://a.example/1")

    lines = log.read_text().splitlines()
    assert [line.split(" ", 1)[1] for line in lines] == [
        "https://a.example/1",
        "https://a.example/2",
        "https://a.example/1",
    ]
    for line in lines:
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d https://\S+", line)
        seen = datetime.fromisoformat(line.split(" ")[0])
        assert abs(datetime.now().astimezone() - seen) < timedelta(seconds=5)
    assert capsys.readouterr().out.count("\n") == 3


def test_log_is_appended_to_never_overwritten(tmp_path: Path) -> None:
    log = tmp_path / "links.log"
    log.write_text("2025-01-01T00:00:00+00:00 https://old.example/kept\n")
    for url in ("https://a.example/1", "https://a.example/2"):
        ui = UI()  # a fresh UI each time, as if irk had been restarted
        ui.log_to(log)
        ui.link("#links", "alice", url)
    assert [line.split(" ", 1)[1] for line in log.read_text().splitlines()] == [
        "https://old.example/kept",
        "https://a.example/1",
        "https://a.example/2",
    ]


def test_log_is_written_whether_or_not_stdout_is_a_terminal(tmp_path: Path) -> None:
    log = tmp_path / "links.log"
    for pretty in (True, False):
        ui = UI()
        ui.pretty = pretty
        ui.log_to(log)
        ui.link("#links", "alice", "https://a.example/\x1b[31mx")
    assert [line.split(" ", 1)[1] for line in log.read_text().splitlines()] == [
        "https://a.example/[31mx",
        "https://a.example/[31mx",
    ]


def test_log_directory_is_created_and_survives_the_file_being_removed(tmp_path: Path) -> None:
    log = tmp_path / "not" / "there" / "yet" / "links.log"
    ui = UI()
    ui.log_to(log)
    ui.link("#links", "alice", "https://a.example/1")
    log.unlink()
    ui.link("#links", "alice", "https://a.example/2")
    assert log.read_text().endswith(" https://a.example/2\n")


def test_unwritable_log_is_an_error_at_startup_and_later(tmp_path: Path) -> None:
    with pytest.raises(IrkError, match=rf"cannot write to the log {tmp_path}: Is a directory"):
        UI().log_to(tmp_path)

    log = tmp_path / "links.log"
    ui = UI()
    ui.log_to(log)
    log.unlink()
    log.mkdir()
    with pytest.raises(IrkError, match=r"cannot write to the log .*links\.log: Is a directory"):
        ui.link("#links", "alice", "https://a.example/1")


def test_no_log_unless_asked_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    UI().link("#links", "alice", "https://a.example/1")
    assert list(tmp_path.iterdir()) == []
