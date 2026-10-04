"""The `irk` command: load the config, then stay connected until interrupted."""

import argparse
import asyncio
import os
import signal
import ssl
from importlib.metadata import version

from irk import IrkError, client, config
from irk.ui import UI

MIN_BACKOFF = 2
MAX_BACKOFF = 60
# A connection that survives this long counts as healthy, so the next retry is quick again.
STABLE_AFTER = 60


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="irk",
        description="Watch IRC channels and print the links you care about.",
        epilog="`irk libera` reads ~/.config/irk/libera.toml. "
        "A config starting with /, ./ or ../ is read as a path instead.",
    )
    parser.add_argument("config", nargs="?", help="name of a config in ~/.config/irk")
    parser.add_argument("-q", "--quiet", action="store_true", help="print links and nothing else")
    parser.add_argument("--debug", action="store_true", help="show the raw IRC conversation")
    parser.add_argument("--version", action="version", version=f"irk {version('irk')}")
    args = parser.parse_args()

    ui = UI(quiet=args.quiet, debug=args.debug)
    try:
        path = config.find(args.config)
        cfg = config.load(path)
        ui.channel_width = max(len(channel) for channel in cfg.channels)
        if cfg.sasl and not cfg.tls:
            ui.warn("tls is off, so your SASL password will be sent in plain text")
        if cfg.sasl and os.name == "posix" and path.stat().st_mode & 0o077:
            ui.warn(f"{path} holds a password but other users can read it: chmod 600 {path}")
        ui.status(f"watching for links to {', '.join(cfg.domains) or 'anywhere'}")
        if cfg.log:
            ui.log_to(cfg.log)
            ui.status(f"logging links to {cfg.log}")
        asyncio.run(watch(cfg, ui))
    except IrkError as exc:
        ui.error(str(exc))
        return 1
    except KeyboardInterrupt:
        ui.interrupted()
        return 130
    return 0


async def watch(cfg: config.Config, ui: UI) -> None:
    """Stay connected for good, backing off between reconnection attempts."""
    loop = asyncio.get_running_loop()
    if os.name == "posix":
        # Ctrl-C makes asyncio cancel us, and the client says QUIT on its way out. Being killed,
        # or having the terminal closed, should end the same way rather than drop the connection.
        for signum in (signal.SIGTERM, signal.SIGHUP):
            loop.add_signal_handler(signum, signal.raise_signal, signal.SIGINT)
    delay = MIN_BACKOFF
    while True:
        started = loop.time()
        try:
            await client.run(cfg, ui)
        except ssl.SSLCertVerificationError as exc:
            # Retrying will not make a bad certificate good.
            reason = f"cannot verify the certificate of {cfg.host}: {exc.verify_message}"
            raise IrkError(reason) from exc
        except OSError as exc:
            ui.warn(exc.strerror or str(exc))
        if loop.time() - started > STABLE_AFTER:
            delay = MIN_BACKOFF
        ui.status(f"reconnecting in {delay}s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_BACKOFF)
