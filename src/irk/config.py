"""Finding, loading and validating irk's TOML config file."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from irk import IrkError

KINDS: dict[type, str] = {
    str: "a string",
    int: "a number",
    bool: "true or false",
    list: "a list",
    dict: "a table",
}


@dataclass(frozen=True, slots=True)
class Sasl:
    username: str
    password: str


@dataclass(frozen=True, slots=True)
class Config:
    host: str
    port: int
    tls: bool
    nick: str
    username: str
    realname: str
    channels: tuple[str, ...]
    domains: tuple[str, ...]
    sasl: Sasl | None
    log: Path | None = None


def directory() -> Path:
    """Where configs live: ~/.config/irk, unless XDG_CONFIG_HOME says otherwise."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "irk"


def available() -> list[str]:
    """The names of the configs in the config directory."""
    files = (path for path in directory().glob("[!.]*") if path.is_file())
    return sorted({path.name.removesuffix(".toml") for path in files})


def find(name: str | None) -> Path:
    """Resolve the config named on the command line, the way catgirl does.

    A name starting with /, ./ or ../ is a path to a file. Any other name is a file in the
    config directory, whose .toml may be left off: `irk libera` is ~/.config/irk/libera.toml.
    """
    if name and name.startswith(("/", "./", "../")):
        return Path(name)
    home = directory()
    if name:
        for candidate in (home / name, home / f"{name}.toml"):
            if candidate.is_file():
                return candidate
    names = available()
    if not names:
        raise IrkError(f"no configs yet: create {home}/<name>.toml, then run: irk <name>")
    problem = f"no config named {name!r}:" if name else "which config?"
    raise IrkError(f"{problem} {home} has: {', '.join(names)}")


def load(path: Path) -> Config:
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except OSError as exc:
        raise IrkError(f"cannot read {path}: {exc.strerror}") from exc
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise IrkError(f"{path}: {exc}") from exc
    try:
        return _build(data)
    except IrkError as exc:
        raise IrkError(f"{path}: {exc}") from None


def _build(data: dict[str, Any]) -> Config:
    tls = _take(data, "tls", bool, True)
    port = _take(data, "port", int, 6697 if tls else 6667)
    if not 0 < port < 65536:
        raise IrkError("'port' must be between 1 and 65535")
    nick = _word(data, "nick")

    sasl = None
    if "sasl" in data:
        table = _take(data, "sasl", dict)
        sasl = Sasl(
            username=_word(table, "username", nick, scope="sasl."),
            password=_take(table, "password", str, scope="sasl."),
        )
        _reject_leftovers(table, scope="sasl.")

    log = None
    if "log" in data:
        try:
            log = Path(_take(data, "log", str)).expanduser()
        except RuntimeError as exc:  # ~someone, where there is no such user
            raise IrkError(f"'log': {exc}") from None
        # A relative path would start a new log wherever irk happened to be run from.
        if not log.is_absolute():
            raise IrkError("'log' must be an absolute path, or start with ~/")

    channels = _strings(data, "channels")
    if not channels:
        raise IrkError("'channels' must list at least one channel")
    for channel in channels:
        if not channel.startswith(("#", "&")) or any(c == "," or c.isspace() for c in channel):
            raise IrkError(f"'channels': {channel!r} is not a channel name like '#example'")

    config = Config(
        host=_word(data, "host"),
        port=port,
        tls=tls,
        nick=nick,
        username=_word(data, "username", nick),
        realname=_take(data, "realname", str, nick),
        channels=channels,
        domains=tuple(_domain(domain) for domain in _strings(data, "domains", required=False)),
        sasl=sasl,
        log=log,
    )
    _reject_leftovers(data)
    return config


def _take[T](
    table: dict[str, Any], key: str, kind: type[T], default: T | None = None, *, scope: str = ""
) -> T:
    """Pop `key` from a TOML table, insisting on its type. No default means it is required."""
    value = table.pop(key, default)
    if value is None:
        raise IrkError(f"'{scope}{key}' is required")
    # bool is a subclass of int, but `port = true` is still a mistake.
    if not isinstance(value, kind) or isinstance(value, bool) != (kind is bool):
        raise IrkError(f"'{scope}{key}' must be {KINDS[kind]}")
    return value


def _word(table: dict[str, Any], key: str, default: str | None = None, *, scope: str = "") -> str:
    """A string that is sent as a single IRC parameter, so cannot be empty or contain spaces."""
    value = _take(table, key, str, default, scope=scope)
    if not value or value.startswith(":") or any(char.isspace() for char in value):
        raise IrkError(f"'{scope}{key}' must be a single word")
    return value


def _strings(table: dict[str, Any], key: str, *, required: bool = True) -> tuple[str, ...]:
    values = _take(table, key, list, None if required else [])
    if not all(isinstance(value, str) for value in values):
        raise IrkError(f"'{key}' must be a list of strings")
    return tuple(values)


def _domain(value: str) -> str:
    domain = value.lower().removeprefix("*.").strip(".")
    if not domain or any(char in domain for char in "/:@ "):
        raise IrkError(f"'domains': {value!r} is not a bare domain like 'example.com'")
    return domain


def _reject_leftovers(table: dict[str, Any], *, scope: str = "") -> None:
    if table:
        raise IrkError(f"unknown key '{scope}{next(iter(table))}'")
