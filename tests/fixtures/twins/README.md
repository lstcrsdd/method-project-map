# twins fixture

One piece of logic, a stream link turned into an audio player's config, written by hand four times:
`swift/main.swift`, `rust/main.rs`, `kotlin/StreamLink.kt` and `python/stream_link.py`, plus an older Swift builder
(`swift/LegacyConfig.swift`). The Swift, Rust and Python copies read one link per line on stdin and print one JSON
object (or `ERROR`) per line; `tests/test_links.py` holds the links they are tested with.

The Python copy never writes `eq`: this is intended, the diagnostics tool does not play audio.
