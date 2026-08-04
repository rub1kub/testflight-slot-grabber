from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from testflight_grabber.automation import MacOSAutomation
from testflight_grabber.config import Config
from testflight_grabber.diagnostics import health
from testflight_grabber.http_client import TestFlightHttpClient
from testflight_grabber.logging_setup import sanitize_for_log
from testflight_grabber.models import AcceptanceResult, HttpResponse, PageState
from testflight_grabber.monitor import Monitor
from testflight_grabber.pipeline import AcceptancePipeline
from testflight_grabber.state_store import StateStore


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
CONFIG = ROOT / "config.example.json"


class FakeNotifier:
    def __init__(self):
        self.events = []

    def notify(self, event, message, title="TestFlight Slot Grabber"):
        self.events.append((event, message, title))
        return "fake-sync-notification-id"

    def notify_async(self, event, message, title="TestFlight Slot Grabber", delay_seconds=0.0):
        del delay_seconds
        self.notify(event, message, title)
        return "fake-notification-id"


class FakePipeline:
    def __init__(self):
        self.calls = []

    def run(self, dry_run=False, mock=False, trigger_id=None, invitation_dispatch=None):
        self.calls.append((dry_run, mock, trigger_id, invitation_dispatch))
        return AcceptanceResult(
            success=True,
            accepted=False,
            installed=False,
            dry_run=True,
            reason="fixture dry-run",
        )


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.range_calls = []

    def fetch(self, url, range_probe_bytes=None):
        del url
        self.range_calls.append(range_probe_bytes)
        return self.responses.pop(0)

    def close(self):
        return None


class FakeAutomation:
    def __init__(self, outcomes, status_payload=None):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.status_calls = 0
        self.status_payload = status_payload or {"ok": True, "state": "beta_full", "app_visible": True}

    def permission(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def status(self):
        self.status_calls += 1
        return self.status_payload


class PredispatchedAutomation:
    def __init__(self):
        self.open_calls = 0

    def open_invitation(self):
        self.open_calls += 1
        raise AssertionError("pipeline must not dispatch an invitation twice")

    def accept(self, mock=False):
        self.mock = mock
        return {
            "ok": True,
            "status_before": {"app_visible": True, "incompatible_mac": True},
            "status_after": {"app_visible": True, "state": "app_incompatible_mac"},
        }


def quiet_logger():
    logger = logging.getLogger("testflight_grabber.tests")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    return logger


class RuntimeTests(unittest.TestCase):
    def test_config_requires_exact_join_path(self):
        base = Config.load(CONFIG)
        with self.assertRaises(ValueError):
            replace(base, target_url="https://testflight.apple.com/join/other?code=u6iogfd0").validate()

    def test_config_accepts_half_second_polling(self):
        base = Config.load(CONFIG)
        replace(base, interval_seconds=0.5, jitter_seconds=0.0).validate()

    def test_config_accepts_production_quarter_second_floor(self):
        base = Config.load(CONFIG)
        replace(base, interval_seconds=0.30, jitter_seconds=0.05).validate()

    def test_config_rejects_polling_below_absolute_floor(self):
        base = Config.load(CONFIG)
        with self.assertRaises(ValueError):
            replace(base, interval_seconds=0.24, jitter_seconds=0.0).validate()
        with self.assertRaises(ValueError):
            replace(base, interval_seconds=0.5, jitter_seconds=0.26).validate()

    def test_config_rejects_too_short_range_probe(self):
        base = Config.load(CONFIG)
        with self.assertRaises(ValueError):
            replace(base, range_probe_bytes=512).validate()

    def test_cache_buster_preserves_url_and_changes_each_request(self):
        one = TestFlightHttpClient.cache_busted_url("https://testflight.apple.com/join/u6iogfd0?a=1")
        two = TestFlightHttpClient.cache_busted_url("https://testflight.apple.com/join/u6iogfd0?a=1")
        self.assertIn("a=1", one)
        self.assertIn("_tfsg=", one)
        self.assertNotEqual(one, two)

    def test_log_sanitizer_redacts_secrets_recursively(self):
        sanitized = sanitize_for_log(
            {
                "headers": {"Set-Cookie": "session=private", "content-type": "text/html"},
                "bot_token": "private-token",
                "nested": [{"authorization": "Bearer private"}],
            }
        )
        self.assertEqual(sanitized["headers"]["Set-Cookie"], "<redacted>")
        self.assertEqual(sanitized["bot_token"], "<redacted>")
        self.assertEqual(sanitized["nested"][0]["authorization"], "<redacted>")
        self.assertEqual(sanitized["headers"]["content-type"], "text/html")

    def test_state_store_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            store.save({"last_state": "beta_full"})
            self.assertEqual(store.load()["last_state"], "beta_full")
            self.assertIn("updated_at", store.load())

    def test_automation_readiness_is_persisted_from_monitor_context(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Config.load(CONFIG)
            config = replace(base, data_dir=Path(directory) / "data", log_dir=Path(directory) / "logs")
            config.data_dir.mkdir(parents=True)
            config.log_dir.mkdir(parents=True)
            monitor = Monitor(config, quiet_logger())
            automation = FakeAutomation([RuntimeError("not granted"), {"ok": True, "trusted": True}])
            monitor.pipeline.automation = automation

            self.assertFalse(
                monitor._check_automation_readiness(
                    source="unit-failed",
                    max_attempts=1,
                    retry_delay_seconds=0.0,
                )
            )
            failed = monitor.store.load()
            self.assertFalse(failed["automation_ready"])
            self.assertEqual(failed["automation_readiness_error"], "not granted")

            self.assertTrue(
                monitor._check_automation_readiness(
                    source="startup",
                    max_attempts=1,
                    retry_delay_seconds=0.0,
                )
            )
            ready = monitor.store.load()
            self.assertTrue(ready["automation_ready"])
            self.assertIsNone(ready["automation_readiness_error"])
            self.assertEqual(ready["automation_readiness_source"], "startup")
            self.assertEqual(ready["automation_ui_probe"]["state"], "beta_full")
            self.assertIsNone(ready["automation_ui_probe_error"])
            self.assertEqual(automation.calls, 2)
            self.assertEqual(automation.status_calls, 1)

    def test_health_rejects_stale_accessibility_result_after_helper_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "helper"
            helper.write_bytes(b"current-helper")
            base = Config.load(CONFIG)
            config = replace(
                base,
                data_dir=root / "data",
                log_dir=root / "logs",
                ax_binary=helper,
            )
            config.data_dir.mkdir(parents=True)
            config.log_dir.mkdir(parents=True)
            config.pid_path.write_text(str(os.getpid()), encoding="ascii")
            StateStore(config.state_path).save(
                {
                    "last_check_at": "2026-08-04T00:00:00+00:00",
                    "automation_ready": True,
                    "automation_helper_sha256": "stale-signature-hash",
                }
            )

            result = health(config)
            self.assertTrue(result["polling_healthy"])
            self.assertFalse(result["automation_helper_hash_matches"])
            self.assertFalse(result["ready_to_accept"])
            self.assertFalse(result["healthy"])

    def test_inconclusive_range_probe_retries_with_full_html(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Config.load(CONFIG)
            config = replace(base, data_dir=Path(directory) / "data", log_dir=Path(directory) / "logs")
            config.data_dir.mkdir(parents=True)
            config.log_dir.mkdir(parents=True)
            monitor = Monitor(config, quiet_logger())
            partial = HttpResponse(
                status_code=206,
                final_url=config.target_url,
                headers={"content-range": "bytes 0-3071/10203"},
                body=(FIXTURES / "unexpected.html").read_text(encoding="utf-8"),
                elapsed_ms=10,
            )
            full = HttpResponse(
                status_code=200,
                final_url=config.target_url,
                headers={},
                body=(FIXTURES / "available.html").read_text(encoding="utf-8"),
                elapsed_ms=20,
            )
            fake_client = FakeHttpClient([partial, full])
            monitor.client = fake_client
            observation, response = monitor.check_once()
            self.assertEqual(observation.state, PageState.AVAILABLE)
            self.assertEqual(response, full)
            self.assertEqual(fake_client.range_calls, [config.range_probe_bytes, None])

    def test_pipeline_dry_run_never_invokes_ax(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Config.load(CONFIG)
            config = replace(base, data_dir=Path(directory), log_dir=Path(directory))
            notifier = FakeNotifier()
            result = AcceptancePipeline(config, quiet_logger(), notifier).run(dry_run=True)
            self.assertTrue(result.success)
            self.assertTrue(result.dry_run)
            self.assertFalse(result.accepted)
            self.assertTrue(any(event[0] == "dry_run_complete" for event in notifier.events))

    def test_invitation_open_dispatch_does_not_wait_for_open_process(self):
        class SlowOpenProcess:
            pid = 4242
            returncode = None

            def communicate(self, timeout=None):
                del timeout
                time.sleep(0.15)
                self.returncode = 0
                return ("", "")

            def kill(self):
                self.returncode = -9

        with tempfile.TemporaryDirectory() as directory:
            base = Config.load(CONFIG)
            config = replace(base, data_dir=Path(directory), log_dir=Path(directory))
            automation = MacOSAutomation(config, quiet_logger())
            with mock.patch("testflight_grabber.automation.subprocess.Popen", return_value=SlowOpenProcess()):
                started = time.monotonic()
                dispatch = automation.open_invitation()
                elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.10)
            self.assertTrue(dispatch["dispatched"])
            self.assertEqual(dispatch["open_process_pid"], 4242)

    def test_pipeline_reuses_predispatched_invitation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Config.load(CONFIG)
            config = replace(
                base,
                data_dir=Path(directory),
                log_dir=Path(directory),
                install_after_accept=False,
                dry_run=False,
            )
            notifier = FakeNotifier()
            pipeline = AcceptancePipeline(config, quiet_logger(), notifier)
            automation = PredispatchedAutomation()
            pipeline.automation = automation
            dispatch = {"dispatched": True, "dispatch_elapsed_ms": 3.0}
            result = pipeline.run(invitation_dispatch=dispatch)
            self.assertTrue(result.success)
            self.assertTrue(result.accepted)
            self.assertEqual(automation.open_calls, 0)
            self.assertEqual(result.details["invitation_open"], dispatch)

    def test_available_fixture_runs_confirmed_pipeline_once(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Config.load(CONFIG)
            config = replace(
                base,
                data_dir=Path(directory) / "data",
                log_dir=Path(directory) / "logs",
                confirm_min_delay_ms=300,
                confirm_max_delay_ms=300,
            )
            config.data_dir.mkdir(parents=True)
            config.log_dir.mkdir(parents=True)
            monitor = Monitor(config, quiet_logger())
            pipeline = FakePipeline()
            notifier = FakeNotifier()
            monitor.pipeline = pipeline
            monitor.notifier = notifier
            exit_code = monitor.run(once=True, fixture=FIXTURES / "available.html", force_dry_run=True)
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(pipeline.calls), 1)
            self.assertEqual(pipeline.calls[0][:2], (True, False))
            self.assertIsNotNone(pipeline.calls[0][2])
            saved = json.loads(config.state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["last_state"], "available")
            self.assertIn("last_acceptance_result", saved)

    def test_confirmation_request_overlaps_minimum_press_gate(self):
        class SlowConfirmationClient:
            def __init__(self, response):
                self.response = response
                self.range_calls = []

            def fetch(self, url, range_probe_bytes=None):
                del url
                self.range_calls.append(range_probe_bytes)
                time.sleep(0.20)
                return self.response

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            base = Config.load(CONFIG)
            config = replace(
                base,
                data_dir=Path(directory) / "data",
                log_dir=Path(directory) / "logs",
                confirm_min_delay_ms=300,
                confirm_max_delay_ms=300,
            )
            config.data_dir.mkdir(parents=True)
            config.log_dir.mkdir(parents=True)
            monitor = Monitor(config, quiet_logger())
            first, _ = monitor.check_once(FIXTURES / "available.html")
            body = (FIXTURES / "available.html").read_text(encoding="utf-8")
            response = HttpResponse(
                status_code=206,
                final_url=config.target_url,
                headers={"content-range": "bytes 0-3071/10203"},
                body=body,
                elapsed_ms=200,
                diagnostics={"request_id": "slow-confirm", "body_sha256": "fixture"},
            )
            client = SlowConfirmationClient(response)
            pipeline = FakePipeline()
            monitor.client = client
            monitor.pipeline = pipeline
            monitor.notifier = FakeNotifier()

            started = time.monotonic()
            monitor._handle_available(first, None, force_dry_run=True)
            elapsed = time.monotonic() - started

            self.assertGreaterEqual(elapsed, 0.29)
            self.assertLess(elapsed, 0.45)
            self.assertEqual(client.range_calls, [config.range_probe_bytes])
            self.assertEqual(len(pipeline.calls), 1)


if __name__ == "__main__":
    unittest.main()
