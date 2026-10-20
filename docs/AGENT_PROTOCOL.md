# Method Project Map Agent Protocol (PMAP-1)

How a coding agent (Claude Code, Codex, or any MCP-capable agent) gets and uses a project map. The same rules hold for every transport (hook, CLI, MCP), so an agent behaves the same whichever is available.

## 1. Delivery: the map comes to the agent

Benchmarks and real sessions showed that agents almost never call map tools on their own. With the overview placed in the prompt, they read about half as much code (0.55x, 95% CI 0.49–0.59), fix quality stays the same, and total tokens do not change measurably. So the map is **pushed**, not merely offered, at the two moments where it adds something an agent does not see by itself:

1. **After an edit** (`PostToolUse`): what depends on the code just changed — callers in other files and the other side of every link between languages (section 5).
2. **At session start** (`SessionStart`): a short overview — languages, links between languages and gaps, then packages and files.

| Agent | Mechanism | Installed by |
|---|---|---|
| Claude Code | `SessionStart` → `pmap hook session-start`; `PostToolUse` on `Edit\|Write\|MultiEdit` → `pmap hook post-edit`; both answer with `hookSpecificOutput.additionalContext` | `pmap connect --claude` or the plugin |
| Codex | the same two hooks in `~/.codex/hooks.json` (`apply_patch` edits); Codex runs them once they are trusted in `/hooks` | `pmap connect --codex` |
| Gemini CLI | rule in `~/.gemini/GEMINI.md` (read by every session) + MCP server in `~/.gemini/settings.json` | `pmap connect --gemini` |
| Any agent | `pmap context <repo> --max-chars 8000` or MCP `pmap_overview` | — |

The hook acts only in code projects, which it recognises in two ways:
- a project marker (`.git`, `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `package.json`, `tsconfig.json`, `go.mod`, `pom.xml`, `build.gradle(.kts)`, `settings.gradle(.kts)`, `Cargo.toml`, `pubspec.yaml`, `Package.swift`, `CMakeLists.txt`, `*.sln`, `*.csproj`, `*.vcxproj`, `*.xcodeproj`, `design.yaml`, `.pmap`);
- or a folder that directly contains a Python package.

Languages besides Python (TypeScript / JavaScript, Go, Java, Kotlin, C#, Swift, Dart, Rust, C / C++) are on the map when their tree-sitter grammars are installed (`pmap langs install`); the overview names any language that is not.

It stays silent in the home folder, outside projects, and on any error. Output is kept under Claude Code's 10,000-character limit.

Both hooks share a cached map in `~/.pmap/cache` (one file per project, outside the repository), reused while the files are unchanged and refreshed in the background after every edit. A large project without a map is scanned in the background rather than holding up the session.

## 2. Overview format

The overview is plain text built by progressive disclosure (spec section 6B), about 4,000 characters. It unfolds in this order:
0. Languages, then the links between languages (one line per channel or kind of link) with gaps first (`[GAP] channel …: "status" is called from Dart (…) but Kotlin (android) does not handle it (…)`).
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
- `mod:<package.module>` for a Python module, `mod:<file path>` for a file in another language;
- `sym:<package.module>:<Name>` / `sym:<file path>:<Name>` for a class or function;
- `sym:<package.module>:<Class>.<method>` / `sym:<file path>:<Class>.<method>` for a method.

A file path or a qualified name also works.

## 4. Trust rules (normative)

1. `resolved`: the link is proven statically. Agents may rely on it.
2. `inferred`: candidates exist (annotated parameter, overridable method, several bindings), listed in `candidates`. Agents must verify them in code.
3. `unresolved`: the target is unknown. The map says nothing about it; agents read the code.
4. A stale map is never served silently. Every transport rescans when files have changed.
5. The map is not a substitute for reading the code that is about to change. It decides *what* to read.

## 5. After changes

**The note after an edit.** After Claude Code's Edit / Write or Codex's apply_patch, `pmap hook post-edit` finds the declarations the edit touched (by line; a deleted or renamed declaration by the name its old text declared) and lists what depends on them outside the file:
- `→ path:line  Name  (Language, channel app.vpn/control "connect")` — the other side of a link this code makes;
- `← path:line  Name  (Language, C ABI vpn_core_start)` — code in another language that reaches this declaration; for a channel handler only the callers of the branches the edit touched;
- `! "status" is not handled in Kotlin (android)` — a gap this code is part of;
- `used in N other files` — callers in other files: all of them when the edit touched the declaration's head or removed it, one summary line for an edit inside the body.

It is silent when nothing outside the file depends on the change, and each declaration is noted at most once per session (twice if a body edit is followed by a head edit). Agents should update or check what the note names before finishing the task.

**Links between languages.** A link through a channel, a C symbol, JNI or a generated binding is checked by no compiler. When one side changes (a method renamed, an argument added, a symbol's signature changed), change the other side in the same task. `pmap check .` lists every channel method a platform does not handle, with or without a design.

If `design.yaml` exists, run `pmap check .`. A new *divergence* means a dependency the design does not allow. Fix it unless the task is to change the architecture.

A project designed in mDm has `PROJECT.mdm`; without `design.yaml` it is the design (spec section 4.1). After building a function, record where it lives: a `код: <paths>` line under the function (` · код: <paths>` on an object), or the mDm MCP tool `set_code`. Then `pmap check .`. A divergence there means the code wires together what the scheme keeps apart: fix the code; if the scheme is what is wrong, record a question for its author (a `G… ?` line or the mDm tool `add_gap`) instead of editing the scheme.

## 6. Installation footprint

`pmap connect` writes only these:
- `~/.pmap/` — a copy of `pmap` and the `pmap` / `pmap.cmd` launchers; at run time also `cache/` (one map per project) and `sessions/` (which notes a session has had, kept two days);
- `~/.claude/skills/project-map/` and `~/.codex/skills/project-map/` — the skill;
- `~/.claude/settings.json` — one `SessionStart` and one `PostToolUse` entry;
- `~/.codex/hooks.json` — the same two entries for Codex;
- `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` — a `<!-- pmap:start -->…<!-- pmap:end -->` block;
- `~/.codex/config.toml` — a `# >>> pmap … # <<< pmap` block;
- `~/.gemini/settings.json` — an `mcpServers.pmap` entry, and `~/.gemini/GEMINI.md` — a marked rule block;
- the user-scope MCP registration, made through `claude mcp add` when the CLI exists.

Before the first change, each config file is backed up as `<name>.bak-pmap`. `pmap disconnect --all` removes exactly these additions.
