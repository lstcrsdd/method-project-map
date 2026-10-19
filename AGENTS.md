# Notes for coding agents working on Method Project Map

This repository is the tool itself: the scanner and the CLI in `pmap/`, the format specification in `spec/`.

- **Never present a guess as a fact.** Any change to how links are resolved in `pmap/scanner.py` comes with a regression test in `tests/test_resolution.py` that checks the exact `confidence` of the exact edge.
- **The format changes in three places at once:** `spec/PMF-v0.md`, `spec/pmf-v0.schema.json` and `validate_pmf_document` in `pmap/yaml_loader.py`.
- **Standard library only** for Python projects, Python 3.9+. Other languages live in `pmap/langs/` (tree-sitter, optional); each has a small project in `tests/fixtures/langs/` with known links — extend it together with the resolver. Tests: `python3 -m unittest discover -s tests` (language tests skip without the grammars).
- **The web UI** is one file, `pmap/web/static/index.html`, with no external dependencies. Every user-facing string exists in English and Russian (`L.en` / `L.ru`).
- **The plugin is generated:** after changing `pmap/`, run `python3 scripts/build_plugin.py`; never edit `plugins/project-map/` by hand.
- **Agent access rules** (overview, `find` / `show` / `source`, MCP tools, trust levels): `docs/AGENT_PROTOCOL.md`.

Orient with the map before reading files: `python3 -m pmap context . --max-chars 8000`, then `python3 -m pmap find <name>`, `show <ref>`, `source <ref>`. Rely on `resolved` links; verify `inferred` ones in the code.
