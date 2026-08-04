from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"
MIN_POLL_INTERVAL_SECONDS = 0.25


def _expand(value: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None else float(raw)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    target_url: str
    join_code: str
    expected_app_name: str
    deep_link: str
    prewarm_testflight: bool
    interval_seconds: float
    jitter_seconds: float
    request_timeout_seconds: float
    range_probe_bytes: int
    confirm_min_delay_ms: int
    confirm_max_delay_ms: int
    backoff_initial_seconds: float
    backoff_max_seconds: float
    acceptance_retry_cooldown_seconds: float
    accept_timeout_seconds: float
    install_timeout_seconds: float
    install_after_accept: bool
    dry_run: bool
    data_dir: Path
    log_dir: Path
    ax_binary: Path
    notification_sound: bool
    telegram_notifications: bool
    telegram_chat_id_keychain_service: str
    telegram_bot_token_keychain_service: str
    keychain_account: str
    experimental_replay_enabled: bool

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "monitor.lock"

    @property
    def pid_path(self) -> Path:
        return self.data_dir / "monitor.pid"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        config_path = path or Path(os.environ.get("TESTFLIGHT_CONFIG", DEFAULT_CONFIG_PATH))
        with config_path.expanduser().open("r", encoding="utf-8") as handle:
            raw: Dict[str, Any] = json.load(handle)

        monitor = raw.get("monitor", {})
        automation = raw.get("automation", {})
        paths = raw.get("paths", {})
        notifications = raw.get("notifications", {})
        telegram = notifications.get("telegram", {})
        replay = raw.get("experimental_api_replay", {})

        cfg = cls(
            target_url=os.environ.get("TESTFLIGHT_URL", raw["target_url"]),
            join_code=str(raw["join_code"]),
            expected_app_name=str(raw["expected_app_name"]),
            deep_link=str(automation.get("deep_link", f"itms-beta://testflight.apple.com/join/{raw['join_code']}")),
            prewarm_testflight=bool(automation.get("prewarm_testflight", True)),
            interval_seconds=_float_env("TESTFLIGHT_INTERVAL_SECONDS", float(monitor.get("interval_seconds", 7.0))),
            jitter_seconds=_float_env("TESTFLIGHT_JITTER_SECONDS", float(monitor.get("jitter_seconds", 1.5))),
            request_timeout_seconds=float(monitor.get("request_timeout_seconds", 12.0)),
            range_probe_bytes=int(monitor.get("range_probe_bytes", 3072)),
            confirm_min_delay_ms=int(monitor.get("confirm_min_delay_ms", 300)),
            confirm_max_delay_ms=int(monitor.get("confirm_max_delay_ms", 900)),
            backoff_initial_seconds=float(monitor.get("backoff_initial_seconds", 15.0)),
            backoff_max_seconds=float(monitor.get("backoff_max_seconds", 300.0)),
            acceptance_retry_cooldown_seconds=float(monitor.get("acceptance_retry_cooldown_seconds", 45.0)),
            accept_timeout_seconds=float(automation.get("accept_timeout_seconds", 12.0)),
            install_timeout_seconds=float(automation.get("install_timeout_seconds", 30.0)),
            install_after_accept=bool(automation.get("install_after_accept", True)),
            dry_run=_bool_env("TESTFLIGHT_DRY_RUN", bool(raw.get("dry_run", False))),
            data_dir=_expand(
                os.environ.get(
                    "TESTFLIGHT_DATA_DIR",
                    paths.get("data_dir", "~/Library/Application Support/TestFlightSlotGrabber"),
                )
            ),
            log_dir=_expand(
                os.environ.get(
                    "TESTFLIGHT_LOG_DIR",
                    paths.get("log_dir", "~/Library/Logs/TestFlightSlotGrabber"),
                )
            ),
            ax_binary=_expand(paths.get("ax_binary", ".build/release/testflight-ax")),
            notification_sound=bool(notifications.get("sound", True)),
            telegram_notifications=bool(telegram.get("enabled", False)),
            telegram_chat_id_keychain_service=str(telegram.get("chat_id_keychain_service", "testflight-slot-grabber-chat-id")),
            telegram_bot_token_keychain_service=str(telegram.get("bot_token_keychain_service", "testflight-slot-grabber-bot-token")),
            keychain_account=str(telegram.get("keychain_account", os.environ.get("USER", "local"))),
            experimental_replay_enabled=bool(replay.get("enabled", False)),
        )
        cfg.validate()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        return cfg

    def validate(self) -> None:
        target = urlparse(self.target_url)
        expected_path = f"/join/{self.join_code}"
        if target.scheme != "https" or target.netloc != "testflight.apple.com" or target.path.rstrip("/") != expected_path:
            raise ValueError("target_url and join_code must identify the same HTTPS public TestFlight invitation")
        deep_link = urlparse(self.deep_link)
        if deep_link.scheme != "itms-beta" or deep_link.netloc != "testflight.apple.com" or deep_link.path.rstrip("/") != expected_path:
            raise ValueError("automation.deep_link must identify the configured TestFlight invitation")
        if self.interval_seconds < MIN_POLL_INTERVAL_SECONDS:
            raise ValueError(f"interval_seconds must be at least {MIN_POLL_INTERVAL_SECONDS} seconds")
        if self.jitter_seconds < 0:
            raise ValueError("jitter_seconds cannot be negative")
        if self.interval_seconds - self.jitter_seconds < MIN_POLL_INTERVAL_SECONDS:
            raise ValueError(
                "interval_seconds - jitter_seconds must be at least "
                f"{MIN_POLL_INTERVAL_SECONDS} seconds"
            )
        if not 1024 <= self.range_probe_bytes <= 32768:
            raise ValueError("range_probe_bytes must stay within 1024..32768 bytes")
        if not 300 <= self.confirm_min_delay_ms <= self.confirm_max_delay_ms <= 1000:
            raise ValueError("confirmation delay must stay within 300..1000 ms")
