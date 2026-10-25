"""The diagnostics copy: a stream link -> an audio player config. Reads one link per line on stdin, prints one JSON
object (or ERROR) per line.

Intended difference: this copy never writes `eq` (the diagnostics tool does not play audio; see README.md).
"""

import json
import sys
from urllib.parse import parse_qs, urlsplit


class Tag:
    REMOTE = "remote"
    LOCAL = "local"


def build_config(link):
    url = urlsplit(link)
    if url.scheme != "stream":
        raise ValueError("scheme")
    host = url.hostname or ""  # urlsplit drops the brackets of an IPv6 host
    try:
        port = url.port
    except ValueError:
        port = None
    if not host or not port or not 0 < port <= 65535:
        raise ValueError("host or port")
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    source = {
        "kind": "stream",
        "tag": Tag.REMOTE,
        "host": host,
        "port": port,
        "user": url.username or "",
        "codec": {"name": query.get("codec", "opus")},
        "tls": {"enabled": True, "peer_name": query.get("peer", host)},
    }
    prefix = "/128" if ":" in host else "/32"
    return {
        "sources": [source, {"kind": "cache", "tag": Tag.LOCAL}],
        "playback": {"rules": [{"peer_cidr": [host + prefix], "source": Tag.LOCAL}], "fallback": Tag.REMOTE},
    }


def main():
    for line in sys.stdin:
        try:
            print(json.dumps(build_config(line.strip()), sort_keys=True))
        except ValueError:
            print("ERROR")


if __name__ == "__main__":
    main()
