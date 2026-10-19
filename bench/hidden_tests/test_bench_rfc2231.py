"""Hidden test for the email-rfc2231-filename task (CPython's own suite does not cover this bug)."""

import email
import unittest
from email import utils


def _message(disposition: str) -> email.message.Message:
    return email.message_from_string(f"Content-Type: text/plain\nContent-Disposition: {disposition}\n\nx\n")


class TestRFC2231Charset(unittest.TestCase):
    def test_utf8_filename(self) -> None:
        msg = _message("attachment; filename*=utf-8''%E2%82%AC%20rates.txt")
        self.assertEqual(msg.get_filename(), "€ rates.txt")

    def test_latin1_filename(self) -> None:
        msg = _message("attachment; filename*=iso-8859-1''caf%E9.txt")
        self.assertEqual(msg.get_filename(), "caf\xe9.txt")

    def test_collapse_uses_declared_charset(self) -> None:
        self.assertEqual(utils.collapse_rfc2231_value(("utf-8", "", "\xe2\x82\xac")), "€")

    def test_missing_charset_uses_fallback(self) -> None:
        self.assertEqual(utils.collapse_rfc2231_value((None, None, "abc")), "abc")

    def test_unknown_charset_returns_text(self) -> None:
        self.assertEqual(utils.collapse_rfc2231_value(("x-unknown-charset", "", "abc")), "abc")


if __name__ == "__main__":
    unittest.main()
