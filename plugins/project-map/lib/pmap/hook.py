"""Agent hooks (Claude Code and Codex).

- `pmap hook session-start` gives a new session an overview of the project and caches the map;
- `pmap hook post-edit` tells the agent, right after an edit, what depends on the code it changed (pmap/hints.py);
- `pmap hook refresh <root>` rescans a project in the background after an edit.

A hook must never get in the way: outside code projects, in the home folder, on huge trees or on
any error it prints nothing and exits 0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

PROJECT_MARKERS = (".git", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", ".pmap", "design.yaml",
                   "PROJECT.mdm", "package.json", "tsconfig.json", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
                   "settings.gradle", "settings.gradle.kts", "Cargo.toml", "pubspec.yaml", "Package.swift",
                   "CMakeLists.txt", "meson.build", "Makefile")
PROJECT_SUFFIXES = (".sln", ".csproj", ".vcxproj", ".xcodeproj", ".xcworkspace")
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".pmap", "dist", "build", ".tox", ".mypy_cache"}
MAX_PY_FILES = 6000
# The overview is paid for on every model call of the session, and agents find files fast on their own
# (bench/reports): keep it to the layout, the links between languages and the gaps.
CONTEXT_CHARS = 4000


def _has_python_package(folder: Path) -> bool:
    try:
        return any(child.is_dir() and (child / "__init__.py").exists() for child in folder.iterdir()
                   if not child.name.startswith("."))
    except OSError:
        return False


def project_root(start: Path) -> Optional[Path]:
    home = Path.home().resolve()
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if candidate == home or candidate == Path(candidate.anchor):
            break
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS) or _has_suffix(candidate):
            return candidate
    # no project marker: a folder that directly holds Python packages is a project too (other languages
    # always come with a marker: package.json, go.mod, pom.xml, build.gradle, Cargo.toml, pubspec.yaml, *.csproj ...)
    if current != home and current != Path(current.anchor) and _has_python_package(current):
        return current
    return None


def _has_suffix(folder: Path) -> bool:
    try:
        return any(child.suffix in PROJECT_SUFFIXES for child in folder.iterdir())
    except OSError:
        return False


def count_source_files(root: Path, limit: int, mapped_only: bool = False) -> int:
    """Python files plus files of the other supported languages (only those whose grammar is installed, if asked)."""
    from pmap import langs

    exts = {".py"} | {ext for lang, spec in langs.LANGUAGES.items() for ext in spec["exts"]
                      if not mapped_only or langs.available(lang)}
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS | langs.SKIP_DIRS and not d.startswith("._")]
        count += sum(1 for f in filenames if os.path.splitext(f)[1] in exts and not f.startswith("._"))
        if count > limit:
            break
    return count


def count_python_files(root: Path, limit: int) -> int:  # kept for callers of earlier versions
    return count_source_files(root, limit)


def session_start_context(cwd: Path) -> Optional[str]:
    root = project_root(cwd)
    if root is None:
        return None
    files = count_source_files(root, MAX_PY_FILES, mapped_only=True)
    if files == 0:
        return None
    from pmap.connect import launcher_path

    launcher = launcher_path()
    cmd = str(launcher) if launcher.exists() else "pmap"
    header = (
        f"Method Project Map (pmap) — static map of this repository ({root.name}), for orientation before reading "
        f"files. Details on demand: `{cmd} find <name>`, `{cmd} show <node-id|path>`, `{cmd} source <node-id>` "
        "(or the pmap_* MCP tools). `resolved` links are proven; verify `inferred` ones. After you edit code, "
        "the map adds a short note naming what depends on it in other files and languages. Skill: project-map.\n\n"
    )
    if files > MAX_PY_FILES:
        return header + f"(Large repository: more than {MAX_PY_FILES} source files; run `{cmd} context .` when needed.)"
    from pmap.hints import SYNC_SCAN_FILES, current_map
    from pmap.loader import find_design
    from pmap.mermaid import format_agent_context

    actual = current_map(root, scan=files <= SYNC_SCAN_FILES)
    if actual is None:  # a big project without a map yet: do not hold up the session
        return header + (f"(The map of this repository is being built in the background: the overview comes with the "
                         f"next session, the notes after edits as soon as it is ready. `{cmd} context .` builds it now.)")
    design = None
    try:
        design = find_design(root)
    except Exception:
        design = None
    return header + format_agent_context(actual, design=design, max_chars=CONTEXT_CHARS)


def run(event: str, stdin_text: str, target: Optional[str] = None) -> int:
    if event == "refresh":
        try:
            from pmap.hints import refresh_cache

            if target:
                refresh_cache(Path(target))
        except Exception:
            pass
        return 0
    if event not in ("session-start", "post-edit"):
        return 0
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        return 0
    try:
        if event == "post-edit":
            from pmap.hints import post_edit_context

            context, name = post_edit_context(payload), "PostToolUse"
        else:
            cwd = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
            context, name = session_start_context(cwd), "SessionStart"
    except Exception:
        return 0
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "session-start", sys.stdin.read()))
