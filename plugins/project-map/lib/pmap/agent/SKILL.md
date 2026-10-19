---
name: project-map
description: Navigate code repositories (Python, TypeScript/JavaScript, Go, Java, Kotlin, C#, Swift, Dart, Rust, C/C++) with Method Project Map (pmap), a static, LLM-free map of packages, files, classes, functions and the call/import links between them. Use when starting work in a codebase, locating where behaviour is implemented, tracing callers and callees, or checking architecture rules — before reading many files.
license: MIT
---

# Method Project Map (pmap)

`pmap` reads the code statically and answers "where is it and what touches it" without reading whole files. Measured on 29k-line codebases it halves the code an agent has to read, with no loss of fix quality. Python is built in; TypeScript/JavaScript, Go, Java, Kotlin, C#, Swift, Dart, Rust and C/C++ need the optional grammars (`{{PMAP}} langs` shows what is installed, `{{PMAP}} langs install` adds them).

Command: `{{PMAP}}` (run from the repository root; `--repo <dir>` elsewhere). The same operations exist as MCP tools `pmap_overview`, `pmap_find`, `pmap_show`, `pmap_source`, `pmap_check` when the MCP server is connected.

## When starting a task

1. Orient. In Claude Code an overview may already be in context ("Method Project Map (pmap) — static map…"). Otherwise run `{{PMAP}} context . --max-chars 8000`: packages, then files with one-line descriptions, then signatures.
2. Locate instead of grepping blindly: `{{PMAP}} find <name>` (exact names, `Class.method`, or a guessed name — it suggests close matches).
3. Inspect one node: `{{PMAP}} show <node-id | file path>` — location, signature, members, what it uses and who uses it, with line numbers. Python ids look like `sym:pkg.module:Class.method`; other languages use the file path: `sym:src/billing/invoice.ts:InvoiceService.issue`.
4. Read only what you need: `{{PMAP}} source <node-id>` prints just that function or class. Open whole files only when the surrounding code matters.

Use plain text search as well when a symptom names literal strings, config keys or data; the map complements search, it does not replace reading the code you change.

## Trust rules

- `resolved` links are proven statically — rely on them.
- `inferred` links are candidates (calls through an interface / protocol / trait, methods a subclass overrides, Python annotations, several possible bindings) — verify in code.
- `unresolved` calls have unknown targets (dynamic dispatch, callbacks, untyped variables) — the map is silent there; read the code.
- Calls into libraries and the standard library get no edge; the import records the dependency.
- The map never invents edges, but it cannot see code outside the repository, monkey-patching or `getattr` tricks.
- Maps refresh themselves when files change; `{{PMAP}} stale` lists files changed since the last saved scan.

## After changing code

If the repository has a `design.yaml`, run `{{PMAP}} check .` and fix any new *divergence* (a dependency the design does not allow) unless the task is to change the architecture.

A project designed in mDm has a `PROJECT.mdm` (functions, the objects they take and give). Without `design.yaml` it is the design: after building a function, write `код: <paths>` under it (or call the mDm MCP tool `set_code`), then `{{PMAP}} check .`. A divergence means the code wires together what the scheme keeps apart; fix the code, or record a question for the scheme's author (`G… ?` line / `add_gap`) rather than editing the scheme.

## For people

`{{PMAP}} ui .` opens an interactive block-diagram of the project in the browser; `{{PMAP}} html . -o map.html` writes it as one shareable file.
