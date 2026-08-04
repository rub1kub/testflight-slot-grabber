from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import random
import secrets
import signal
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from .config import Config, MIN_POLL_INTERVAL_SECONDS
from .http_client import ResponseTooLarge, TestFlightHttpClient
from .lock import InstanceLock
from .logging_setup import log_event, log_exception, sanitize_for_log
from .models import HttpResponse, PageObservation, PageState, utc_now
from .notifier import Notifier
from .parser import classify_page, network_error_observation
from .pipeline import AcceptancePipeline
from .state_store import StateStore


class Monitor:
    AUTOMATION_READINESS_INTERVAL_SECONDS = 60.0

    BACKOFF_STATES = {
        PageState.NETWORK_ERROR,
        PageState.RATE_LIMITED,
        PageState.UNEXPECTED_RESPONSE,
    }

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.client = TestFlightHttpClient(config.request_timeout_seconds, logger=logger)
        self.store = StateStore(config.state_path)
        self.notifier = Notifier(config, logger)
        self.pipeline = AcceptancePipeline(config, logger, self.notifier)
        self.stopping = False
        self.network_failures = 0
        self.check_sequence = 0
        self.last_automation_readiness_check_monotonic = 0.0

    def _handle_signal(self, signum: int, frame: object) -> None:
        del frame
        self.stopping = True
        log_event(self.logger, logging.INFO, "monitor_stopping", "Monitor received stop signal", signal=signum)

    def _fixture_response(self, fixture: Path) -> HttpResponse:
        body = fixture.read_text(encoding="utf-8")
        return HttpResponse(
            status_code=200,
            final_url=self.config.target_url,
            headers={"content-type": "text/html;charset=utf-8", "x-fixture": fixture.name},
            body=body,
            elapsed_ms=0,
            diagnostics={
                "request_id": f"fixture-{secrets.token_hex(6)}",
                "fixture": str(fixture),
                "wire_bytes": len(body.encode("utf-8")),
                "decoded_bytes": len(body.encode("utf-8")),
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "range_probe_bytes": None,
            },
        )

    def check_once(
        self,
        fixture: Optional[Path] = None,
        full_response: bool = False,
        check_id: Optional[str] = None,
        phase: str = "poll",
    ) -> Tuple[PageObservation, Optional[HttpResponse]]:
        effective_check_id = check_id or secrets.token_hex(8)
        started = time.monotonic()
        log_event(
            self.logger,
            logging.DEBUG,
            "page_check_started",
            "Starting TestFlight page classification check",
            check_id=effective_check_id,
            phase=phase,
            full_response=full_response,
            fixture=str(fixture) if fixture else None,
            range_probe_bytes=None if full_response else self.config.range_probe_bytes,
        )
        try:
            response = (
                self._fixture_response(fixture)
                if fixture
                else self.client.fetch(
                    self.config.target_url,
                    range_probe_bytes=None if full_response else self.config.range_probe_bytes,
                )
            )
            observation = classify_page(
                response.status_code,
                response.final_url,
                response.body,
                response.headers,
                self.config.expected_app_name,
                self.config.join_code,
                response.elapsed_ms,
            )
            log_event(
                self.logger,
                logging.DEBUG,
                "page_classified",
                "Classified TestFlight page response",
                check_id=effective_check_id,
                phase=phase,
                full_response=full_response,
                classification=observation.to_dict(),
                response_request_id=response.diagnostics.get("request_id"),
                response_body_sha256=response.diagnostics.get("body_sha256"),
                response_wire_bytes=response.diagnostics.get("wire_bytes"),
                connection_reused=response.diagnostics.get("connection_reused"),
                classification_elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            )
            if not fixture and not full_response and observation.state == PageState.UNEXPECTED_RESPONSE:
                log_event(
                    self.logger,
                    logging.WARNING,
                    "range_probe_fallback",
                    "Range probe was inconclusive; retrying once with a full response",
                    check_id=effective_check_id,
                    phase=phase,
                    status_code=response.status_code,
                    content_range=response.headers.get("content-range"),
                    signals=observation.signals,
                )
                return self.check_once(
                    full_response=True,
                    check_id=effective_check_id,
                    phase="range_fallback_full",
                )
            return observation, response
        except (urllib.error.URLError, TimeoutError, OSError, ResponseTooLarge) as exc:
            log_exception(
                self.logger,
                "page_check_failed",
                "TestFlight page check failed",
                check_id=effective_check_id,
                phase=phase,
                full_response=full_response,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            observation = network_error_observation(self.config.target_url, f"{type(exc).__name__}: {exc}")
            log_event(
                self.logger,
                logging.WARNING,
                "page_classified",
                "Classified failed page check as network_error",
                check_id=effective_check_id,
                phase=phase,
                classification=observation.to_dict(),
            )
            return observation, None

    def _snapshot_response(
        self,
        response: Optional[HttpResponse],
        category: str,
        observation: Optional[PageObservation] = None,
    ) -> Optional[str]:
        if not response:
            return None
        safe_category = "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in category)
        snapshot_dir = self.config.log_dir / "html-snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        basename = f"{safe_category}-{stamp}-{secrets.token_hex(3)}"
        path = snapshot_dir / f"{basename}.html"
        metadata_path = snapshot_dir / f"{basename}.json"
        path.write_text(response.body, encoding="utf-8")
        metadata = {
            "category": category,
            "saved_at": utc_now(),
            "html_path": str(path),
            "status_code": response.status_code,
            "final_url": response.final_url,
            "headers": response.headers,
            "diagnostics": response.diagnostics,
            "observation": observation.to_dict() if observation else None,
        }
        metadata_path.write_text(
            json.dumps(sanitize_for_log(metadata), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        log_event(
            self.logger,
            logging.INFO,
            "html_snapshot_saved",
            "Saved TestFlight HTML and response metadata",
            category=category,
            html_path=str(path),
            metadata_path=str(metadata_path),
            html_bytes=path.stat().st_size,
            html_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            request_id=response.diagnostics.get("request_id"),
        )
        return str(path)

    def _record_observation(self, observation: PageObservation, response: Optional[HttpResponse]) -> None:
        state = self.store.load()
        previous = state.get("last_state")
        state_changed = previous != observation.state.value
        body_sha256 = response.diagnostics.get("body_sha256") if response else None
        previous_body_sha256 = state.get("last_body_sha256")
        body_changed = bool(body_sha256 and body_sha256 != previous_body_sha256)
        snapshot_saved = False
        state["last_check_at"] = observation.checked_at
        state["last_state"] = observation.state.value
        state["last_observation"] = observation.to_dict()
        if body_sha256:
            state["last_body_sha256"] = body_sha256
        if state_changed:
            state["last_state_change_at"] = observation.checked_at
            log_event(
                self.logger,
                logging.INFO,
                "state_changed",
                f"TestFlight state changed: {previous or 'unknown'} -> {observation.state.value}",
                previous=previous,
                current=observation.state.value,
                reason=observation.reason,
                signals=observation.signals,
                observation=observation.to_dict(),
                response_diagnostics=response.diagnostics if response else None,
            )
            self._snapshot_response(response, f"state-{observation.state.value}", observation)
        else:
            log_event(
                self.logger,
                logging.DEBUG,
                "check_complete",
                f"State remains {observation.state.value}",
                state=observation.state.value,
                elapsed_ms=observation.elapsed_ms,
                status_code=observation.status_code,
                body_bytes=observation.body_bytes,
                reason=observation.reason,
                app_name=observation.app_name,
                final_url=observation.final_url,
                signals=observation.signals,
                response_request_id=response.diagnostics.get("request_id") if response else None,
                response_body_sha256=response.diagnostics.get("body_sha256") if response else None,
            )
        if body_changed:
            log_event(
                self.logger,
                logging.INFO,
                "response_body_changed",
                "TestFlight response body hash changed",
                previous_body_sha256=previous_body_sha256,
                current_body_sha256=body_sha256,
                state=observation.state.value,
                request_id=response.diagnostics.get("request_id") if response else None,
            )
            if not state_changed:
                snapshot_saved = bool(self._snapshot_response(response, "body-changed", observation))
        snapshot_epoch = float(state.get("last_unexpected_snapshot_epoch", 0.0) or 0.0)
        if observation.state == PageState.UNEXPECTED_RESPONSE and (
            previous != PageState.UNEXPECTED_RESPONSE.value or time.time() - snapshot_epoch >= 3600
        ):
            snapshot = self._snapshot_response(response, "unexpected", observation)
            if snapshot:
                snapshot_saved = True
                state["last_unexpected_html"] = snapshot
                state["last_unexpected_snapshot_epoch"] = time.time()
                log_event(self.logger, logging.WARNING, "unexpected_html_saved", "Saved unexpected response HTML", path=snapshot)
        state.pop("last_acceptance_attempt_monotonic", None)
        # A routine same-state heartbeat is atomically replaced but does not
        # force the SSD to sync on every subsecond check. State transitions,
        # diagnostic snapshots and acceptance records remain durable.
        self.store.save(state, durable=state_changed or snapshot_saved)

    def _handle_available(
        self,
        first: PageObservation,
        fixture: Optional[Path],
        force_dry_run: bool,
        mock_automation: bool = False,
    ) -> None:
        availability_id = secrets.token_hex(8)
        detected_monotonic = time.monotonic()
        effective_dry_run = force_dry_run or self.config.dry_run
        invitation_dispatch = None
        if not fixture and not mock_automation and not effective_dry_run:
            try:
                # Opening the invitation is harmless and reversible. Dispatch it
                # on the first strong signal so TestFlight can refresh while the
                # mandatory independent HTTP confirmation is still in flight.
                # AXPress remains gated on the confirmation below.
                invitation_dispatch = self.pipeline.automation.open_invitation()
            except Exception as exc:
                log_event(
                    self.logger,
                    logging.WARNING,
                    "testflight_predispatch_failed",
                    "Early TestFlight refresh failed; the confirmed pipeline will retry it",
                    availability_id=availability_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        minimum_confirmation_age_ms = random.randint(
            self.config.confirm_min_delay_ms,
            self.config.confirm_max_delay_ms,
        )
        log_event(
            self.logger,
            logging.INFO,
            "availability_confirmation",
            "Starting an independent cache-busted confirmation while the press gate remains closed",
            availability_id=availability_id,
            minimum_confirmation_age_ms=minimum_confirmation_age_ms,
            first_observation=first.to_dict(),
            invitation_dispatch=invitation_dispatch,
        )
        confirmation_request_started_monotonic = time.monotonic()
        confirmed, response = self.check_once(
            fixture,
            full_response=False,
            check_id=availability_id,
            phase="availability_confirmation",
        )
        confirmation_response_monotonic = time.monotonic()
        press_gate_wait_seconds = 0.0
        if confirmed.state == PageState.AVAILABLE:
            press_gate_wait_seconds = max(
                0.0,
                minimum_confirmation_age_ms / 1000.0
                - (confirmation_response_monotonic - detected_monotonic),
            )
            if press_gate_wait_seconds > 0:
                time.sleep(press_gate_wait_seconds)
        log_event(
            self.logger,
            logging.INFO,
            "availability_confirmed" if confirmed.state == PageState.AVAILABLE else "availability_rejected",
            f"Confirmation state: {confirmed.state.value}",
            availability_id=availability_id,
            observation=confirmed.to_dict(),
            response_diagnostics=response.diagnostics if response else None,
            confirmation_request_started_after_detection_ms=round(
                (confirmation_request_started_monotonic - detected_monotonic) * 1000,
                3,
            ),
            confirmation_response_after_detection_ms=round(
                (confirmation_response_monotonic - detected_monotonic) * 1000,
                3,
            ),
            press_gate_wait_ms=round(press_gate_wait_seconds * 1000, 3),
            detection_to_confirmation_ms=round((time.monotonic() - detected_monotonic) * 1000, 3),
        )
        if confirmed.state != PageState.AVAILABLE:
            self._record_observation(confirmed, response)
            return

        self._snapshot_response(response, "available-confirmed", confirmed)

        persisted = self.store.load()
        if persisted.get("accepted"):
            log_event(
                self.logger,
                logging.INFO,
                "acceptance_suppressed",
                "Invitation was already marked accepted",
                availability_id=availability_id,
                accepted_at=persisted.get("accepted_at"),
            )
            return
        last_attempt = float(persisted.get("last_acceptance_attempt_epoch", 0.0) or 0.0)
        if last_attempt and time.time() - last_attempt < self.config.acceptance_retry_cooldown_seconds:
            log_event(
                self.logger,
                logging.WARNING,
                "acceptance_cooldown",
                "Acceptance attempt suppressed by cooldown",
                availability_id=availability_id,
                last_attempt_epoch=last_attempt,
                seconds_since_last_attempt=round(time.time() - last_attempt, 3),
                cooldown_seconds=self.config.acceptance_retry_cooldown_seconds,
            )
            return

        slot_notification_id = self.notifier.notify_async(
            "slot_detected",
            f"Доступность {self.config.expected_app_name} в TestFlight подтверждена двумя проверками.",
            delay_seconds=3.0,
        )
        log_event(
            self.logger,
            logging.DEBUG,
            "availability_notification_queued",
            "Queued non-blocking slot notification without delaying acceptance",
            availability_id=availability_id,
            notification_id=slot_notification_id,
        )
        persisted["last_acceptance_attempt_at"] = utc_now()
        persisted["last_acceptance_attempt_epoch"] = time.time()
        persisted["last_availability_id"] = availability_id
        self.store.save(persisted)
        result = self.pipeline.run(
            dry_run=force_dry_run,
            mock=mock_automation,
            trigger_id=availability_id,
            invitation_dispatch=invitation_dispatch,
        )
        persisted = self.store.load()
        persisted["last_acceptance_result"] = result.to_dict()
        if result.accepted:
            persisted["accepted"] = True
            persisted["accepted_at"] = result.completed_at
        self.store.save(persisted)
        if result.accepted and not mock_automation:
            post_accept_artifacts = self.pipeline.automation.capture_failure_artifacts(
                "post-accept state after durable acceptance record",
                mock=False,
                stage="post-accept",
            )
            persisted = self.store.load()
            persisted["last_post_accept_artifacts"] = post_accept_artifacts
            self.store.save(persisted)
        log_event(
            self.logger,
            logging.INFO if result.success else logging.ERROR,
            "availability_pipeline_finished",
            "Availability-triggered acceptance pipeline finished",
            availability_id=availability_id,
            result=result.to_dict(),
        )

    def _runtime_context(self) -> dict:
        helper_sha256 = None
        try:
            helper_sha256 = hashlib.sha256(self.config.ax_binary.read_bytes()).hexdigest()
        except OSError:
            pass
        return {
            "pid": os.getpid(),
            "argv": sys.argv,
            "python": platform.python_version(),
            "machine": platform.machine(),
            "macos": platform.mac_ver()[0],
            "target_url": self.config.target_url,
            "expected_app_name": self.config.expected_app_name,
            "interval_seconds": self.config.interval_seconds,
            "jitter_seconds": self.config.jitter_seconds,
            "range_probe_bytes": self.config.range_probe_bytes,
            "request_timeout_seconds": self.config.request_timeout_seconds,
            "confirmation_delay_ms": [self.config.confirm_min_delay_ms, self.config.confirm_max_delay_ms],
            "backoff_seconds": [self.config.backoff_initial_seconds, self.config.backoff_max_seconds],
            "accept_timeout_seconds": self.config.accept_timeout_seconds,
            "install_timeout_seconds": self.config.install_timeout_seconds,
            "acceptance_retry_cooldown_seconds": self.config.acceptance_retry_cooldown_seconds,
            "dry_run": self.config.dry_run,
            "prewarm_testflight": self.config.prewarm_testflight,
            "ax_binary": str(self.config.ax_binary),
            "ax_binary_sha256": helper_sha256,
            "data_dir": str(self.config.data_dir),
            "log_dir": str(self.config.log_dir),
        }

    def _check_automation_readiness(
        self,
        *,
        source: str,
        max_attempts: int = 1,
        retry_delay_seconds: float = 0.5,
    ) -> bool:
        self.last_automation_readiness_check_monotonic = time.monotonic()
        readiness_error: Optional[Exception] = None
        permission: Optional[dict] = None
        successful_attempt = 0
        for readiness_attempt in range(1, max_attempts + 1):
            try:
                permission = self.pipeline.automation.permission()
                readiness_error = None
                successful_attempt = readiness_attempt
                break
            except Exception as exc:
                readiness_error = exc
                if readiness_attempt < max_attempts:
                    log_event(
                        self.logger,
                        logging.WARNING,
                        "automation_readiness_retry",
                        "Accessibility readiness check failed transiently; retrying",
                        source=source,
                        attempt=readiness_attempt,
                        max_attempts=max_attempts,
                        retry_delay_ms=round(retry_delay_seconds * 1000),
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    time.sleep(retry_delay_seconds)

        checked_at = utc_now()
        ui_probe: Optional[dict] = None
        ui_probe_error: Optional[Exception] = None
        if readiness_error is None and permission is not None and source == "startup":
            try:
                ui_probe = self.pipeline.automation.status()
            except Exception as exc:
                ui_probe_error = exc
        state = self.store.load()
        previous_ready = state.get("automation_ready")
        ready = readiness_error is None and permission is not None
        state["automation_ready"] = ready
        state["automation_readiness_checked_at"] = checked_at
        state["automation_readiness_source"] = source
        state["automation_readiness_error"] = None if ready else str(readiness_error)
        if source == "startup":
            state["automation_ui_probe_checked_at"] = checked_at
            state["automation_ui_probe"] = ui_probe
            state["automation_ui_probe_error"] = str(ui_probe_error) if ui_probe_error else None
        try:
            state["automation_helper_sha256"] = hashlib.sha256(self.config.ax_binary.read_bytes()).hexdigest()
        except OSError:
            state["automation_helper_sha256"] = None
        if previous_ready is not ready:
            state["automation_readiness_changed_at"] = checked_at
        self.store.save(state, durable=previous_ready is not ready)

        if ready:
            log_event(
                self.logger,
                logging.INFO,
                "automation_readiness",
                "Verified Accessibility readiness from the running monitor context",
                source=source,
                attempt=successful_attempt,
                previous_ready=previous_ready,
                permission=permission,
            )
            if source == "startup":
                log_event(
                    self.logger,
                    logging.INFO if ui_probe_error is None else logging.WARNING,
                    "automation_ui_probe" if ui_probe_error is None else "automation_ui_probe_failed",
                    "Read TestFlight Accessibility status from the running monitor context"
                    if ui_probe_error is None
                    else "Accessibility permission is ready, but the startup TestFlight UI probe failed",
                    source=source,
                    status=ui_probe,
                    error_type=type(ui_probe_error).__name__ if ui_probe_error else None,
                    error=str(ui_probe_error) if ui_probe_error else None,
                )
            return True

        log_event(
            self.logger,
            logging.WARNING,
            "automation_readiness_failed",
            "Accessibility readiness check failed from the running monitor context",
            source=source,
            attempts=max_attempts,
            previous_ready=previous_ready,
            error_type=type(readiness_error).__name__ if readiness_error else None,
            error=str(readiness_error) if readiness_error else "unknown readiness failure",
        )
        return False

    def run(
        self,
        once: bool = False,
        fixture: Optional[Path] = None,
        force_dry_run: bool = False,
        mock_automation: bool = False,
    ) -> int:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        try:
            with InstanceLock(self.config.lock_path, self.config.pid_path):
                runtime_context = self._runtime_context()
                log_event(
                    self.logger,
                    logging.INFO,
                    "monitor_started",
                    "TestFlight monitor started",
                    interval_seconds=self.config.interval_seconds,
                    jitter_seconds=self.config.jitter_seconds,
                    range_probe_bytes=self.config.range_probe_bytes,
                    fixture=str(fixture) if fixture else None,
                    dry_run=force_dry_run or self.config.dry_run,
                    mock_automation=mock_automation,
                    runtime=runtime_context,
                    log_policy={
                        "structured_events": "all HTTP checks, classifications, sleeps, transitions, AX calls and pipeline results",
                        "secrets": "redacted by key; cookies, authorization, passwords and tokens are never emitted",
                        "html": "saved on state changes, confirmed availability and unexpected responses",
                    },
                )
                log_event(
                    self.logger,
                    logging.INFO,
                    "instance_lock_acquired",
                    "Acquired exclusive monitor lock",
                    lock_path=str(self.config.lock_path),
                    pid_path=str(self.config.pid_path),
                    pid=os.getpid(),
                )
                if self.config.prewarm_testflight and not fixture:
                    try:
                        self.pipeline.automation.prewarm_testflight()
                    except Exception as exc:
                        log_event(
                            self.logger,
                            logging.WARNING,
                            "testflight_prewarm_nonfatal",
                            "Monitor will continue although TestFlight prewarm failed",
                            error_type=type(exc).__name__,
                            error=str(exc),
                        )
                if not fixture:
                    self._check_automation_readiness(source="startup", max_attempts=4)
                while not self.stopping:
                    self.check_sequence += 1
                    iteration_started = time.monotonic()
                    iteration_id = f"poll-{self.check_sequence}-{secrets.token_hex(4)}"
                    observation, response = self.check_once(
                        fixture,
                        check_id=iteration_id,
                        phase="poll",
                    )
                    self._record_observation(observation, response)
                    if (
                        not fixture
                        and time.monotonic() - self.last_automation_readiness_check_monotonic
                        >= self.AUTOMATION_READINESS_INTERVAL_SECONDS
                    ):
                        self._check_automation_readiness(source="periodic")
                    if observation.state == PageState.AVAILABLE:
                        self.network_failures = 0
                        self._handle_available(observation, fixture, force_dry_run, mock_automation)
                    elif observation.state in self.BACKOFF_STATES:
                        self.network_failures += 1
                    else:
                        self.network_failures = 0

                    if once:
                        log_event(
                            self.logger,
                            logging.INFO,
                            "monitor_once_completed",
                            "One-shot monitor iteration completed",
                            iteration_id=iteration_id,
                            observation=observation.to_dict(),
                        )
                        return 0
                    if observation.state in self.BACKOFF_STATES:
                        delay = min(
                            self.config.backoff_max_seconds,
                            self.config.backoff_initial_seconds * (2 ** max(0, self.network_failures - 1)),
                        )
                        if response:
                            try:
                                retry_after = float(response.headers.get("retry-after", "0"))
                                delay = min(self.config.backoff_max_seconds, max(delay, retry_after))
                            except ValueError:
                                pass
                    else:
                        target_interval = self.config.interval_seconds + random.uniform(
                            -self.config.jitter_seconds,
                            self.config.jitter_seconds,
                        )
                        cycle_elapsed = time.monotonic() - iteration_started
                        # Interval is measured request-start to request-start. A
                        # slow request never overlaps the next one.
                        delay = max(
                            0.0,
                            target_interval - cycle_elapsed,
                            MIN_POLL_INTERVAL_SECONDS - cycle_elapsed,
                        )
                    log_event(
                        self.logger,
                        logging.DEBUG,
                        "monitor_sleep",
                        "Sleeping before next check",
                        iteration_id=iteration_id,
                        state=observation.state.value,
                        cycle_elapsed_ms=round((time.monotonic() - iteration_started) * 1000, 3),
                        target_interval_seconds=round(target_interval, 6) if observation.state not in self.BACKOFF_STATES else None,
                        sleep_seconds=round(delay, 6),
                        network_failures=self.network_failures,
                        retry_after=response.headers.get("retry-after") if response else None,
                    )
                    deadline = time.monotonic() + delay
                    while not self.stopping and time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        time.sleep(min(0.5, remaining))
                return 0
        finally:
            self.client.close()
            log_event(
                self.logger,
                logging.INFO,
                "monitor_finished",
                "Monitor closed its HTTP client and finished",
                checks_started=self.check_sequence,
                stopping=self.stopping,
            )
