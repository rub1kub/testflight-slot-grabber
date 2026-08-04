from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Optional, Sequence

from .config import Config
from .diagnostics import collect_diagnostics, health, save_diagnostics
from .http_client import TestFlightHttpClient
from .lock import AlreadyRunning
from .logging_setup import log_event, log_exception, setup_logging
from .models import PageState
from .monitor import Monitor
from .notifier import Notifier
from .parser import classify_page
from .pipeline import AcceptancePipeline
from .replay import ReplayUnavailable, experimental_replay


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m testflight_grabber")
    parser.add_argument("--config", type=Path, help="path to config.json")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    monitor = sub.add_parser("monitor", help="run the conservative polling loop")
    monitor.add_argument("--once", action="store_true", help="perform one polling iteration")
    monitor.add_argument("--fixture", type=Path, help="use local HTML instead of network")
    monitor.add_argument("--dry-run", action="store_true", help="never press external UI buttons")
    monitor.add_argument(
        "--mock-automation",
        action="store_true",
        help="run the full pipeline against the local mock; requires --fixture",
    )

    check = sub.add_parser("check", help="classify the current public page")
    check.add_argument("--fixture", type=Path, help="classify a local HTML fixture")

    accept = sub.add_parser("accept", help="run the TestFlight acceptance pipeline")
    accept.add_argument("--dry-run", action="store_true")
    accept.add_argument("--mock", action="store_true", help="use the local TestFlight-like mock window")
    accept.add_argument("--experimental-api-replay", action="store_true")

    sub.add_parser("diagnose", help="collect environment and readiness diagnostics")
    sub.add_parser("health", help="print monitor health as JSON")
    sub.add_parser("notify-test", help="send a harmless local test notification")
    return parser


def _check(config: Config, fixture: Optional[Path], logger: logging.Logger) -> dict:
    if fixture:
        document = fixture.read_text(encoding="utf-8")
        response_status = 200
        final_url = config.target_url
        headers = {"x-fixture": fixture.name}
        elapsed_ms = 0
    else:
        client = TestFlightHttpClient(config.request_timeout_seconds, logger=logger)
        try:
            response = client.fetch(config.target_url)
        finally:
            client.close()
        document = response.body
        response_status = response.status_code
        final_url = response.final_url
        headers = response.headers
        elapsed_ms = response.elapsed_ms
    return classify_page(
        response_status,
        final_url,
        document,
        headers,
        config.expected_app_name,
        config.join_code,
        elapsed_ms,
    ).to_dict()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = Config.load(args.config)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": f"configuration error: {exc}"}, ensure_ascii=False))
        return 2
    logger = setup_logging(config.log_dir, args.verbose)

    try:
        if args.command == "check":
            result = _check(config, args.fixture, logger)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if result["state"] in {PageState.UNEXPECTED_RESPONSE.value, PageState.NETWORK_ERROR.value} else 0

        if args.command == "monitor":
            if args.mock_automation and not args.fixture:
                print(json.dumps({"ok": False, "error": "--mock-automation requires --fixture"}, ensure_ascii=False))
                return 2
            return Monitor(config, logger).run(
                once=args.once,
                fixture=args.fixture,
                force_dry_run=args.dry_run,
                mock_automation=args.mock_automation,
            )

        if args.command == "accept":
            if args.experimental_api_replay:
                try:
                    experimental_replay(config)
                except ReplayUnavailable as exc:
                    print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
                    return 4
            result = AcceptancePipeline(config, logger, Notifier(config, logger)).run(
                dry_run=args.dry_run, mock=args.mock
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0 if result.success else 3

        if args.command == "diagnose":
            report = collect_diagnostics(config, logger=logger)
            path = save_diagnostics(config, report)
            report["saved_to"] = str(path)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        if args.command == "health":
            result = health(config)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["healthy"] else 1

        if args.command == "notify-test":
            Notifier(config, logger).notify("manual_action_required", "Тест локального уведомления TestFlight Slot Grabber.")
            return 0
    except AlreadyRunning as exc:
        log_event(logger, logging.ERROR, "instance_lock_rejected", str(exc), error_type=type(exc).__name__)
        return 9
    except KeyboardInterrupt:
        log_event(logger, logging.INFO, "command_interrupted", "Command interrupted by user")
        return 130
    except Exception as exc:
        log_exception(
            logger,
            "command_failed",
            "Command failed",
            command=args.command,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return 1
    return 2
