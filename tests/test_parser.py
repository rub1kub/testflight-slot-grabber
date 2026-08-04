from pathlib import Path
import unittest

from testflight_grabber.models import PageState
from testflight_grabber.parser import classify_page


FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://testflight.apple.com/join/u6iogfd0"


class ParserTests(unittest.TestCase):
    def classify(self, fixture: str, status: int = 200, url: str = URL):
        document = (FIXTURES / fixture).read_text(encoding="utf-8")
        return classify_page(status, url, document, {}, "Telegram Messenger", "u6iogfd0", 42)

    def test_beta_full(self):
        result = self.classify("beta_full.html")
        self.assertEqual(result.state, PageState.BETA_FULL)
        self.assertIn("full_text", result.signals)
        self.assertEqual(result.app_name, "Telegram Messenger")

    def test_partial_content_prefix_can_report_beta_full(self):
        document = (FIXTURES / "beta_full.html").read_text(encoding="utf-8")
        result = classify_page(
            206,
            URL,
            document,
            {"content-range": "bytes 0-3071/10203"},
            "Telegram Messenger",
            "u6iogfd0",
        )
        self.assertEqual(result.state, PageState.BETA_FULL)

    def test_available_requires_multiple_positive_signals(self):
        result = self.classify("available.html")
        self.assertEqual(result.state, PageState.AVAILABLE)
        self.assertIn("join_enabled_flag", result.signals)
        self.assertGreaterEqual(len(result.signals), 6)

    def test_partial_content_prefix_can_report_available(self):
        document = (FIXTURES / "available.html").read_text(encoding="utf-8")
        result = classify_page(
            206,
            URL,
            document,
            {"Content-Range": "bytes 0-3071/10203"},
            "Telegram Messenger",
            "u6iogfd0",
        )
        self.assertEqual(result.state, PageState.AVAILABLE)

    def test_unvalidated_partial_content_is_unexpected(self):
        document = (FIXTURES / "available.html").read_text(encoding="utf-8")
        result = classify_page(206, URL, document, {}, "Telegram Messenger", "u6iogfd0")
        self.assertEqual(result.state, PageState.UNEXPECTED_RESPONSE)

    def test_invalid(self):
        self.assertEqual(self.classify("invitation_invalid.html").state, PageState.INVITATION_INVALID)

    def test_http_404_is_invalid(self):
        self.assertEqual(self.classify("unexpected.html", status=404).state, PageState.INVITATION_INVALID)

    def test_build_unavailable(self):
        self.assertEqual(self.classify("build_unavailable.html").state, PageState.BUILD_UNAVAILABLE)

    def test_rate_limited(self):
        self.assertEqual(self.classify("unexpected.html", status=429).state, PageState.RATE_LIMITED)

    def test_unexpected(self):
        self.assertEqual(self.classify("unexpected.html").state, PageState.UNEXPECTED_RESPONSE)

    def test_redirect_to_other_host_is_not_available(self):
        self.assertEqual(
            self.classify("available.html", url="https://example.com/join/u6iogfd0").state,
            PageState.UNEXPECTED_RESPONSE,
        )

    def test_wrong_app_is_not_available(self):
        document = (FIXTURES / "available.html").read_text(encoding="utf-8").replace(
            "Telegram Messenger", "Another App"
        )
        result = classify_page(200, URL, document, {}, "Telegram Messenger", "u6iogfd0")
        self.assertEqual(result.state, PageState.UNEXPECTED_RESPONSE)

    def test_wrong_app_full_page_is_not_accepted_as_telegram_state(self):
        document = (FIXTURES / "beta_full.html").read_text(encoding="utf-8").replace(
            "Telegram Messenger", "Another App"
        )
        result = classify_page(200, URL, document, {}, "Telegram Messenger", "u6iogfd0")
        self.assertEqual(result.state, PageState.UNEXPECTED_RESPONSE)

    def test_full_marker_after_foreign_redirect_is_unexpected(self):
        result = self.classify("beta_full.html", url="https://example.com/join/u6iogfd0")
        self.assertEqual(result.state, PageState.UNEXPECTED_RESPONSE)

    def test_full_marker_in_http_500_is_unexpected(self):
        result = self.classify("beta_full.html", status=500)
        self.assertEqual(result.state, PageState.UNEXPECTED_RESPONSE)

    def test_missing_enabled_flag_is_not_inferred_from_absence_of_full_text(self):
        document = (FIXTURES / "available.html").read_text(encoding="utf-8").replace(
            "var showSteps = true", "var showSteps = false"
        )
        result = classify_page(200, URL, document, {}, "Telegram Messenger", "u6iogfd0")
        self.assertEqual(result.state, PageState.UNEXPECTED_RESPONSE)


if __name__ == "__main__":
    unittest.main()
