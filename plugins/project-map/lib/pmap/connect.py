"""Connect Method Project Map to coding agents (Claude Code, Codex) and disconnect it again.

Everything written is either a whole file we own (the skill, the runtime under ~/.pmap) or a clearly
marked block / entry inside the user's config, so `disconnect` can remove exactly what `connect` added.
Existing config files are backed up next to themselves (`*.bak-pmap`) before the first change.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

SKILL_NAME = "project-map"
HOOK_MARKER = "hook session-start"
MD_START, MD_END = "<!-- pmap:start -->", "<!-- pmap:end -->"
TOML_START, TOML_END = "# >>> pmap (managed by `pmap connect`; remove with `pmap disconnect`)", "# <<< pmap"


def home() -> Path:
    return Path(os.environ.get("PMAP_USER_HOME") or Path.home())


def pmap_home() -> Path:
    return Path(os.environ.get("PMAP_HOME") or home() / ".pmap")


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or home() / ".claude")


def gemini_dir() -> Path:
    return home() / ".gemini"


def codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or home() / ".codex")


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def _backup(path: Path) -> None:
    backup = path.with_name(path.name + ".bak-pmap")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)


# ------------------------------------------------------------------ runtime


# Launchers run inside the user's projects (hook, MCP server, CLI). `python -I -c` keeps the current
# folder and PYTHON* variables off sys.path, so a project's own `pmap` or `json.py` is never imported.
BOOTSTRAP = "import sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pmap';from pmap.cli import main;sys.exit(main())"


def launcher_path(root: Optional[Path] = None) -> Path:
    root = root or pmap_home()
    return root / "bin" / ("pmap.cmd" if os.name == "nt" else "pmap")


def venv_python(root: Optional[Path] = None) -> Path:
    """The Python of ~/.pmap/venv, where `pmap langs install` puts the tree-sitter grammars."""
    venv = (root or pmap_home()) / "venv"
    return venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"


def install_runtime(root: Optional[Path] = None) -> Path:
    """Copy this pmap package to ~/.pmap/lib and write launchers to ~/.pmap/bin; returns the launcher.

    The launchers use ~/.pmap/venv when it exists (it carries the grammars for languages besides Python)."""
    root = root or pmap_home()
    lib, target = root / "lib", root / "lib" / "pmap"
    source = _package_dir()
    if source != target.resolve():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "._*", "*.pyc"))
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python = venv_python(root) if venv_python(root).exists() else Path(sys.executable)
    sh = bin_dir / "pmap"
    sh.write_text(
        "#!/bin/sh\n# Method Project Map launcher (installed by `pmap connect`)\n"
        f'exec "{python}" -I -c "{BOOTSTRAP}" "{lib}" "$@"\n',
        encoding="utf-8",
    )
    sh.chmod(0o755)
    (bin_dir / "pmap.cmd").write_bytes(  # bytes: write_text would turn \r\n into \r\r\n on Windows
        f'@echo off\r\nrem Method Project Map launcher (installed by `pmap connect`)\r\n"{python}" -I -c "{BOOTSTRAP}" "{lib}" %*\r\n'
        .encode("utf-8"))
    return launcher_path(root)


def install_languages(root: Optional[Path] = None, log=print) -> List[str]:
    """Create ~/.pmap/venv with tree-sitter and the grammars, then point the launchers at it."""
    from pmap.langs import PIP_PACKAGES

    root = root or pmap_home()
    python = venv_python(root)
    report = []
    if not python.exists():
        log(f"Creating {root / 'venv'} ...")
        made = subprocess.run([sys.executable, "-m", "venv", str(root / "venv")], capture_output=True, text=True)
        if made.returncode != 0:
            raise RuntimeError("could not create a virtual environment (on Debian/Ubuntu: apt install python3-venv): "
                               + (made.stderr or made.stdout).strip()[-400:])
        report.append(f"Environment: {root / 'venv'}")
    log("Installing tree-sitter grammars (TypeScript/JavaScript, Go, Java, Kotlin, C#, Swift, Dart, Rust, C/C++) ...")
    pip = subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--upgrade",
                          *PIP_PACKAGES], capture_output=True, text=True)
    if pip.returncode != 0:
        raise RuntimeError("pip could not install the grammars: " + (pip.stderr or pip.stdout).strip()[-400:])
    report.append("Grammars: " + ", ".join(PIP_PACKAGES))
    report.append(f"Runtime: {install_runtime(root)} (now runs on {python})")
    return report


def _quote(path: Path) -> str:
    return f'"{path}"'


def skill_text(launcher: Path) -> str:
    text = (_package_dir() / "agent" / "SKILL.md").read_text(encoding="utf-8")
    return text.replace("{{PMAP}}", str(launcher))


def _write_skill(skills_root: Path, launcher: Path) -> Path:
    target = skills_root / SKILL_NAME / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(skill_text(launcher), encoding="utf-8")
    return target


# ------------------------------------------------------------------ claude code


def _claude_cli() -> Optional[str]:
    found = shutil.which("claude")
    if found:
        return found
    pattern = str(home() / "Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude")
    candidates = sorted(glob.glob(pattern))
    return candidates[-1] if candidates else None


def _load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def _strip_pmap_hooks(settings: Dict) -> bool:
    """Remove our SessionStart entries; returns True if something was removed."""
    hooks = settings.get("hooks") or {}
    entries = hooks.get("SessionStart") or []
    kept = [e for e in entries if not any(HOOK_MARKER in str(h.get("command", "")) and "pmap" in str(h.get("command", ""))
                                          for h in e.get("hooks", []))]
    if len(kept) == len(entries):
        return False
    if kept:
        hooks["SessionStart"] = kept
    else:
        hooks.pop("SessionStart", None)
    if not hooks:
        settings.pop("hooks", None)
    return True


def connect_claude(launcher: Path, register_mcp: bool = True) -> List[str]:
    report = [f"Claude Code: skill -> {_write_skill(claude_dir() / 'skills', launcher)}"]
    settings_path = claude_dir() / "settings.json"
    settings = _load_json(settings_path)
    _backup(settings_path)
    _strip_pmap_hooks(settings)
    settings.setdefault("hooks", {}).setdefault("SessionStart", []).append({
        "matcher": "startup|resume|clear|compact",
        "hooks": [{"type": "command", "command": f"{_quote(launcher)} {HOOK_MARKER}", "timeout": 30}],
    })
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report.append(f"Claude Code: session-start overview hook -> {settings_path}")
    rules = claude_dir() / "CLAUDE.md"
    _backup(rules)
    rules.write_text(_replace_block(rules.read_text(encoding="utf-8") if rules.exists() else "", MD_START, MD_END,
                                    _rule_text(launcher)), encoding="utf-8")
    report.append(f"Claude Code: standing rule -> {rules}")
    if register_mcp:
        cli = _claude_cli()
        if cli is None:
            report.append("Claude Code: MCP not registered (no `claude` CLI found); the skill uses the command line instead")
        else:
            subprocess.run([cli, "mcp", "remove", "--scope", "user", "pmap"], capture_output=True, text=True)
            done = subprocess.run([cli, "mcp", "add", "--scope", "user", "pmap", "--", str(launcher), "mcp"],
                                  capture_output=True, text=True)
            report.append("Claude Code: MCP server `pmap` registered for all projects" if done.returncode == 0
                          else f"Claude Code: MCP registration failed ({(done.stderr or done.stdout).strip()[:200]})")
    return report


def disconnect_claude() -> List[str]:
    report = []
    skill = claude_dir() / "skills" / SKILL_NAME
    if skill.exists():
        shutil.rmtree(skill)
        report.append(f"Claude Code: removed skill {skill}")
    settings_path = claude_dir() / "settings.json"
    settings = _load_json(settings_path)
    if _strip_pmap_hooks(settings):
        _backup(settings_path)
        settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report.append(f"Claude Code: removed session-start hook from {settings_path}")
    rules = claude_dir() / "CLAUDE.md"
    if rules.exists() and MD_START in rules.read_text(encoding="utf-8"):
        rules.write_text(_replace_block(rules.read_text(encoding="utf-8"), MD_START, MD_END, None), encoding="utf-8")
        report.append(f"Claude Code: removed standing rule from {rules}")
    cli = _claude_cli()
    if cli is not None:
        done = subprocess.run([cli, "mcp", "remove", "--scope", "user", "pmap"], capture_output=True, text=True)
        if done.returncode == 0:
            report.append("Claude Code: MCP server `pmap` removed")
    return report or ["Claude Code: nothing to remove"]


def claude_status() -> Dict:
    settings = _load_json(claude_dir() / "settings.json")
    hook = any(HOOK_MARKER in str(h.get("command", "")) for e in (settings.get("hooks") or {}).get("SessionStart", [])
               for h in e.get("hooks", []))
    skill = (claude_dir() / "skills" / SKILL_NAME / "SKILL.md").exists()
    return {"connected": skill and hook, "skill": skill, "hook": hook}


# ------------------------------------------------------------------ codex


def _replace_block(text: str, start: str, end: str, block: Optional[str]) -> str:
    if start in text and end in text:
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        text = before.rstrip("\n") + ("\n" if before.strip() else "") + after.lstrip("\n")
    if block:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + f"{start}\n{block.rstrip()}\n{end}\n"
    return text


def _rule_text(launcher: Path) -> str:
    return (f"## Method Project Map\nIn code repositories, orient with Method Project Map before reading files: use the overview "
            f"injected at session start or run `{launcher} context . --max-chars 8000`, then `{launcher} find <name>`, "
            f"`{launcher} show <node-id|path>` and `{launcher} source <node-id>` (or the pmap_* MCP tools) to locate and "
            "read only the code you need. Trust `resolved` links, verify `inferred` ones; details in the project-map skill.")


def connect_codex(launcher: Path) -> List[str]:
    report = [f"Codex: skill -> {_write_skill(codex_dir() / 'skills', launcher)}"]
    config = codex_dir() / "config.toml"
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    unmanaged = text.replace(text.split(TOML_START, 1)[1].split(TOML_END, 1)[0], "") if TOML_START in text else text
    if "[mcp_servers.pmap]" in unmanaged:
        report.append("Codex: config.toml already has [mcp_servers.pmap]; left unchanged")
    else:
        _backup(config)
        launcher_toml = str(launcher).replace("\\", "\\\\")
        block = f'[mcp_servers.pmap]\ncommand = "{launcher_toml}"\nargs = ["mcp"]'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(_replace_block(text, TOML_START, TOML_END, block), encoding="utf-8")
        report.append(f"Codex: MCP server `pmap` -> {config}")
    agents = codex_dir() / "AGENTS.md"
    rule = _rule_text(launcher)
    _backup(agents)
    agents.write_text(_replace_block(agents.read_text(encoding="utf-8") if agents.exists() else "", MD_START, MD_END, rule),
                      encoding="utf-8")
    report.append(f"Codex: start-of-task rule -> {agents}")
    return report


def disconnect_codex() -> List[str]:
    report = []
    skill = codex_dir() / "skills" / SKILL_NAME
    if skill.exists():
        shutil.rmtree(skill)
        report.append(f"Codex: removed skill {skill}")
    for path, start, end in ((codex_dir() / "config.toml", TOML_START, TOML_END),
                             (codex_dir() / "AGENTS.md", MD_START, MD_END)):
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if start in text:
                path.write_text(_replace_block(text, start, end, None), encoding="utf-8")
                report.append(f"Codex: removed pmap block from {path}")
    return report or ["Codex: nothing to remove"]


def codex_status() -> Dict:
    config = codex_dir() / "config.toml"
    mcp = config.exists() and TOML_START in config.read_text(encoding="utf-8")
    skill = (codex_dir() / "skills" / SKILL_NAME / "SKILL.md").exists()
    return {"connected": skill and mcp, "skill": skill, "mcp": mcp}


# ------------------------------------------------------------------ Gemini CLI


def _is_ours(entry) -> bool:
    return isinstance(entry, dict) and entry.get("args") == ["mcp"] and str(entry.get("command", "")).replace(
        "\\", "/").endswith((".pmap/bin/pmap", ".pmap/bin/pmap.cmd"))


def connect_gemini(launcher: Path) -> List[str]:
    """Gemini CLI: the `pmap` MCP server in ~/.gemini/settings.json and the rule in ~/.gemini/GEMINI.md."""
    report = []
    settings = gemini_dir() / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() and settings.read_text().strip() else {}
    servers = data.setdefault("mcpServers", {})
    if "pmap" in servers and not _is_ours(servers["pmap"]):
        report.append("Gemini CLI: settings.json already has an mcpServers.pmap of its own; left unchanged")
    else:
        _backup(settings)
        servers["pmap"] = {"command": str(launcher), "args": ["mcp"], "timeout": 120000}
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report.append(f"Gemini CLI: MCP server `pmap` -> {settings}")
    rules = gemini_dir() / "GEMINI.md"
    _backup(rules)
    rules.write_text(_replace_block(rules.read_text(encoding="utf-8") if rules.exists() else "", MD_START, MD_END,
                                    _rule_text(launcher)), encoding="utf-8")
    report.append(f"Gemini CLI: rule for every session -> {rules}")
    return report


def disconnect_gemini() -> List[str]:
    report = []
    settings = gemini_dir() / "settings.json"
    if settings.exists():
        data = json.loads(settings.read_text(encoding="utf-8") or "{}")
        servers = data.get("mcpServers", {})
        if _is_ours(servers.get("pmap")):
            del servers["pmap"]
            if not servers:
                data.pop("mcpServers", None)
            settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            report.append(f"Gemini CLI: removed MCP server `pmap` from {settings}")
    rules = gemini_dir() / "GEMINI.md"
    if rules.exists() and MD_START in rules.read_text(encoding="utf-8"):
        rest = _replace_block(rules.read_text(encoding="utf-8"), MD_START, MD_END, None)
        if rest.strip():
            rules.write_text(rest, encoding="utf-8")
        else:
            rules.unlink()  # we created it
        report.append(f"Gemini CLI: removed the pmap rule from {rules}")
    return report or ["Gemini CLI: nothing to remove"]


def gemini_status() -> Dict:
    settings = gemini_dir() / "settings.json"
    try:
        mcp = _is_ours(json.loads(settings.read_text(encoding="utf-8")).get("mcpServers", {}).get("pmap"))
    except (OSError, ValueError):
        mcp = False
    rules = gemini_dir() / "GEMINI.md"
    rule = rules.exists() and MD_START in rules.read_text(encoding="utf-8")
    return {"connected": mcp and rule, "mcp": mcp, "rule": rule}


# ------------------------------------------------------------------ entry points


def connect(claude: bool = True, codex: bool = False, gemini: bool = False) -> List[str]:
    launcher = install_runtime()
    report = [f"Runtime: {launcher}"]
    if claude:
        report += connect_claude(launcher)
    if codex:
        report += connect_codex(launcher)
    if gemini:
        report += connect_gemini(launcher)
    if claude:
        report.append("New Claude Code sessions in code projects now start with a project map overview.")
    return report


def disconnect(claude: bool = True, codex: bool = True, gemini: bool = True) -> List[str]:
    report: List[str] = []
    if claude:
        report += disconnect_claude()
    if codex:
        report += disconnect_codex()
    if gemini:
        report += disconnect_gemini()
    return report


def status() -> Dict:
    return {"runtime": str(launcher_path()) if launcher_path().exists() else None,
            "claude": claude_status(), "codex": codex_status(), "gemini": gemini_status()}
