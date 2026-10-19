# Changelog

## Unreleased

**mDm schemes as the design.** Without a `design.yaml`, a `PROJECT.mdm` written by [mDm](https://method-network.com/) is the design map (spec section 4.1):

- functions and objects with a `код:` line become subsystems (`f-2-1`, `o-zayavka`); folders, `file:Name` and node ids in `код:` resolve to files;
- the scheme's wiring is what the code may use: a function → its objects (required), functions joined by an object, a function and its subfunctions, objects handled by one function (permitted either way);
- anything else is a divergence, reported with the function names; functions without `код:` are listed in a warning;
- the agent overview opens with the functions and their paths; `PROJECT.mdm` marks a code project for the hook.

**Format:** an allowed dependency may say `required: false` — it is permitted but its absence is not reported. Optional, so the format version stays 0.2.0.

## 0.4.0 — 2026-10-19

**More languages:** Swift, Dart / Flutter, Rust and C / C++ (Win32, C++/WinRT).

- **Swift:** SwiftPM and Xcode modules, protocols, extensions.
- **Dart:** `package:` imports, `export` barrels, prefixes, `part` files, widgets, mixins and extensions.
- **Rust:** crates and workspaces, `mod` files, `use` paths, `pub use`, `impl` blocks and traits, `Self`, `?` and `unwrap()`.
- **C / C++:** `#include`, namespaces, methods defined outside their class (header declarations give way to definitions).

**Gemini CLI:** `pmap connect --gemini` (also part of `--all`) adds the MCP server to `~/.gemini/settings.json` and a rule to `~/.gemini/GEMINI.md`.

**Better links in every typed language:**
- A call through a variable or field of a project class is now **confirmed** when no subclass overrides the method, because the compiler checks declared types. Through an interface, protocol or trait it stays **possible**. Python keeps its rule, since annotations are not checked.
- Types follow call chains (`a.b().c()`), fields of variables, getters and static fields in the middle of a chain, enum variants and generic bounds (`P: Trait`, `impl Trait`).
- Values made by library calls, and methods inherited from library types, are library calls, not gaps in the map.

**Measured on real code** (share of the calls the map judges):

| Code | Confirmed / possible / unresolved |
|---|---|
| Flutter framework | 90% / 3% / 7% |
| Go `net/http` | 73% / 4% / 23% |
| Rust `time` | 65% / 4% / 31% |
| Rust `openmls`, library code | 61% / 0% / 39% |

## 0.3.0 — 2026-10-19

**Languages besides Python.** TypeScript / JavaScript, Go, Java, Kotlin and C# are mapped with tree-sitter, with the same confidence rules as Python (resolved / inferred / unresolved). One project may mix languages.

- TypeScript / JavaScript: ES modules and CommonJS, `tsconfig` / `jsconfig` path aliases, barrel re-exports, monorepo packages, JSX components.
- Go: `go.mod` modules, packages spread over files, methods on receivers, embedding, interfaces.
- Java / Kotlin: packages, single, wildcard and static imports, same-package classes, Kotlin top-level functions.
- C#: block and file-scoped namespaces, `using`, `using static`, `global using`, aliases, records, top-level statements.
- Variable types come from declarations, constructors and the declared result types of project functions, also through fields.
- The grammars are optional: `pmap langs` shows what is installed, `pmap langs install` puts them into `~/.pmap/venv`. The installers do it by default; `--python-only` skips it.
- The map gets a `languages` section, and files parsed with errors are reported as `partial_parse`. The format version stays 0.2.0 because both fields are optional.

**Interface.**
- An English / Russian switch, a one-minute guided tour and a built-in reference.
- A project switcher with the system folder dialog, a typed path and recent projects.
- *Show in Finder / Explorer*.
- Shorter package labels for Java-style source trees and a languages panel.
- Link options for published maps: `?lang=`, `?tour=0`, `#open=`, `#select=` and `#view=design`.

**Start and install.**
- `pmap ui` and a **Method Project Map** app shortcut on macOS, Windows and Linux. They open the last project and reuse a running map. The map stops on its own after its tab is closed.
- Launchers run Python in isolated mode, so a project's own `pmap` or `json.py` can never be imported by the map.

**Docs.** User guides in English and Russian, `README.ru.md`, screenshots.

## 0.2.0 — 2026-10-19

First public version: the Python scanner with explicit confidence levels, the PMF 0.2 format and JSON Schema, the Reflexion check against `design.yaml`, the block-diagram web UI, an MCP server, the session-start overview for Claude Code and Codex, the plugin, and the agent benchmark.
