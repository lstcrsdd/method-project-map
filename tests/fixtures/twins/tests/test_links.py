"""Stream links the copies are tested with (the inputs `pmap twins --exec` harvests)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from stream_link import build_config  # noqa: E402

LINKS = [
    "stream://listener@203.0.113.7:443?codec=opus&eq=bass&eq-gain=3&peer=radio.example.com#home",
    "stream://listener@[2001:db8::1]:8443?codec=opus#Name",
    "stream://listener@198.51.100.2:443#plain",
]
REJECTED = ["stream://listener@example.com:999999#bad", "podcast://x@example.com:443"]


class StreamLinkTests(unittest.TestCase):
    def test_accepted(self):
        for link in LINKS:
            self.assertEqual(build_config(link)["sources"][0]["kind"], "stream")

    def test_rejected(self):
        for link in REJECTED:
            with self.assertRaises(ValueError):
                build_config(link)


if __name__ == "__main__":
    unittest.main()
