#!/usr/bin/python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_JOIN_URL = "https://testflight.apple.com/join/u6iogfd0"
TESTFLIGHT_BUNDLE = "com.apple.TestFlight"


class AppiumClient:
    def __init__(self, server: str) -> None:
        parsed = urllib.parse.urlparse(server)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Appium server must be local")
        self.server = server.rstrip("/")
        self.session_id = None

    def request(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.server + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
        value = body.get("value")
        if isinstance(value, dict) and value.get("error"):
            raise RuntimeError(f"Appium error: {value.get('error')}: {value.get('message')}")
        return body

    def create_session(self, udid: str):
        body = self.request("POST", "/session", {
            "capabilities": {"alwaysMatch": {
                "platformName": "iOS",
                "appium:automationName": "XCUITest",
                "appium:udid": udid,
                "appium:bundleId": TESTFLIGHT_BUNDLE,
                "appium:noReset": True,
                "appium:newCommandTimeout": 120,
            }}
        })
        self.session_id = body.get("sessionId") or body.get("value", {}).get("sessionId")
        if not self.session_id:
            raise RuntimeError("Appium did not return a session id")

    def execute(self, script: str, args):
        return self.request("POST", f"/session/{self.session_id}/execute/sync", {"script": script, "args": args})

    def source(self) -> str:
        return str(self.request("GET", f"/session/{self.session_id}/source").get("value", ""))

    def find_accessibility_id(self, label: str):
        try:
            body = self.request("POST", f"/session/{self.session_id}/element", {
                "using": "accessibility id", "value": label,
            })
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        value = body.get("value", {})
        return value.get("element-6066-11e4-a52e-4f735466cecf") or value.get("ELEMENT")

    def click(self, element_id: str):
        self.request("POST", f"/session/{self.session_id}/element/{element_id}/click", {})

    def close(self):
        if self.session_id:
            try:
                self.request("DELETE", f"/session/{self.session_id}")
            except Exception:
                pass


def wait_for_source(client: AppiumClient, needle: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = client.source()
        if needle.casefold() in last.casefold():
            return last
        time.sleep(0.25)
    raise RuntimeError(f"did not find {needle!r} in TestFlight UI")


def find_exact_button(client: AppiumClient, labels):
    for label in labels:
        element = client.find_accessibility_id(label)
        if element:
            return label, element
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:4723")
    parser.add_argument("--udid", required=True)
    parser.add_argument("--join-url", default=os.environ.get("TESTFLIGHT_URL", DEFAULT_JOIN_URL))
    parser.add_argument("--app-name", default=os.environ.get("TESTFLIGHT_APP_NAME", "Telegram Messenger"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    join_url = urllib.parse.urlparse(args.join_url)
    if join_url.scheme != "https" or join_url.netloc != "testflight.apple.com" or not join_url.path.startswith("/join/"):
        raise ValueError("--join-url must be an HTTPS public TestFlight invitation")

    client = AppiumClient(args.server)
    try:
        client.create_session(args.udid)
        client.execute("mobile: deepLink", [{"url": args.join_url, "bundleId": TESTFLIGHT_BUNDLE}])
        source = wait_for_source(client, args.app_name, 15)
        if "TestFlight" not in source and args.app_name not in source:
            raise RuntimeError("safety check failed: expected TestFlight invitation is not visible")

        accept_label, accept = find_exact_button(client, ["Accept", "Join", "Принять", "Присоединиться"])
        if args.dry_run:
            print(json.dumps({"ok": True, "dry_run": True, "app_visible": True, "accept_button": accept_label}, ensure_ascii=False))
            return 0
        if not accept:
            raise RuntimeError("exact Accept/Join button not found")
        client.click(accept)

        deadline = time.monotonic() + 15
        install_label = install = None
        while time.monotonic() < deadline and not install:
            install_label, install = find_exact_button(client, ["Install", "Установить"])
            if not install: time.sleep(0.2)
        if install:
            client.click(install)
        final_source = client.source()
        print(json.dumps({
            "ok": True,
            "accepted": True,
            "accept_button": accept_label,
            "install_button": install_label,
            "app_visible": args.app_name in final_source,
        }, ensure_ascii=False))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
