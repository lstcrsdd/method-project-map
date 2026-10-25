---
name: project-map
description: Navigate code repositories (Python, TypeScript/JavaScript, Go, Java, Kotlin, C#, Swift, Dart, Rust, C/C++) with Method Project Map (pmap), a static, LLM-free map of packages, files, classes, functions and the call/import links between them — including links between languages that no compiler checks (Flutter platform channels, Pigeon, FFI, JNI, flutter_rust_bridge, UniFFI, Tauri). Use when starting work in a codebase, locating where behaviour is implemented, tracing callers and callees across languages, checking what a change affects, or checking architecture rules — before reading many files.
license: MIT
---

# Method Project Map (pmap)

`pmap` reads the code statically and answers "where is it and what touches it" without reading whole files — also across languages: from a Dart `invokeMethod('connect')` to the Swift, Kotlin and C++ branches that handle it, from Swift or C++ to the Rust function behind a C symbol. Python is built in; TypeScript/JavaScript, Go, Java, Kotlin, C#, Swift, Dart, Rust and C/C++ need the optional grammars (`pmap langs` shows what is installed, `pmap langs install` adds them).

Command: `pmap` (on PATH in Claude Code with this plugin; otherwise install the CLI with install.sh / install.bat). Run it from the repository root, or pass `--repo <dir>`. When using the MCP tools, pass `repo` = the absolute repository root. The same operations exist as MCP tools `pmap_overview`, `pmap_find`, `pmap_show`, `pmap_source`, `pmap_check` when the MCP server is connected.

## When starting a task

1. Orient. In Claude Code an overview may already be in context ("Method Project Map (pmap) — static map…"). Otherwise run `pmap context . --max-chars 8000`: packages, then files with one-line descriptions, then signatures.
2. Locate instead of grepping blindly: `pmap find <name>` (exact names, `Class.method`, or a guessed name — it suggests close matches).
3. Inspect one node: `pmap show <node-id | file path>` — location, signature, members, what it uses and who uses it, with line numbers. Python ids look like `sym:pkg.module:Class.method`; other languages use the file path: `sym:src/billing/invoice.ts:InvoiceService.issue`.
4. Read only what you need: `pmap source <node-id>` prints just that function or class. Open whole files only when the surrounding code matters.

Use plain text search as well when a symptom names literal strings, config keys or data; the map complements search, it does not replace reading the code you change.

## Trust rules

- `resolved` links are proven statically — rely on them.
- `inferred` links are candidates (calls through an interface / protocol / trait, methods a subclass overrides, Python annotations, several possible bindings) — verify in code.
- `unresolved` calls have unknown targets (dynamic dispatch, callbacks, untyped variables) — the map is silent there; read the code.
- Calls into libraries and the standard library get no edge; the import records the dependency.
- The map never invents edges, but it cannot see code outside the repository, monkey-patching or `getattr` tricks.
- Maps refresh themselves when files change; `pmap stale` lists files changed since the last saved scan.

## Links between languages

A Flutter app reaches Swift, Kotlin and C++ through a channel name and a method name (plain strings); Dart, Swift, C++ and Kotlin reach Rust or C through symbol names (FFI, JNI), Pigeon, flutter_rust_bridge, UniFFI or Tauri commands. No compiler checks these, so a rename on one side breaks the app at run time only. The map links both sides (`show` prints `via channel audio/player "pause" -> ios/Classes/AudioPlayerPlugin.swift:88`), the overview lists them, and a channel method that some platform does not handle is a **gap** (`[GAP]` in the overview, `pmap check .`). When you change one side of a link, change or check the other side in the same task.

## After changing code

After each edit, a short note may appear ("Method Project Map — code elsewhere depends on what you just changed"): callers in other files and the other side of every link between languages for the declarations you touched. Read it and update or check what it names before you finish; it is silent when nothing outside the file depends on the change. When it lists the same logic in other languages (a function written by hand again in Swift, Kotlin, Rust…), decide whether the same change is needed there; `pmap twins .` lists such copies, and `pmap twins . --exec --config twins.json` runs them on the same inputs when the project has a config.

If the repository has a `design.yaml`, run `pmap check .` and fix any new *divergence* (a dependency the design does not allow) unless the task is to change the architecture.

A project designed in mDm has a `PROJECT.mdm` (functions, the objects they take and give). Without `design.yaml` it is the design: after building a function, write `код: <paths>` under it (or call the mDm MCP tool `set_code`), then `pmap check .`. A divergence means the code wires together what the scheme keeps apart; fix the code, or record a question for the scheme's author (`G… ?` line / `add_gap`) rather than editing the scheme.

## For people

`pmap ui .` opens an interactive block-diagram of the project in the browser; `pmap html . -o map.html` writes it as one shareable file.
