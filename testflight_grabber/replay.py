from __future__ import annotations

from .config import Config


class ReplayUnavailable(RuntimeError):
    pass


def experimental_replay(config: Config) -> None:
    """Fail closed until a first-party request has been observed and validated locally."""
    if not config.experimental_replay_enabled:
        raise ReplayUnavailable("experimental API replay is disabled in config.json")
    raise ReplayUnavailable(
        "no stable first-party acceptance request has been captured; replay remains intentionally unimplemented"
    )
