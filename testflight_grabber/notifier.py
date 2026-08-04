from __future__ import annotations

import json
import logging
import secrets
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

from .config import Config
from .logging_setup import log_event


class Notifier:
    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    def notify(self, event: str, message: str, title: str = "TestFlight Slot Grabber") -> str:
        notification_id = secrets.token_hex(8)
        self._dispatch(notification_id, event, message, title)
        return notification_id

    def notify_async(
        self,
        event: str,
        message: str,
        title: str = "TestFlight Slot Grabber",
        delay_seconds: float = 0.0,
    ) -> str:
        notification_id = secrets.token_hex(8)
        thread = threading.Thread(
            target=self._dispatch_after_delay,
            args=(notification_id, event, message, title, max(0.0, delay_seconds)),
            name=f"notification-{event}-{notification_id[:6]}",
            daemon=False,
        )
        log_event(
            self.logger,
            logging.DEBUG,
            "notification_queued",
            "Queued non-blocking notification dispatch",
            notification_id=notification_id,
            notification_event=event,
            thread_name=thread.name,
            delay_seconds=max(0.0, delay_seconds),
        )
        thread.start()
        return notification_id

    def _dispatch_after_delay(
        self,
        notification_id: str,
        event: str,
        message: str,
        title: str,
        delay_seconds: float,
    ) -> None:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        self._dispatch(notification_id, event, message, title)

    def _dispatch(self, notification_id: str, event: str, message: str, title: str) -> None:
        log_event(
            self.logger,
            logging.INFO,
            "notification_started",
            message,
            notification_id=notification_id,
            notification_event=event,
            title=title,
            sound_enabled=self.config.notification_sound,
            telegram_enabled=self.config.telegram_notifications,
        )
        self._macos_notification(notification_id, event, title, message)
        if self.config.notification_sound and event in {
            "slot_detected",
            "accepted",
            "automation_failed",
            "manual_action_required",
        }:
            self._sound(notification_id, event)
        if self.config.telegram_notifications:
            self._telegram(notification_id, event, message)
        log_event(
            self.logger,
            logging.INFO,
            "notification_dispatch_finished",
            "Finished dispatching configured notification channels",
            notification_id=notification_id,
            notification_event=event,
        )

    def _macos_notification(self, notification_id: str, event: str, title: str, message: str) -> None:
        started = time.monotonic()
        script = (
            "on run argv\n"
            "display notification (item 1 of argv) with title (item 2 of argv)\n"
            "end run"
        )
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script, message, title],
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            log_event(
                self.logger,
                logging.DEBUG,
                "macos_notification_completed",
                "macOS notification command completed",
                notification_id=notification_id,
                notification_event=event,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                exit_code=result.returncode,
                stderr=result.stderr,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log_event(
                self.logger,
                logging.WARNING,
                "notification_failed",
                "macOS notification failed",
                notification_id=notification_id,
                notification_event=event,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                error_type=type(exc).__name__,
                error=str(exc),
            )

    def _sound(self, notification_id: str, event: str) -> None:
        sound = "Glass" if event in {"slot_detected", "accepted"} else "Basso"
        path = f"/System/Library/Sounds/{sound}.aiff"
        try:
            process = subprocess.Popen(
                ["/usr/bin/afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            log_event(
                self.logger,
                logging.DEBUG,
                "notification_sound_started",
                "Started notification sound",
                notification_id=notification_id,
                notification_event=event,
                sound=sound,
                path=path,
                pid=process.pid,
            )
        except OSError as exc:
            log_event(
                self.logger,
                logging.WARNING,
                "notification_sound_failed",
                "Could not start notification sound",
                notification_id=notification_id,
                notification_event=event,
                sound=sound,
                error_type=type(exc).__name__,
                error=str(exc),
            )

    def _keychain_secret(self, service: str) -> Optional[str]:
        try:
            result = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-a",
                    self.config.keychain_account,
                    "-s",
                    service,
                    "-w",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    def _telegram(self, notification_id: str, event: str, message: str) -> None:
        token = self._keychain_secret(self.config.telegram_bot_token_keychain_service)
        chat_id = self._keychain_secret(self.config.telegram_chat_id_keychain_service)
        if not token or not chat_id:
            log_event(
                self.logger,
                logging.WARNING,
                "telegram_notification_skipped",
                "Telegram notification is enabled but Keychain entries are missing",
                notification_id=notification_id,
                notification_event=event,
            )
            return
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                raw = response.read(4096)
                result = json.loads(raw.decode("utf-8", "replace"))
                if not result.get("ok"):
                    raise RuntimeError("Telegram API returned ok=false")
                log_event(
                    self.logger,
                    logging.INFO,
                    "telegram_notification_completed",
                    "Telegram notification delivered",
                    notification_id=notification_id,
                    notification_event=event,
                    status_code=response.status,
                    response_bytes=len(raw),
                )
        except Exception as exc:
            # Never include the request URL or token in logs.
            log_event(
                self.logger,
                logging.WARNING,
                "telegram_notification_failed",
                "Telegram notification failed",
                notification_id=notification_id,
                notification_event=event,
                error=type(exc).__name__,
            )
