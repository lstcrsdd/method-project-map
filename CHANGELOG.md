# Changelog

## 0.6.2 — 2026-10-27

Two fixes to the note after an edit, both found by replaying the 25 edits Codex made in a 48-run benchmark:

- A Codex patch whose hunk has unchanged lines between its changes was not undone, so the note could not tell what the edit changed and kept showing a JNI difference the edit had just fixed. Each hunk is now undone as one block, unchanged lines included; on the replayed edits the note now says `✓ the two sides agree now` there.
- An edit that changed several files at once (a C function, its header and every call) was compared one file at a time, so a change that kept every side in agreement was reported as `✓ the two sides agree now`. All files of one edit are now put back together before the comparison; that edit now gets no such line.

## 0.6.1 — 2026-10-26

- **The note after an edit says what the edit changed between languages**: a gap it closed or opened, a JNI or C function whose two sides now agree or differ. The files on that link (both ends, the files that spell out its name, the Dart helpers that send on the channel) are mapped twice, with the edited file as it was and as it is, so the line comes from the edit alone; a link those files cannot show is missed, not guessed. The old text is rebuilt from the Edit or apply_patch that made the change; after a whole-file Write the note says nothing about it. About 0.1 s on a plugin and 0.4 s on the 3,000 files of the Flutter SDK's packages. In the cross-language benchmark the note had said nothing on the fixes in the Kotlin and C++ channel handlers; on those fixes it now says `✓ gap closed: …`, and on the Rust side of a JNI fix `✓ the two sides agree now: …`. A fix to the keys of an event payload still gets no such line: the map does not compare payload keys.
- The note no longer shows a gap, a difference or a link that the edit has just undone (the cached map is refreshed in the background after each edit).
- `pmap twins` no longer lists the handlers of one Flutter channel on different platforms as copies: they are the two ends of one link, and a branch one of them lacks is a gap.

## 0.6.0 — 2026-10-25

**The same logic in several languages.** An app shipped on several platforms often has one piece of logic written by hand in each language. `pmap twins` finds such copies: functions in different languages that use the same rare string literals and constants (resolved through each language's constants), with where each is reached from. Pairs where one side writes the keys and the other reads them are listed apart as two ends of one format. Tests, generated and vendored files are skipped; about a second on a 2,000-file repository. `pmap twins --exec --config twins.json` builds and runs the copies on the same inputs, taken from the repository's own tests (string literals in any language that match a pattern), compares the outputs key by key and can pass each output to a judge, a program that checks it. For each difference it names the line in every copy that writes the key and when that line last changed. It reports differences, not bugs: the reference copy is only the base of the comparison. A difference counts as documented only when a comment or a `*.md` line says it is intended; `--strict` fails a CI job on any other. Test strings it cannot use as they are (interpolated, concatenated) are listed as not executed. On a team's client app written in Swift, C#, Java and Rust, the first run found that the Swift copy kept the brackets of an IPv6 host and passed ports above 65535 through; the app's own config checker refused those outputs. After an edit, the note names the other copies of the function that changed.

**Signatures and names that differ.** A link whose two ends are both known can still disagree, and no compiler sees it. `pmap check .` lists, in a new section, and `--strict` fails on: a JNI function that takes a different number of parameters than its Kotlin or Java declaration, or a parameter of a clearly different type; a C ABI function called or declared with another number of arguments than its Rust or C definition takes (header, `dart:ffi` binding, Swift, C, C++ or Go call); a channel that Dart uses under one name while a platform registers a near name that no Dart code uses. A gap also names the method the platform handles instead, when no Dart code sends it. Only certain counts are compared; on real code (the Flutter SDK, 111 pub.dev packages with native code, Go's cgo tests, four apps) the checks reported nothing and every compared pair agreed. The overview marks these `[MISMATCH]`, the note after an edit shows them on the link, and the web map lists them under the gaps. In the map file they are a `mismatch` key on the link (`bridge`), and gaps may carry `orphan`.

**Fix.** With an iOS and a macOS NetworkExtension provider, each app is linked to the provider in its own platform folder (0.5 linked the macOS app to both and showed the iOS one).

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
