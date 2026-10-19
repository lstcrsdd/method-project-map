---
name: project-map
description: Navigate code repositories (Python, TypeScript/JavaScript, Go, Java, Kotlin, C#) with Method Project Map (pmap), a static, LLM-free map of packages, files, classes, functions and the call/import links between them. Use when starting work in a codebase, locating where behaviour is implemented, tracing callers and callees, or checking architecture rules — before reading many files.
license: MIT
---

# Method Project Map (pmap)

`pmap` reads the code statically and answers "where is it and what touches it" without reading whole files. Measured on 29k-line codebases it halves the code an agent has to read, with no loss of fix quality. Python is built in; TypeScript/JavaScript, Go, Java, Kotlin and C# need the optional grammars (`pmap langs` shows what is installed, `pmap langs install` adds them).

Command: `pmap` (on PATH in Claude Code with this plugin; otherwise install the CLI with install.sh / install.bat). Run it from the repository root, or pass `--repo <dir>`. When using the MCP tools, pass `repo` = the absolute repository root. The same operations exist as MCP tools `pmap_overview`, `pmap_find`, `pmap_show`, `pmap_source`, `pmap_check` when the MCP server is connected.

## When starting a task

1. Orient. In Claude Code an overview may already be in context ("Method Project Map (pmap) — static map…"). Otherwise run `pmap context . --max-chars 8000`: packages, then files with one-line descriptions, then signatures.
2. Locate instead of grepping blindly: `pmap find <name>` (exact names, `Class.method`, or a guessed name — it suggests close matches).
3. Inspect one node: `pmap show <node-id | file path>` — location, signature, members, what it uses and who uses it, with line numbers. Python ids look like `sym:pkg.module:Class.method`; other languages use the file path: `sym:src/billing/invoice.ts:InvoiceService.issue`.
4. Read only what you need: `pmap source <node-id>` prints just that function or class. Open whole files only when the surrounding code matters.

Use plain text search as well when a symptom names literal strings, config keys or data; the map complements search, it does not replace reading the code you change.

## Trust rules

- `resolved` links are proven statically — rely on them.
- `inferred` links are candidates (typed parameters, overridable methods, several possible bindings) — verify in code.
- `unresolved` calls have unknown targets (dynamic dispatch, callbacks, untyped variables) — the map is silent there; read the code.
- Calls into libraries and the standard library get no edge; the import records the dependency.
- The map never invents edges, but it cannot see code outside the repository, monkey-patching or `getattr` tricks.
- Maps refresh themselves when files change; `pmap stale` lists files changed since the last saved scan.

## After changing code

If the repository has a `design.yaml`, run `pmap check .` and fix any new *divergence* (a dependency the design does not allow) unless the task is to change the architecture.

## For people

`pmap ui .` opens an interactive block-diagram of the project in the browser; `pmap html . -o map.html` writes it as one shareable file.
