from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config
from .http_client import TestFlightHttpClient
from .parser import classify_page
from .state_store import StateStore


def _run(arguments: List[str], timeout: int = 12) -> Dict[str, object]:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout.strip()[-4000:],
            "stderr": result.stderr.strip()[-2000:],
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _testflight_info() -> Dict[str, object]:
    app = Path("/Applications/TestFlight.app")
    if not app.exists():
        return {"installed": False}
    result: Dict[str, object] = {"installed": True, "path": str(app)}
    try:
        with (app / "Contents/Info.plist").open("rb") as handle:
            plist = plistlib.load(handle)
        result.update(
            {
                "version": plist.get("CFBundleShortVersionString"),
                "build": plist.get("CFBundleVersion"),
                "bundle_id": plist.get("CFBundleIdentifier"),
                "url_schemes": [
                    scheme
                    for item in plist.get("CFBundleURLTypes", [])
                    for scheme in item.get("CFBundleURLSchemes", [])
                ],
            }
        )
    except (OSError, plistlib.InvalidFileException) as exc:
        result["error"] = str(exc)
    return result


def _system_proxy_info() -> Dict[str, object]:
    command = _run(["/usr/sbin/scutil", "--proxy"])
    stdout = str(command.get("stdout", ""))
    values = {
        key: int(value)
        for key, value in re.findall(r"^\s*(HTTPEnable|HTTPSEnable)\s*:\s*([01])\s*$", stdout, re.MULTILINE)
    }
    return {
        "ok": bool(command.get("ok")),
        "http_enabled": bool(values.get("HTTPEnable", 0)),
        "https_enabled": bool(values.get("HTTPSEnable", 0)),
    }


def _proxyman_info() -> Dict[str, object]:
    app = Path("/Applications/Proxyman.app")
    result: Dict[str, object] = {"installed": app.exists(), "path": str(app)}
    if not app.exists():
        return result
    try:
        with (app / "Contents/Info.plist").open("rb") as handle:
            plist = plistlib.load(handle)
        result["version"] = plist.get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException) as exc:
        result["error"] = str(exc)
    result["running"] = bool(_run(["/usr/bin/pgrep", "-x", "Proxyman"]).get("ok"))
    result["system_proxy"] = _system_proxy_info()
    return result


def collect_diagnostics(config: Config, logger: Optional[logging.Logger] = None) -> Dict[str, object]:
    persisted_state = StateStore(config.state_path).load()
    report: Dict[str, object] = {
        "platform": {
            "machine": platform.machine(),
            "macos": platform.mac_ver()[0],
            "python": platform.python_version(),
        },
        "project_root": str(Path(__file__).resolve().parent.parent),
        "testflight": _testflight_info(),
        "state": persisted_state,
        "ax_binary": {"path": str(config.ax_binary), "exists": config.ax_binary.exists()},
        "xcode_select": _run(["/usr/bin/xcode-select", "-p"]),
        "xcodebuild": _run(["/usr/bin/xcodebuild", "-version"]),
        "accessibility": _run([str(config.ax_binary), "permission", "--json"]) if config.ax_binary.exists() else {"ok": False},
        "accessibility_probe_context": "interactive caller; authoritative background result is accessibility_launch_agent",
        "accessibility_launch_agent": {
            "ready": persisted_state.get("automation_ready"),
            "checked_at": persisted_state.get("automation_readiness_checked_at"),
            "changed_at": persisted_state.get("automation_readiness_changed_at"),
            "source": persisted_state.get("automation_readiness_source"),
            "error": persisted_state.get("automation_readiness_error"),
            "helper_sha256": persisted_state.get("automation_helper_sha256"),
        },
        "ax_status": _run([str(config.ax_binary), "status", "--json", "--bundle-id", "com.apple.TestFlight"]) if config.ax_binary.exists() else {"ok": False},
        "automation_system_events": _run(["/usr/bin/osascript", "-e", 'tell application "System Events" to get UI elements enabled']),
        "proxyman": _proxyman_info(),
        "appium": _run(["/usr/bin/env", "appium", "--version"]),
        "launch_agent": {
            "plist": str(Path.home() / "Library/LaunchAgents/local.testflight-slot-grabber.plist"),
            "installed": (Path.home() / "Library/LaunchAgents/local.testflight-slot-grabber.plist").exists(),
            "status": _run(["/bin/launchctl", "print", f"gui/{os.getuid()}/local.testflight-slot-grabber"]),
        },
    }
    client = TestFlightHttpClient(config.request_timeout_seconds, logger=logger)
    try:
        response = client.fetch(config.target_url)
        observation = classify_page(
            response.status_code,
            response.final_url,
            response.body,
            response.headers,
            config.expected_app_name,
            config.join_code,
            response.elapsed_ms,
        )
        report["network_check"] = observation.to_dict()
    except Exception as exc:
        report["network_check"] = {"state": "network_error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        client.close()
    return report


def save_diagnostics(config: Config, report: Dict[str, object]) -> Path:
    path = config.log_dir / "diagnose.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def health(config: Config) -> Dict[str, object]:
    state = StateStore(config.state_path).load()
    pid: Optional[int] = None
    try:
        pid = int(config.pid_path.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
        running = True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        running = False
    polling_healthy = running and bool(state.get("last_check_at"))
    recorded_helper_sha256 = state.get("automation_helper_sha256")
    try:
        current_helper_sha256: Optional[str] = hashlib.sha256(config.ax_binary.read_bytes()).hexdigest()
    except OSError:
        current_helper_sha256 = None
    helper_hash_matches = bool(
        current_helper_sha256
        and recorded_helper_sha256
        and current_helper_sha256 == recorded_helper_sha256
    )
    automation_ready = state.get("automation_ready") is True and helper_hash_matches
    return {
        "healthy": polling_healthy and automation_ready,
        "polling_healthy": polling_healthy,
        "ready_to_accept": polling_healthy and automation_ready,
        "running": running,
        "pid": pid,
        "last_check_at": state.get("last_check_at"),
        "last_state": state.get("last_state"),
        "last_state_change_at": state.get("last_state_change_at"),
        "last_body_sha256": state.get("last_body_sha256"),
        "automation_ready": state.get("automation_ready"),
        "automation_readiness_checked_at": state.get("automation_readiness_checked_at"),
        "automation_readiness_changed_at": state.get("automation_readiness_changed_at"),
        "automation_readiness_source": state.get("automation_readiness_source"),
        "automation_readiness_error": state.get("automation_readiness_error"),
        "automation_ui_probe_checked_at": state.get("automation_ui_probe_checked_at"),
        "automation_ui_probe": state.get("automation_ui_probe"),
        "automation_ui_probe_error": state.get("automation_ui_probe_error"),
        "automation_helper_sha256": recorded_helper_sha256,
        "automation_helper_current_sha256": current_helper_sha256,
        "automation_helper_hash_matches": helper_hash_matches,
        "accepted": bool(state.get("accepted", False)),
        "accepted_at": state.get("accepted_at"),
        "last_availability_id": state.get("last_availability_id"),
        "last_acceptance_attempt_at": state.get("last_acceptance_attempt_at"),
        "last_acceptance_result": state.get("last_acceptance_result"),
    }
