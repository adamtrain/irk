from pathlib import Path

import pytest

from irk import IrkError
from irk.config import Config, Sasl, available, directory, find, load

MINIMAL = """
host = "irc.example"
nick = "adam"
channels = ["#links"]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "irk.toml"
    path.write_text(text)
    return path


def test_defaults(tmp_path: Path) -> None:
    assert load(write(tmp_path, MINIMAL)) == Config(
        host="irc.example",
        port=6697,
        tls=True,
        nick="adam",
        username="adam",
        realname="adam",
        channels=("#links",),
        domains=(),
        sasl=None,
    )


def test_everything_set(tmp_path: Path) -> None:
    text = """
    host = "irc.example"
    port = 7000
    tls = false
    nick = "adam"
    username = "at"
    realname = "Adam T"
    channels = ["#links", "&local"]
    domains = ["GitHub.com", "*.example.org", ".youtu.be."]
    log = "/var/log/irk/links.log"

    [sasl]
    username = "adam-account"
    password = "hunter2"
    """
    assert load(write(tmp_path, text)) == Config(
        host="irc.example",
        port=7000,
        tls=False,
        nick="adam",
        username="at",
        realname="Adam T",
        channels=("#links", "&local"),
        domains=("github.com", "example.org", "youtu.be"),
        sasl=Sasl("adam-account", "hunter2"),
        log=Path("/var/log/irk/links.log"),
    )


def test_plain_text_port_and_sasl_username_defaults(tmp_path: Path) -> None:
    config = load(write(tmp_path, MINIMAL + 'tls = false\n[sasl]\npassword = "hunter2"'))
    assert config.port == 6667
    assert config.sasl == Sasl("adam", "hunter2")


def test_log_path_may_start_with_a_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/adam")
    config = load(write(tmp_path, MINIMAL + 'log = "~/irk/links.log"'))
    assert config.log == Path("/home/adam/irk/links.log")


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ('nick = "adam"\nchannels = ["#links"]', "'host' is required"),
        ('host = "irc.example"\nchannels = ["#links"]', "'nick' is required"),
        ('host = "irc.example"\nnick = "adam"', "'channels' is required"),
        (MINIMAL.replace('["#links"]', "[]"), "at least one channel"),
        (MINIMAL.replace("#links", "links"), "'links' is not a channel name"),
        (MINIMAL.replace("#links", "#a,#b"), "'#a,#b' is not a channel name"),
        (MINIMAL.replace('["#links"]', '"#links"'), "'channels' must be a list"),
        (MINIMAL.replace('["#links"]', '["#links", 7]'), "'channels' must be a list of strings"),
        (MINIMAL.replace("adam", "two words"), "'nick' must be a single word"),
        (MINIMAL + "port = true", "'port' must be a number"),
        (MINIMAL + "port = 70000", "'port' must be between 1 and 65535"),
        (MINIMAL + 'tls = "yes"', "'tls' must be true or false"),
        (MINIMAL + 'domain = ["github.com"]', "unknown key 'domain'"),
        (MINIMAL + 'domains = ["https://github.com/"]', "is not a bare domain"),
        (MINIMAL + 'sasl = "hunter2"', "'sasl' must be a table"),
        (MINIMAL + "[sasl]", "'sasl.password' is required"),
        (MINIMAL + '[sasl]\npassword = "x"\nmechanism = "PLAIN"', "unknown key 'sasl.mechanism'"),
        (MINIMAL + "log = true", "'log' must be a string"),
        (MINIMAL + 'log = "links.log"', "'log' must be an absolute path, or start with ~/"),
        (MINIMAL + 'log = ""', "'log' must be an absolute path, or start with ~/"),
        (MINIMAL + 'log = "~no-such-user-here/links.log"', "'log': Could not determine home"),
        ("host = ", "Invalid value"),
    ],
)
def test_mistakes_are_explained(tmp_path: Path, text: str, complaint: str) -> None:
    path = write(tmp_path, text)
    with pytest.raises(IrkError) as error:
        load(path)
    assert str(error.value).startswith(f"{path}: ")
    assert complaint in str(error.value)


def test_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(IrkError, match=r"cannot read .*nope\.toml: No such file"):
        load(tmp_path / "nope.toml")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty config directory, standing in for ~/.config/irk."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "irk"


def test_config_directory_is_in_dot_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert directory() == tmp_path / ".config" / "irk"


def test_find_looks_a_name_up_in_the_config_directory(home: Path) -> None:
    home.mkdir()
    (home / "libera.toml").write_text(MINIMAL)
    (home / "oftc").write_text(MINIMAL)
    (home / ".DS_Store").write_text("")
    (home / "a-directory").mkdir()

    assert find("libera") == home / "libera.toml"
    assert find("libera.toml") == home / "libera.toml"
    assert find("oftc") == home / "oftc"
    assert available() == ["libera", "oftc"]
    with pytest.raises(IrkError, match=rf"no config named 'nope': {home} has: libera, oftc"):
        find("nope")
    with pytest.raises(IrkError, match=rf"which config\? {home} has: libera, oftc"):
        find(None)


def test_find_explains_how_to_start_when_there_are_no_configs(home: Path) -> None:
    for name in ("libera", None):
        with pytest.raises(IrkError, match=rf"no configs yet: create {home}/<name>\.toml"):
            find(name)


@pytest.mark.parametrize("path", ["/etc/irk.toml", "./libera.toml", "../configs/libera"])
def test_find_takes_a_path_as_it_is(home: Path, path: str) -> None:
    assert find(path) == Path(path)
