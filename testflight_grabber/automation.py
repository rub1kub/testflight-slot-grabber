from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import Config, PROJECT_ROOT
from .logging_setup import log_event, log_exception


class AutomationError(RuntimeError):
    def __init__(self, message: str, exit_code: Optional[int] = None, details: Optional[Dict[str, object]] = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.details = details or {}


class ManagedProcess:
    """Small process handle for an app launched through LaunchServices."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: Optional[int] = None
        self._termination_requested = False

    def poll(self) -> Optional[int]:
        if self.returncode is not None:
            return self.returncode
        try:
            os.kill(self.pid, 0)
            return None
        except ProcessLookupError:
            self.returncode = -signal.SIGTERM if self._termination_requested else 0
            return self.returncode

    def terminate(self) -> None:
        if self.poll() is None:
            os.kill(self.pid, signal.SIGTERM)
            self._termination_requested = True

    def wait(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.poll() is not None:
                return int(self.returncode or 0)
            time.sleep(0.05)
        raise subprocess.TimeoutExpired("managed TestFlight AX mock", timeout)


class MacOSAutomation:
    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    def prewarm_testflight(self) -> None:
        call_id = secrets.token_hex(8)
        started = time.monotonic()
        arguments = ["/usr/bin/open", "-g", "-j", "-a", "TestFlight"]
        log_event(
            self.logger,
            logging.INFO,
            "testflight_prewarm_started",
            "Ensuring TestFlight is launched in the background",
            call_id=call_id,
            command=arguments,
        )
        try:
            result = subprocess.run(
                arguments,
                check=False,
                timeout=8,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log_exception(
                self.logger,
                "testflight_prewarm_failed",
                "Could not prewarm TestFlight",
                call_id=call_id,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise AutomationError(f"could not prewarm TestFlight: {exc}") from exc
        log_event(
            self.logger,
            logging.INFO if result.returncode == 0 else logging.WARNING,
            "testflight_prewarm_completed",
            "TestFlight background prewarm command completed",
            call_id=call_id,
            elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        if result.returncode != 0:
            raise AutomationError(f"TestFlight prewarm returned exit {result.returncode}: {result.stderr.strip()}")

    def _finish_invitation_open(
        self,
        process: subprocess.Popen[str],
        call_id: str,
        started: float,
    ) -> None:
        try:
            _, stderr = process.communicate(timeout=15)
            elapsed_ms = round((time.monotonic() - started) * 1000, 3)
            log_event(
                self.logger,
                logging.INFO if process.returncode == 0 else logging.WARNING,
                "testflight_open_completed" if process.returncode == 0 else "testflight_open_failed",
                "TestFlight invitation open command completed",
                call_id=call_id,
                deep_link=self.config.deep_link,
                elapsed_ms=elapsed_ms,
                exit_code=process.returncode,
                stderr=stderr,
                asynchronous=True,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
            log_event(
                self.logger,
                logging.WARNING,
                "testflight_open_failed",
                "Timed out waiting for the asynchronous TestFlight open command",
                call_id=call_id,
                deep_link=self.config.deep_link,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                exit_code=process.returncode,
                stderr=stderr,
                asynchronous=True,
            )

    def open_invitation(self) -> Dict[str, object]:
        call_id = secrets.token_hex(8)
        started = time.monotonic()
        arguments = ["/usr/bin/open", self.config.deep_link]
        log_event(
            self.logger,
            logging.INFO,
            "testflight_open_started",
            "Opening TestFlight invitation",
            call_id=call_id,
            deep_link=self.config.deep_link,
            command=arguments,
        )
        try:
            process = subprocess.Popen(
                arguments,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            log_exception(
                self.logger,
                "testflight_open_failed",
                "Could not open TestFlight invitation",
                call_id=call_id,
                deep_link=self.config.deep_link,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise AutomationError(f"could not open TestFlight deep link: {exc}") from exc

        dispatch_elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        log_event(
            self.logger,
            logging.INFO,
            "testflight_open_dispatched",
            "Dispatched TestFlight invitation without waiting for LaunchServices",
            call_id=call_id,
            deep_link=self.config.deep_link,
            dispatch_elapsed_ms=dispatch_elapsed_ms,
            open_process_pid=process.pid,
        )
        waiter = threading.Thread(
            target=self._finish_invitation_open,
            args=(process, call_id, started),
            name=f"testflight-open-{call_id[:6]}",
            daemon=False,
        )
        waiter.start()
        return {
            "call_id": call_id,
            "dispatched": True,
            "dispatch_elapsed_ms": dispatch_elapsed_ms,
            "open_process_pid": process.pid,
        }

    def _run_ax(
        self,
        command: str,
        timeout_seconds: float,
        extra: Optional[List[str]] = None,
        mock: bool = False,
    ) -> Tuple[Dict[str, object], subprocess.CompletedProcess[str]]:
        if not self.config.ax_binary.exists():
            raise AutomationError(f"AX helper is missing: {self.config.ax_binary}")
        arguments = [str(self.config.ax_binary), command, "--json"]
        arguments.extend(["--app-name", self.config.expected_app_name])
        if mock:
            arguments.extend(["--allow-mock", "--process-name", "testflight-ax-mock"])
        else:
            arguments.extend(["--bundle-id", "com.apple.TestFlight"])
        if extra:
            arguments.extend(extra)
        ax_call_id = secrets.token_hex(8)
        started = time.monotonic()
        log_event(
            self.logger,
            logging.INFO,
            "ax_command_started",
            "Starting native Accessibility command",
            ax_call_id=ax_call_id,
            command=command,
            arguments=arguments,
            timeout_seconds=timeout_seconds + 4,
            mock=mock,
        )
        try:
            result = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 4,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            log_exception(
                self.logger,
                "ax_command_timeout",
                "Native Accessibility command timed out",
                ax_call_id=ax_call_id,
                command=command,
                arguments=arguments,
                timeout_seconds=timeout_seconds + 4,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                stdout=exc.stdout,
                stderr=exc.stderr,
            )
            raise AutomationError(f"AX helper timed out during {command}") from exc

        payload: Dict[str, object] = {}
        for line in reversed(result.stdout.splitlines()):
            try:
                candidate = json.loads(line)
                if isinstance(candidate, dict):
                    payload = candidate
                    break
            except json.JSONDecodeError:
                continue
        stdout_bytes = result.stdout.encode("utf-8", "replace")
        stderr_bytes = result.stderr.encode("utf-8", "replace")
        event_level = logging.INFO if result.returncode == 0 else logging.WARNING
        log_event(
            self.logger,
            event_level,
            "ax_command_completed",
            "Native Accessibility command completed",
            ax_call_id=ax_call_id,
            command=command,
            arguments=arguments,
            mock=mock,
            elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            exit_code=result.returncode,
            payload=payload,
            stdout=result.stdout,
            stdout_bytes=len(stdout_bytes),
            stdout_sha256=hashlib.sha256(stdout_bytes).hexdigest(),
            stderr=result.stderr,
            stderr_bytes=len(stderr_bytes),
            stderr_sha256=hashlib.sha256(stderr_bytes).hexdigest(),
        )
        if result.returncode != 0:
            summary = str(payload.get("message") or result.stderr.strip() or f"exit {result.returncode}")
            raise AutomationError(summary, exit_code=result.returncode, details=payload)
        return payload, result

    def status(self, mock: bool = False) -> Dict[str, object]:
        payload, _ = self._run_ax("status", 5, mock=mock)
        return payload

    def permission(self) -> Dict[str, object]:
        payload, _ = self._run_ax("permission", 5, mock=False)
        return payload

    def accept(self, mock: bool = False) -> Dict[str, object]:
        deadline = time.monotonic() + self.config.accept_timeout_seconds
        while True:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                payload, _ = self._run_ax(
                    "accept",
                    remaining,
                    extra=["--timeout", str(remaining)],
                    mock=mock,
                )
                return payload
            except AutomationError as exc:
                # `open itms-beta://…` may return before TestFlight has created its
                # process. Retry only that precise, safe startup condition.
                if mock or exc.exit_code != 11 or time.monotonic() >= deadline:
                    raise
                log_event(
                    self.logger,
                    logging.DEBUG,
                    "ax_accept_startup_retry",
                    "TestFlight process is not visible yet; retrying Accept lookup",
                    exit_code=exc.exit_code,
                    remaining_seconds=round(max(0.0, deadline - time.monotonic()), 3),
                )
                time.sleep(0.1)

    def install(self, mock: bool = False) -> Dict[str, object]:
        payload, _ = self._run_ax(
            "install",
            self.config.install_timeout_seconds,
            extra=["--timeout", str(self.config.install_timeout_seconds)],
            mock=mock,
        )
        return payload

    def capture_failure_artifacts(
        self,
        reason: str,
        mock: bool = False,
        stage: str = "failure",
    ) -> Dict[str, str]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        artifact_dir = self.config.log_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        suffix = secrets.token_hex(3)
        tree_path = artifact_dir / f"ax-{stage}-{timestamp}-{suffix}.txt"
        screenshot_path = artifact_dir / f"screen-{stage}-{timestamp}-{suffix}.png"
        paths: Dict[str, str] = {"reason": reason, "stage": stage}

        try:
            extra = ["--output", str(tree_path)]
            self._run_ax("inspect", 8, extra=extra, mock=mock)
            if tree_path.exists():
                paths["ax_tree"] = str(tree_path)
                paths["ax_tree_sha256"] = hashlib.sha256(tree_path.read_bytes()).hexdigest()
        except AutomationError as exc:
            log_event(self.logger, logging.WARNING, "ax_dump_failed", "Could not capture AX tree", error=str(exc))

        try:
            self._run_ax("screenshot", 8, extra=["--output", str(screenshot_path)], mock=mock)
            if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
                paths["screenshot"] = str(screenshot_path)
                paths["screenshot_sha256"] = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
        except AutomationError as exc:
            log_event(self.logger, logging.WARNING, "screenshot_failed", "Could not capture TestFlight screenshot", error=str(exc))

        log_event(
            self.logger,
            logging.INFO,
            "ax_artifacts_captured",
            "Finished capturing Accessibility diagnostic artifacts",
            mock=mock,
            stage=stage,
            artifacts=paths,
        )
        return paths

    def capture_failure_artifacts_async(
        self,
        reason: str,
        mock: bool = False,
        stage: str = "failure",
    ) -> Dict[str, object]:
        job_id = secrets.token_hex(8)

        def capture() -> None:
            started = time.monotonic()
            artifacts = self.capture_failure_artifacts(reason, mock=mock, stage=stage)
            log_event(
                self.logger,
                logging.INFO,
                "ax_artifacts_async_completed",
                "Asynchronous Accessibility diagnostics completed",
                artifact_job_id=job_id,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                artifacts=artifacts,
            )

        thread = threading.Thread(
            target=capture,
            name=f"ax-artifacts-{job_id[:6]}",
            daemon=False,
        )
        thread.start()
        queued = {
            "queued": True,
            "artifact_job_id": job_id,
            "stage": stage,
            "thread_name": thread.name,
        }
        log_event(
            self.logger,
            logging.INFO,
            "ax_artifacts_queued",
            "Queued failure diagnostics without blocking another acceptance attempt",
            mock=mock,
            **queued,
        )
        return queued

    def launch_mock(self) -> ManagedProcess:
        mock_app = PROJECT_ROOT / "mock/TestFlightAXMock.app"
        mock_binary = mock_app / "Contents/MacOS/testflight-ax-mock"
        if not mock_binary.exists():
            raise AutomationError(f"mock executable is missing: {mock_binary}")
        log_event(
            self.logger,
            logging.INFO,
            "mock_app_launch_started",
            "Launching local TestFlight AX mock",
            binary=str(mock_binary),
        )
        launcher = subprocess.run(
            [
                "/usr/bin/open",
                "-n",
                str(mock_app),
                "--args",
                "--app-name",
                self.config.expected_app_name,
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if launcher.returncode != 0:
            raise AutomationError(f"could not launch mock app through LaunchServices: {launcher.stderr.strip()}")
        process: Optional[ManagedProcess] = None
        last_error: Optional[str] = None
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            try:
                payload, _ = self._run_ax("status", 1, mock=True)
                pid = int(payload.get("pid", 0) or 0)
                if pid > 0:
                    process = ManagedProcess(pid)
                    break
            except (AutomationError, ValueError) as exc:
                last_error = str(exc)
            time.sleep(0.1)
        if process is None:
            raise AutomationError(f"mock app did not become AX-visible: {last_error or 'unknown error'}")
        log_event(
            self.logger,
            logging.INFO,
            "mock_app_launch_completed",
            "Local TestFlight AX mock is running",
            binary=str(mock_binary),
            pid=process.pid,
            launcher_exit_code=launcher.returncode,
            launcher_stdout=launcher.stdout,
            launcher_stderr=launcher.stderr,
        )
        return process
