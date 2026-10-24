# Changelog

## 0.5.1 — 2026-10-24

Documentation, examples and test projects. The map, its links, gaps and notes are the same as in 0.5.0.

- The examples in the README, the guide, the format specification, the agent skill and the web map's help use an audio player now: [examples/audio_player](examples/audio_player), a Flutter plugin (Dart, Swift, Kotlin, C++) whose Windows side has no branch for `setSpeed`. The outputs shown are what `pmap check .` and the note after an edit print on it. Like the test projects, the example is left out of this repository's own map (`.pmapignore`).
- The test projects for Swift, Dart, Rust, C++ and the links between languages are renamed (`tests/fixtures/langs/*_player`) and describe an audio player. Their maps have the same nodes, links and gaps as before the rename.

## 0.5.0 — 2026-10-20

**Links between languages.** The map follows the calls that no compiler checks, so a Flutter app is no longer a set of islands, one per language:

- Flutter platform channels: `MethodChannel`, `EventChannel` and `BasicMessageChannel` calls in Dart reach the Swift, Kotlin, Java and C++ branch that handles each method (`case "connect":`, `"connect" ->`, `call.method_name() == "connect"`), and native calls back into Dart; channel names may come from constants.
- The C ABI: `dart:ffi` lookups and `@Native`, and Swift, C/C++ and Go (cgo) calls, reach `#[no_mangle]` / `export_name` Rust functions, C definitions, `@_cdecl` Swift and `//export` Go functions. A C++ call bound to a header declaration now goes to the definition in the other language.
- Pigeon: the generated `*.g.dart` stays on the map, and a call through the generated API reaches the app's Swift, Kotlin or Java implementation of it (checked on url_launcher_ios and url_launcher_android).
- JNI (`external fun` / `native` to `Java_…` functions), flutter_rust_bridge, UniFFI and Tauri commands.
- Hand-offs through the system, as possible links: Apple NetworkExtension, from the app that starts its extension to the extension's provider; Android `startService(Intent(…, X::class.java))` to `X.onStartCommand`.
- Real plugins wrap the call, and the map follows them: helpers that take the method name (`_call('pause', id)`, an extension `call` on `MethodChannel`), files that serve two channels (told apart by the handler each channel is given), `let method = call.method` aliases, handlers that answer every method alike. Checked on pub.dev plugins: audioplayers (Dart, Kotlin, Swift, C++) gets 73 links, all confirmed, each of its 23 channel methods reaching its branch on every platform; connectivity_plus gets 7.
- A channel method that one platform does not handle is a **gap**, a crash at run time: `[GAP]` in the overview, listed by `pmap check .` even without a design (`--strict` fails on it).
- In the map file such edges carry `bridge` (`via`, `name`, `method`, `to`, `missing`); the field is optional, so the format version stays 0.2.0.

**Notes after edits.** `pmap hook post-edit` runs after Claude Code's Edit / Write and Codex's apply_patch. In a few lines it tells the agent what depends on the code it just changed: callers in other files and the other side of every link between languages. It says nothing when nothing outside the file depends on the change, and never repeats itself within a session. It reads a compact index cached next to the map: 0.4 s on the 1,700-file Flutter framework. `pmap connect` adds it for Claude Code (`~/.claude/settings.json`) and Codex (`~/.codex/hooks.json`; trust it once in `/hooks`); the plugin carries it too.

**Session start.** Codex now gets the overview at session start as well (a `SessionStart` hook). The overview is shorter (4,000 characters instead of 7,500: the benchmark showed it costs as many tokens as it saves) and leads with the links between languages. The map is cached in `~/.pmap/cache` and reused while the files are unchanged; a large project without a map is scanned in the background instead of holding up the session.

**C headers** wrapped in `#ifdef __cplusplus` / `extern "C" {` parse without errors.

**mDm schemes as the design.** Without a `design.yaml`, a `PROJECT.mdm` written by [mDm](https://method-network.com/) is the design map (spec section 4.1):

- functions and objects with a `код:` line become subsystems (`f-2-1`, `o-zayavka`); folders, `file:Name` and node ids in `код:` resolve to files;
- the scheme's wiring is what the code may use: a function → its objects (required), functions joined by an object, a function and its subfunctions, objects handled by one function (permitted either way);
- objects with the same `код:` are one data type («Заявка» and «Проверенная заявка» share one class) and one subsystem;
- anything else is a divergence, reported with the function names; functions without `код:` are listed in a warning;
- the agent overview opens with the functions and their paths; `PROJECT.mdm` marks a code project for the hook.

**Honest check:** `pmap check` names every language whose grammar is missing — those files are not on the map, so "no divergences" does not cover them.

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
