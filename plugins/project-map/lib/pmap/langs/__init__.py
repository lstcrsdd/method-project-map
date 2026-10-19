"""Languages besides Python: TypeScript / JavaScript, Go, Java, Kotlin and C#, read with tree-sitter.

tree-sitter is optional: `pmap langs install` (or `pip install "method-project-map[langs]"`) adds it. Without it
these files are still counted in the map's `languages` section, marked as not mapped, and the rest of the map
(Python included) works as before.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pmap.models import ActualEdge, ActualNode, ScanError

LANGUAGES: Dict[str, dict] = {
    "typescript": {"name": "TypeScript", "exts": (".ts", ".tsx", ".mts", ".cts"), "grammars": ("typescript", "tsx")},
    "javascript": {"name": "JavaScript", "exts": (".js", ".jsx", ".mjs", ".cjs"), "grammars": ("javascript",)},
    "go": {"name": "Go", "exts": (".go",), "grammars": ("go",)},
    "java": {"name": "Java", "exts": (".java",), "grammars": ("java",)},
    "kotlin": {"name": "Kotlin", "exts": (".kt", ".kts"), "grammars": ("kotlin",)},
    "csharp": {"name": "C#", "exts": (".cs",), "grammars": ("csharp",)},
}
EXT_TO_LANGUAGE = {ext: lang for lang, spec in LANGUAGES.items() for ext in spec["exts"]}
PIP_PACKAGES = ["tree-sitter>=0.22", "tree-sitter-javascript", "tree-sitter-typescript", "tree-sitter-go",
                "tree-sitter-java", "tree-sitter-kotlin", "tree-sitter-c-sharp"]
MAX_FILE_BYTES = 1_000_000  # bigger files are bundles or generated code
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "out", ".next", ".nuxt", ".svelte-kit", "coverage", "vendor",
             "target", ".gradle", "bin", "obj", ".venv", "venv", "env", ".pmap", "__pycache__", ".idea", ".vscode",
             ".turbo", ".cache", "storybook-static", ".expo", "Pods", "DerivedData", ".pytest_cache", ".playwright-cli",
             ".aistudio"}


def available(language: str) -> bool:
    from pmap.langs.base import grammar_available

    return all(grammar_available(g) for g in LANGUAGES[language]["grammars"])


def _skip_file(name: str) -> bool:
    return (name.startswith("._") or name.endswith(".d.ts") or name.endswith(".min.js") or name.endswith(".bundle.js")
            or name.endswith(".generated.cs") or name.endswith(".g.cs") or name.endswith(".Designer.cs"))


def discover(repo_root: Path, ignore: Optional[List[str]] = None) -> List[Tuple[Path, str, str]]:
    """(absolute path, relative POSIX path, language) of every non-Python source file the map covers."""
    from pmap.scanner import is_ignored

    out: List[Tuple[Path, str, str]] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        rel_dir = Path(dirpath).relative_to(repo_root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("._")
                             and not d.startswith(".pmap")
                             and not (ignore and is_ignored(f"{rel_dir}/{d}/x" if rel_dir else f"{d}/x", ignore)))
        for name in sorted(filenames):
            language = EXT_TO_LANGUAGE.get(os.path.splitext(name)[1])
            if language is None or _skip_file(name):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if ignore and is_ignored(rel, ignore):
                continue
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append((path, rel, language))
    return out


@dataclass
class LanguageScan:
    nodes: List[ActualNode] = field(default_factory=list)
    edges: List[ActualEdge] = field(default_factory=list)
    errors: List[ScanError] = field(default_factory=list)
    languages: Dict[str, dict] = field(default_factory=dict)  # language -> {"files": n, "mapped": bool}


def scan(repo_root: Path, ignore: Optional[List[str]] = None) -> LanguageScan:
    from pmap.langs import csharp, golang, javascript, jvm
    from pmap.langs.resolve import Resolver

    extractors = {"typescript": javascript, "javascript": javascript, "go": golang, "java": jvm, "kotlin": jvm,
                  "csharp": csharp}
    result = LanguageScan()
    files = discover(repo_root, ignore)
    counts: Dict[str, int] = {}
    for _path, _rel, language in files:
        counts[language] = counts.get(language, 0) + 1
    ready = {lang: available(lang) for lang in counts}
    for lang, n in sorted(counts.items()):
        result.languages[lang] = {"files": n, "mapped": ready[lang]}
    facts_list = []
    hashes: Dict[str, str] = {}
    for path, rel, language in files:
        if not ready[language]:
            continue
        try:
            src = path.read_bytes()
        except OSError as exc:
            result.errors.append(ScanError(path=rel, kind="decode_error", message=str(exc)))
            continue
        hashes[rel] = "sha256:" + hashlib.sha256(src).hexdigest()  # as pmap.scanner.compute_file_hash
        try:
            facts = extractors[language].extract(src, rel, os.path.splitext(rel)[1])
        except Exception as exc:  # a grammar bug must not take the whole map down
            result.errors.append(ScanError(path=rel, kind="syntax_error", message=f"{type(exc).__name__}: {exc}",
                                           file_hash=hashes[rel]))
            continue
        if facts.syntax_errors:
            result.errors.append(ScanError(path=rel, kind="partial_parse", file_hash=hashes[rel],
                                           message="parsed with errors; some declarations or links may be missing"))
        facts_list.append(facts)
    if facts_list:
        result.nodes, result.edges = Resolver(repo_root, facts_list, hashes).build()
    return result
