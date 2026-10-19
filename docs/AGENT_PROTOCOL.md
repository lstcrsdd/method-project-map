# Method Project Map Agent Protocol (PMAP-1)

How a coding agent (Claude Code, Codex, or any MCP-capable agent) gets and uses a project map. The same rules hold for every transport (hook, CLI, MCP), so an agent behaves the same whichever is available.

## 1. Delivery: the overview comes to the agent

Benchmarks showed that agents almost never call map tools on their own. With the overview placed in the prompt, they read about half as much code (0.55x, 95% CI 0.49–0.59), and fix quality stays the same. So the overview is **pushed**, not merely offered:

| Agent | Mechanism | Installed by |
|---|---|---|
| Claude Code | `SessionStart` hook → `pmap hook session-start` → `hookSpecificOutput.additionalContext` | `pmap connect --claude` or the plugin |
| Codex | rule in `~/.codex/AGENTS.md`: run `pmap context` at task start | `pmap connect --codex` |
| Any agent | `pmap context <repo> --max-chars 8000` or MCP `pmap_overview` | — |

The hook acts only in Python projects, which it recognises in two ways:
- a project marker (`.git`, `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `design.yaml`, `.pmap`);
- or a folder that directly contains a Python package.

It stays silent in the home folder, outside projects, and on any error. Output is kept under Claude Code's 10,000-character limit.

## 2. Overview format

The overview is plain text built by progressive disclosure (spec section 6B). It unfolds in this order:
1. Packages, with their description and `uses:`.
2. Files, one line each: description, main names, length.
3. Signatures.
4. Methods.

Within each level, the most widely used code comes first. A final line says what was cut and how to get it.

## 3. On-demand queries

| Need | CLI | MCP tool |
|---|---|---|
| Overview | `pmap context . --max-chars N` | `pmap_overview {max_chars}` |
| Locate a symbol (exact, `Class.method`, or a guess) | `pmap find <name>` | `pmap_find {name}` |
| One node: location, signature, members, uses / used by | `pmap show <node-id \| path>` | `pmap_show {ref}` |
| Read one symbol instead of a whole file | `pmap source <node-id>` | `pmap_source {ref, context_lines}` |
| Architecture check against `design.yaml` | `pmap check .` | `pmap_check {design?}` |
| What changed since the saved scan | `pmap stale .` | `pmap_stale` |
| Save a fresh map | `pmap scan .` | `pmap_scan` |

The MCP tools take an optional `repo`. It defaults to `$PMAP_REPO`, then `$CLAUDE_PROJECT_DIR`, then the server's working directory. Pass it explicitly when the server's working directory is not the project, as with Codex plugins.

Node ids look like this:
- `mod:<package.module>` for a module;
- `sym:<package.module>:<Name>` for a class or function;
- `sym:<package.module>:<Class>.<method>` for a method.

A file path or a qualified name also works.

## 4. Trust rules (normative)

1. `resolved`: the link is proven statically. Agents may rely on it.
2. `inferred`: candidates exist (annotated parameter, overridable method, several bindings), listed in `candidates`. Agents must verify them in code.
3. `unresolved`: the target is unknown. The map says nothing about it; agents read the code.
4. A stale map is never served silently. Every transport rescans when files have changed.
5. The map is not a substitute for reading the code that is about to change. It decides *what* to read.

## 5. After changes

If `design.yaml` exists, run `pmap check .`. A new *divergence* means a dependency the design does not allow. Fix it unless the task is to change the architecture.

## 6. Installation footprint

`pmap connect` writes only these:
- `~/.pmap/` — a copy of `pmap` and the `pmap` / `pmap.cmd` launchers;
- `~/.claude/skills/project-map/` and `~/.codex/skills/project-map/` — the skill;
- `~/.claude/settings.json` — one `SessionStart` entry;
- `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` — a `<!-- pmap:start -->…<!-- pmap:end -->` block;
- `~/.codex/config.toml` — a `# >>> pmap … # <<< pmap` block;
- the user-scope MCP registration, made through `claude mcp add` when the CLI exists.

Before the first change, each config file is backed up as `<name>.bak-pmap`. `pmap disconnect --all` removes exactly these additions.
