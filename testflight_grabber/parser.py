from __future__ import annotations

import html as html_module
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import urlparse

from .models import PageObservation, PageState


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _visible_text(document: str) -> str:
    parser = _VisibleTextParser()
    try:
        parser.feed(document)
    except Exception:
        return " "
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _extract_title(document: str) -> Optional[str]:
    match = re.search(r"<title[^>]*>(.*?)</title>", document, re.I | re.S)
    return html_module.unescape(re.sub(r"\s+", " ", match.group(1)).strip()) if match else None


def _app_name_from_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    match = re.match(r"Join the (.+?) beta\s*-\s*TestFlight", title, re.I)
    return match.group(1).strip() if match else None


def classify_page(
    status_code: int,
    final_url: str,
    document: str,
    headers: Optional[Dict[str, str]],
    expected_app_name: str,
    join_code: str,
    elapsed_ms: Optional[int] = None,
) -> PageObservation:
    """Classify a TestFlight public page using negative and positive evidence."""
    normalized_headers = {key.lower(): value for key, value in (headers or {}).items()}
    signals: List[str] = []
    title = _extract_title(document)
    app_name = _app_name_from_title(title)
    text = _visible_text(document)
    folded = text.casefold()
    lower_html = document.casefold()
    parsed_url = urlparse(final_url)
    body_bytes = len(document.encode("utf-8", "replace"))

    def observation(state: PageState, reason: str) -> PageObservation:
        return PageObservation(
            state=state,
            reason=reason,
            status_code=status_code,
            final_url=final_url,
            app_name=app_name,
            signals=signals,
            body_bytes=body_bytes,
            elapsed_ms=elapsed_ms,
        )

    if status_code == 429:
        signals.append("http_429")
        return observation(PageState.RATE_LIMITED, "Apple returned HTTP 429")

    expected_path = f"/join/{join_code}"
    if parsed_url.scheme != "https" or parsed_url.netloc != "testflight.apple.com":
        signals.append("unexpected_final_host")
        return observation(PageState.UNEXPECTED_RESPONSE, "Request redirected away from testflight.apple.com")
    if parsed_url.path.rstrip("/") != expected_path:
        signals.append("unexpected_final_path")
        return observation(PageState.UNEXPECTED_RESPONSE, "Final URL does not match the configured invitation")

    invalid_phrases = (
        "this invitation is invalid",
        "this beta invitation is invalid",
        "the link you followed is invalid or has expired",
        "this beta is no longer available",
        "invitation has expired",
    )
    unavailable_phrases = (
        "no builds are available to test",
        "this beta isn't accepting any new testers right now",
        "this beta is not currently accepting new testers",
        "a build is not available for this beta",
        "the developer has removed this beta",
    )
    full_phrases = ("this beta is full", "в этой программе бета-тестирования больше нет мест")

    if any(phrase in folded for phrase in invalid_phrases) or status_code in {404, 410}:
        signals.append("invalid_invitation_marker")
        return observation(PageState.INVITATION_INVALID, "Invitation is invalid, expired, or missing")

    valid_prefix_response = status_code == 206 and normalized_headers.get("content-range", "").startswith("bytes 0-")
    if status_code != 200 and not valid_prefix_response:
        signals.append(f"http_{status_code}")
        return observation(PageState.UNEXPECTED_RESPONSE, "Unexpected HTTP status")

    if app_name and app_name.casefold() != expected_app_name.casefold():
        signals.append("unexpected_app_title")
        return observation(PageState.UNEXPECTED_RESPONSE, "Invitation page belongs to a different application")

    if any(phrase in folded for phrase in unavailable_phrases):
        signals.append("build_unavailable_marker")
        return observation(PageState.BUILD_UNAVAILABLE, "Invitation exists but no joinable build is available")

    if any(phrase in folded for phrase in full_phrases):
        signals.append("full_text")
        beta_status = 'class="beta-status"' in lower_html or "class='beta-status'" in lower_html
        expected_title = bool(app_name and app_name.casefold() == expected_app_name.casefold())
        if beta_status:
            signals.append("beta_status_container")
        if expected_title:
            signals.append("expected_app_title")
        if beta_status and expected_title:
            return observation(PageState.BETA_FULL, "Matching public beta explicitly reports that its tester limit is full")
        return observation(
            PageState.UNEXPECTED_RESPONSE,
            "Full marker appeared without matching application and TestFlight status structure",
        )

    deep_link = f"itms-beta://testflight.apple.com/join/{join_code}".casefold()
    positive_checks = {
        "expected_app_title": bool(app_name and app_name.casefold() == expected_app_name.casefold()),
        "testflight_title": bool(title and "testflight" in title.casefold()),
        "deep_link": deep_link in lower_html,
        "main_container": 'id="main"' in lower_html or "id='main'" in lower_html,
        "steps_container": 'id="steps"' in lower_html or "id='steps'" in lower_html,
        "app_icon": "class='app-icon" in lower_html or 'class="app-icon' in lower_html,
        "join_enabled_flag": bool(re.search(r"var\s+showSteps\s*=\s*true\b", document, re.I)),
    }
    signals.extend(name for name, present in positive_checks.items() if present)

    mandatory = ("expected_app_title", "deep_link", "main_container", "join_enabled_flag")
    if all(positive_checks[item] for item in mandatory) and sum(positive_checks.values()) >= 6:
        return observation(PageState.AVAILABLE, "Join page is enabled and has matching app/deep-link metadata")

    return observation(
        PageState.UNEXPECTED_RESPONSE,
        "Response lacks a known negative state and enough independent availability signals",
    )


def network_error_observation(url: str, reason: str) -> PageObservation:
    return PageObservation(
        state=PageState.NETWORK_ERROR,
        reason=reason,
        status_code=None,
        final_url=url,
        app_name=None,
        signals=["transport_error"],
    )
