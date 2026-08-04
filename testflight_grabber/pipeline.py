from __future__ import annotations

import logging
import secrets
import time
from typing import Dict, Optional

from .automation import AutomationError, MacOSAutomation
from .config import Config
from .logging_setup import log_event
from .models import AcceptanceResult
from .notifier import Notifier


class AcceptancePipeline:
    def __init__(self, config: Config, logger: logging.Logger, notifier: Notifier) -> None:
        self.config = config
        self.logger = logger
        self.notifier = notifier
        self.automation = MacOSAutomation(config, logger)

    def run(
        self,
        dry_run: bool = False,
        mock: bool = False,
        trigger_id: Optional[str] = None,
        invitation_dispatch: Optional[Dict[str, object]] = None,
    ) -> AcceptanceResult:
        attempt_id = secrets.token_hex(8)
        started = time.monotonic()
        effective_dry_run = dry_run or self.config.dry_run
        prefix = "[MOCK] " if mock else ""
        acceptance_started_notification_id = self.notifier.notify_async(
            "acceptance_started",
            f"{prefix}Запущена попытка принятия {self.config.expected_app_name} в TestFlight.",
            delay_seconds=3.0,
        )
        log_event(
            self.logger,
            logging.INFO,
            "acceptance_pipeline_started",
            "Acceptance pipeline started",
            attempt_id=attempt_id,
            trigger_id=trigger_id,
            dry_run=effective_dry_run,
            mock=mock,
            deep_link=self.config.deep_link,
            accept_timeout_seconds=self.config.accept_timeout_seconds,
            install_timeout_seconds=self.config.install_timeout_seconds,
            install_after_accept=self.config.install_after_accept,
            ax_binary=str(self.config.ax_binary),
            acceptance_started_notification_id=acceptance_started_notification_id,
            invitation_predispatched=bool(invitation_dispatch),
            invitation_dispatch=invitation_dispatch,
        )

        if effective_dry_run:
            details: Dict[str, object] = {
                "deep_link": self.config.deep_link,
                "ax_binary_ready": self.config.ax_binary.exists(),
                "would_press": ["Accept", "Join", "Принять", "Присоединиться"],
                "would_install": self.config.install_after_accept,
            }
            dry_run_notification_id = self.notifier.notify(
                "dry_run_complete", "Dry-run завершён: внешние кнопки не нажимались."
            )
            result = AcceptanceResult(
                success=True,
                accepted=False,
                installed=False,
                dry_run=True,
                reason="dry-run pipeline validated without external actions",
                details=details,
            )
            log_event(
                self.logger,
                logging.INFO,
                "acceptance_pipeline_completed",
                "Dry-run acceptance pipeline completed",
                attempt_id=attempt_id,
                trigger_id=trigger_id,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                result=result.to_dict(),
                notification_id=dry_run_notification_id,
            )
            return result

        mock_process = None
        open_details = invitation_dispatch
        try:
            if mock:
                mock_process = self.automation.launch_mock()
            elif open_details is None:
                open_details = self.automation.open_invitation()

            accepted = self.automation.accept(mock=mock)
            accept_notification_id = self.notifier.notify_async(
                "accept_pressed", f"{prefix}Кнопка принятия приглашения нажата и переход подтверждён."
            )
            log_event(
                self.logger,
                logging.INFO,
                "accept_pressed",
                "Accept/Join button pressed",
                attempt_id=attempt_id,
                trigger_id=trigger_id,
                response=accepted,
                notification_id=accept_notification_id,
            )

            installed = False
            install_details: Dict[str, object] = {}
            status_before = accepted.get("status_before", {})
            incompatible_mac = isinstance(status_before, dict) and bool(status_before.get("incompatible_mac"))
            if self.config.install_after_accept and incompatible_mac:
                install_details = {
                    "skipped": True,
                    "reason": "accepted build is iOS-only and incompatible with this Mac",
                    "iphone_required": True,
                }
                log_event(
                    self.logger,
                    logging.INFO,
                    "install_skipped_incompatible_mac",
                    "Invitation accepted; Mac installation skipped because this is an iOS-only build",
                    attempt_id=attempt_id,
                    trigger_id=trigger_id,
                    status_before=status_before,
                )
            elif self.config.install_after_accept:
                try:
                    install_details = self.automation.install(mock=mock)
                    installed = True
                    install_notification_id = self.notifier.notify_async(
                        "install_started", f"{prefix}Кнопка установки нажата и переход подтверждён."
                    )
                    log_event(
                        self.logger,
                        logging.INFO,
                        "install_completed",
                        "Install/Update button pressed and transition verified",
                        attempt_id=attempt_id,
                        trigger_id=trigger_id,
                        response=install_details,
                        notification_id=install_notification_id,
                    )
                except AutomationError as install_error:
                    # Joining is the scarce operation. Missing Install is expected for an iOS-only build on Mac.
                    install_details = {
                        "skipped": True,
                        "reason": str(install_error),
                        "exit_code": install_error.exit_code,
                    }
                    log_event(
                        self.logger,
                        logging.WARNING,
                        "install_not_completed",
                        "Invitation was accepted but installation was not completed on this Mac",
                        attempt_id=attempt_id,
                        trigger_id=trigger_id,
                        **install_details,
                    )

            accepted_message = (
                "[MOCK] Тестовый поток Accept → Install завершён."
                if mock
                else f"Приглашение {self.config.expected_app_name} принято; проверьте TestFlight на целевом устройстве."
            )
            accepted_notification_id = self.notifier.notify_async("accepted", accepted_message)
            post_accept_artifacts = (
                self.automation.capture_failure_artifacts(
                    "post-accept state",
                    mock=True,
                    stage="post-accept",
                )
                if mock
                else {"deferred_until_after_durable_acceptance": True}
            )
            result = AcceptanceResult(
                success=True,
                accepted=True,
                installed=installed,
                dry_run=False,
                reason="mock acceptance flow verified" if mock else "invitation acceptance button was pressed and transition verified",
                details={
                    "attempt_id": attempt_id,
                    "trigger_id": trigger_id,
                    "accept": accepted,
                    "invitation_open": open_details,
                    "install": install_details,
                    "post_accept_artifacts": post_accept_artifacts,
                    "accepted_notification_id": accepted_notification_id,
                },
            )
            log_event(
                self.logger,
                logging.INFO,
                "acceptance_pipeline_completed",
                "Acceptance pipeline completed successfully",
                attempt_id=attempt_id,
                trigger_id=trigger_id,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                result=result.to_dict(),
            )
            return result
        except AutomationError as exc:
            artifacts = self.automation.capture_failure_artifacts_async(str(exc), mock=mock, stage="failure")
            details = {
                "attempt_id": attempt_id,
                "trigger_id": trigger_id,
                "exit_code": exc.exit_code,
                "ax": exc.details,
                "artifacts": artifacts,
                "invitation_open": open_details,
            }
            failure_notification_id = self.notifier.notify_async(
                "automation_failed", f"Автоматизация TestFlight не сработала: {exc}"
            )
            result = AcceptanceResult(
                success=False,
                accepted=False,
                installed=False,
                dry_run=False,
                reason=str(exc),
                details=details,
            )
            log_event(
                self.logger,
                logging.ERROR,
                "acceptance_pipeline_failed",
                "Acceptance pipeline failed",
                error=str(exc),
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                result=result.to_dict(),
                notification_id=failure_notification_id,
                **details,
            )
            return result
        finally:
            if mock_process is not None and mock_process.poll() is None:
                mock_process.terminate()
                try:
                    mock_process.wait(timeout=2)
                    log_event(
                        self.logger,
                        logging.INFO,
                        "mock_app_terminated",
                        "Terminated local TestFlight AX mock",
                        attempt_id=attempt_id,
                        pid=mock_process.pid,
                        exit_code=mock_process.returncode,
                    )
                except Exception as exc:
                    log_event(
                        self.logger,
                        logging.WARNING,
                        "mock_app_termination_failed",
                        "Could not confirm TestFlight AX mock termination",
                        attempt_id=attempt_id,
                        pid=mock_process.pid,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
