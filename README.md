<h1 align="center">Method Project Map</h1>

<p align="center">
  <b>See how a codebase is built — and let your AI agent see it too.</b>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python | TypeScript | JavaScript | Go | Java | Kotlin | C# | Swift | Dart | Rust | C/C++" src="https://img.shields.io/badge/languages-Python%20%7C%20TS%2FJS%20%7C%20Go%20%7C%20Java%20%7C%20Kotlin%20%7C%20C%23%20%7C%20Swift%20%7C%20Dart%20%7C%20Rust%20%7C%20C%2B%2B-8A2BE2.svg">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-3776AB.svg">
  <img alt="No dependencies" src="https://img.shields.io/badge/dependencies-none-brightgreen.svg">
  <img alt="macOS | Linux | Windows" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg">
</p>

<p align="center">
  <b>English</b> · <a href="README.ru.md">Русский</a>
</p>

<p align="center">
  A project by <a href="https://method-network.com/"><b>Method Network</b></a>
</p>

![Method Project Map: packages stacked in layers, the links of the selected package](docs/images/overview-en.png)

**Method Project Map** reads a codebase statically (Python, TypeScript / JavaScript, Go, Java, Kotlin, C#, Swift, Dart / Flutter, Rust, C / C++) and draws its structure: packages, files, classes, functions, and who imports, calls and inherits whom. No AI and no network are involved: the map is computed, so it is the same every time and refreshes itself when the code changes.

One map serves three jobs:

- **For people**, an interactive block diagram in the browser. Packages are stacked in layers, from entry points down to the foundation; click a card to see what it uses and what uses it.
- **For AI coding agents** (Claude Code, Codex, any MCP client), a compact overview of the project at the start of every session, plus precise queries instead of reading whole files. In our benchmark the agent read **half as much code** with the same quality of fixes.
- **For architects**, the intended architecture (`design.yaml`) checked against the real code. Every forbidden dependency is shown with the file and line it comes from.

Every link carries a confidence level: **confirmed**, **possible** or **unresolved**. The map never presents a guess as a fact.

## Quick start

You need Python 3.9 or newer to run the tool, whatever language your project is in. The installer also fetches the tree-sitter grammars for the other languages (about 15 MB from PyPI, into `~/.pmap/venv`); add `--python-only` to skip them.

```bash
git clone https://github.com/lstcrsdd/method-project-map.git
cd method-project-map
./install.sh          # Windows: install.bat
```

Then open **Method Project Map** from Applications (macOS), the Start menu (Windows) or the app menu (Linux), or run `~/.pmap/bin/pmap ui`. The first time, it asks which project folder to show, and it offers a one-minute tour.

<details>
<summary>Installer options and running without installing</summary>

| | macOS / Linux | Windows |
|---|---|---|
| Install and connect Claude Code (default) | `./install.sh` | `install.bat` |
| …connect Codex / both agents | `./install.sh --codex` / `--all` | `install.bat --codex` / `--all` |
| Install without connecting any agent | `./install.sh --none` | `install.bat --none` |
| Skip the grammars for other languages | `./install.sh --python-only` | `install.bat --python-only` |
| Run from the downloaded folder | `./pmap-ui.sh`, or double-click `Method Project Map.command` on macOS | double-click `pmap-ui.bat` |

Everything goes to `~/.pmap`, plus the app shortcut. To undo: `pmap disconnect --all`, `pmap shortcut --remove`, then delete `~/.pmap`.
</details>

## What you can do

| | |
|---|---|
| **Browse in three levels**: overview → package → file. Inside a file you see three columns: who uses it, what it contains, what it uses. Click a function to keep only its own callers and callees. | ![File view](docs/images/file-en.png) |
| **Check the architecture**: describe the subsystems and the allowed dependencies in `design.yaml`. The Architecture view and `pmap check` show where the code breaks the rules; `pmap check --strict` fails a CI job when it does. | ![Architecture view](docs/images/design-en.png) |

More:

- switch between projects in Finder, Explorer or the recent list;
- search classes, functions and files by name;
- show the code of a single function;
- open a file in Finder or Explorer;
- export diagrams to Mermaid;
- save the whole map as one self-contained HTML page for a website or wiki;
- use the interface in **English and Russian**, with a built-in reference and a guided tour.

## Languages

| Language | What the map understands |
|---|---|
| **Python** | imports (relative, `src/` layouts, re-exports), calls through scopes and type annotations, inheritance — built in, no dependencies |
| **TypeScript / JavaScript** | ES modules and CommonJS, `tsconfig` path aliases, barrel re-exports, monorepo packages, JSX components |
| **Go** | `go.mod` modules, packages spread over files, methods on receivers, embedding, interfaces |
| **Java / Kotlin** | packages, single, wildcard and static imports, same-package classes, Kotlin top-level functions, both in one project |
| **C#** | block and file-scoped namespaces, `using`, `using static`, `global using`, aliases, records, top-level statements |
| **Swift** | SwiftPM and Xcode modules (files of a module see each other), `import`, protocols, extensions, `init` |
| **Dart / Flutter** | `package:` and relative imports, `export` barrels, `as` prefixes, `part` files, widgets, mixins, extensions |
| **Rust** | crates and workspaces, `mod` files, `use` with `crate::` / `self::` / `super::`, `pub use` re-exports, `impl` blocks and traits |
| **C / C++** | `#include`, namespaces and `using namespace`, classes, methods defined outside the class (`Type::m` in a .cpp), Win32 and C++/WinRT calls left to the headers |

One project may mix languages. Variable types are followed through declarations, constructors, the declared results of project functions (also along `a.b().c()` chains, `?`, `unwrap()`) and field types. In typed languages the compiler checks declared types, so a call on a project class is **confirmed** unless a subclass overrides the method; through an interface, protocol or trait it is **possible**. Values made by libraries are recognised as such: calls on them are library calls, not gaps in the map.

Measured on real code (calls the map judges; library calls excluded):

| Code | Files | Time | Confirmed | Possible | Unresolved |
|---|---|---|---|---|---|
| Flutter framework (Dart) | 1,712 | 20 s | 90% | 3% | 7% |
| Go `net/http` | 157 | 0.6 s | 73% | 4% | 23% |
| Rust `time` crate | 164 | 0.3 s | 65% | 4% | 31% |
| Rust `openmls` crate, library code | 263 | 0.5 s | 61% | 0% | 39% |

What stays unresolved is genuinely unknown to a static reader: code generated by macros, closures without types, values from functions without declared types. `pmap langs` shows which grammars are installed; `pmap langs install` adds them.

## For AI agents

```bash
pmap connect --claude      # or --codex, --all; undo with pmap disconnect --all
```

This installs a skill, a rule, a `SessionStart` hook (Claude Code) that hands the agent an overview of the project, and an MCP server with the tools `pmap_overview`, `pmap_find`, `pmap_show`, `pmap_source`, `pmap_check`, `pmap_stale` and `pmap_scan`. Every change is a marked block with a backup.

The same can be installed as a plugin:

```text
/plugin marketplace add lstcrsdd/method-project-map      # Claude Code
/plugin install project-map@method-project-map
```

Agents follow one protocol whatever the transport. Rely on `resolved` links, verify `inferred` ones, read the code for `unresolved`. See [docs/AGENT_PROTOCOL.md](docs/AGENT_PROTOCOL.md).

### Benchmark

The benchmark was run with Codex on a 29,000-line project, with hidden tests the agent never sees: 6 bug-fix tasks × 2 modes × 5 runs.

| | With the map / without |
|---|---|
| Fixes that pass the hidden tests | 60 of 60 in both modes |
| Code read by the agent | **0.55×** (95% CI 0.49–0.59) |
| Output tokens | 0.85× |
| Total input tokens | 0.93× (CI 0.83–1.02, not significant) |

Agents that were only *told* about the map almost never used it, so the overview is delivered at the start of the session. Reports: [bench/reports](bench/reports).

## Documentation

- **User guide**: [English](docs/guide.en.md) · [Русский](docs/guide.ru.md). Covers installation, reading the map, `design.yaml`, AI agents, every command and troubleshooting.
- **Agent protocol**: [docs/AGENT_PROTOCOL.md](docs/AGENT_PROTOCOL.md). Describes how agents get and use the map.
- **Changelog**: [CHANGELOG.md](CHANGELOG.md).
- **Map format (PMF 0.2)**: [spec/PMF-v0.md](spec/PMF-v0.md) and the [JSON Schema](spec/pmf-v0.schema.json).

## How it works

1. **Scan.** Python's own `ast` parser reads Python files, tree-sitter the other languages. Imports, calls and base classes are resolved through scopes, import roots (`src/` layouts are detected) and type annotations. A link that cannot be proven is marked `inferred`, with its candidates, or `unresolved`. It is never silently promoted.
2. **Map.** The result is an *actual map* in the PMF format (`.pmap/actual.json`), with file hashes so that a stale map is never served.
3. **Compare.** An optional *design map* (`design.yaml`) is compared with the actual map using the Reflexion model (Murphy & Notkin, 1995). The result is convergences, divergences and absences.
4. **Deliver.** The same map is served to people (web UI), agents (overview, CLI, MCP) and CI (`pmap check`).

## Repository layout

```text
pmap/            the tool: scanner, parsers (pmap/langs for languages besides Python), queries, Reflexion, web UI, MCP server
spec/            PMF 0.2 format specification and JSON Schema
docs/            user guides (EN/RU), agent protocol, screenshots
plugins/         generated plugin for Claude Code and Codex (scripts/build_plugin.py)
examples/        a small project with design.yaml and one deliberate architecture violation
bench/           agent benchmark: tasks, synthetic projects, hidden tests, reports
tests/           unit tests and one small project per language (python3 -m unittest discover -s tests)
```

## Limitations

- No type checker: calls on values whose type the map cannot see (untyped variables, library return values) stay unresolved.
- Generated or vendored code clutters the map; leave it out with `.pmapignore`.
- Static, closed-world analysis: no calls from code outside the repository, no monkey-patching, no `getattr` by name.
- The Windows installer, the Windows and Linux shortcuts and folder dialogs, and the Codex plugin install have not been tried on real machines yet; Swift and C++ have been checked on test projects only. Reports are welcome.

## Contributing

Issues and pull requests are welcome. Before sending a change, run `python3 -m unittest discover -s tests` on Python 3.9 or newer. After changing `pmap/`, rebuild the plugin with `python3 scripts/build_plugin.py`. Notes for coding agents are in [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE) © lstcrsdd · [Method Network](https://method-network.com/)
