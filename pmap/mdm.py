"""A design map from an mDm scheme (`PROJECT.mdm`): functions and objects become subsystems.

mDm (Method Diagram, the design tool of the Method Network) writes a project as `PROJECT.mdm`: numbered
functions with the objects they take (`из:`) and give (`в:`). A coding agent records where each function
or object is implemented with a `код:` line. That is enough to check the code against the scheme:

- a function or object with `код:` is a subsystem (`f-2-1`, `o-zayavka`), its paths are the `код:` globs;
  objects with the same `код:` («Заявка», «Проверенная заявка») are one data type and share a subsystem;
- a function may depend on the objects it takes or gives (required: an unused one is an absence);
- functions wired by an object (one gives what the other takes), a function and its subfunctions, and
  two objects handled by one function may depend on each other in either direction (optional);
- anything else is a divergence: the code wires together what the scheme keeps apart.

Only what the parser needs is read here; the full syntax lives with mDm (`references/project-mdm.md`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pmap import PMF_VERSION
from pmap.models import AllowedDependencySpec, DesignMap, SubsystemSpec

SECTIONS = (
    ("functions", re.compile(r"^(функции|схема|функциональная схема)$")),
    ("objects", re.compile(r"^(объекты|сущности|данные)$")),
)
FUNCTION_LINE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")
PROP_LINE = re.compile(r"^([^:]{1,40}):\s*(.*)$")
INPUT_KEYS = {"из", "вход", "входы"}
OUTPUT_KEYS = {"в", "выход", "выходы"}
CODE_KEYS = {"код", "code"}

TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
     "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))


@dataclass
class MdmFunction:
    number: str
    title: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    code: list[str] = field(default_factory=list)

    @property
    def parent(self) -> Optional[str]:
        return self.number.rsplit(".", 1)[0] if "." in self.number else None


@dataclass
class MdmObject:
    title: str
    code: list[str] = field(default_factory=list)


@dataclass
class MdmScheme:
    title: str = ""
    essence: str = ""
    functions: list[MdmFunction] = field(default_factory=list)
    objects: list[MdmObject] = field(default_factory=list)


def split_list(text: str) -> list[str]:
    """«А, «Б, В», Г» → ['А', 'Б, В', 'Г']: commas inside «» or "" do not split."""
    out: list[str] = []
    cur = ""
    quote: Optional[str] = None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur += ch
        elif ch == "«":
            quote = "»"
        elif ch == '"':
            quote = '"'
        elif ch in ",;":
            if cur.strip():
                out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _split_props(text: str) -> tuple[str, dict[str, str]]:
    """«Название · кто: Х · код: app/x.py» → ('Название', {'кто': 'Х', 'код': 'app/x.py'})."""
    head, *rest = text.split(" · ")
    props: dict[str, str] = {}
    for part in rest:
        m = PROP_LINE.match(part.strip())
        if m:
            props[m.group(1).strip().lower()] = m.group(2).strip()
    return head.strip(), props


def _code_of(props: dict[str, str]) -> list[str]:
    return [item for key in CODE_KEYS if props.get(key) for item in split_list(props[key])]


def parse_mdm(text: str) -> MdmScheme:
    """Read the parts of `PROJECT.mdm` a design map needs: title, functions, objects and `код:`."""
    scheme = MdmScheme()
    section: Optional[str] = None
    current: Optional[MdmFunction] = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        h2 = re.match(r"^##\s+(.+)$", line)
        if h2:
            name = h2.group(1).strip().lower()
            section = next((key for key, rx in SECTIONS if rx.match(name)), "other")
            current = None
            continue
        if section is None:
            if line.startswith("# ") and not scheme.title:
                scheme.title = line[2:].strip()
            elif line.startswith(">"):
                scheme.essence = " ".join(filter(None, [scheme.essence, line[1:].strip()]))
            continue
        if section == "functions":
            fn = FUNCTION_LINE.match(line)
            if fn:
                head, props = _split_props(fn.group(2))
                current = MdmFunction(number=fn.group(1), title=head, code=_code_of(props))
                scheme.functions.append(current)
                continue
            prop = PROP_LINE.match(line)
            if prop and current is not None:
                key, value = prop.group(1).strip().lower(), prop.group(2).strip()
                if key in INPUT_KEYS:
                    current.inputs.extend(split_list(value))
                elif key in OUTPUT_KEYS:
                    current.outputs.extend(split_list(value))
                elif key in CODE_KEYS:
                    current.code.extend(split_list(value))
        elif section == "objects":
            head, props = _split_props(line)
            title = head.split(":", 1)[0].strip()
            if title:
                scheme.objects.append(MdmObject(title=title, code=_code_of(props)))
    return scheme


def _key(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().strip("«»\"")).lower()


def slug(title: str) -> str:
    """Object title → a subsystem id part: «Расписание мастера» → 'raspisanie-mastera'."""
    text = "".join(TRANSLIT.get(ch, ch) for ch in title.lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")


SYMBOL_ID = re.compile(r"^(?:sym|mod):([^:]+)(?::.*)?$")
SOURCE_FILE = re.compile(r"\.(py|pyi|ts|tsx|js|jsx|mjs|cjs|go|java|kt|kts|cs|swift|dart|rs|c|h|cc|cpp|cxx|hpp|hh|m|mm)$")


def code_paths(items: list[str], repo_root: Optional[Path] = None) -> tuple[list[str], list[str]]:
    """`код:` items → glob patterns. A folder means everything in it; `file.py:Name` and node ids keep the
    file. Returns (patterns, items that are not paths)."""
    paths: list[str] = []
    bad: list[str] = []
    for raw in items:
        item = raw.strip().strip("`").strip()
        sym = SYMBOL_ID.match(item)
        if sym:
            item = sym.group(1)
            if "/" not in item and not SOURCE_FILE.search(item):
                # A Python id names a module, not a file: `sym:pkg.mod:Name` → pkg/mod.py (or the package)
                module = item.replace(".", "/")
                is_pkg = repo_root is not None and (repo_root / module / "__init__.py").is_file()
                item = f"{module}/__init__.py" if is_pkg else f"{module}.py"
        item = re.sub(r"^(.+\.\w{1,5}):[\w.]+$", r"\1", item)  # app/x.py:Class.method → app/x.py
        item = re.sub(r"^\./", "", item).replace("\\", "/")
        if not item or item.startswith("/") or ".." in item.split("/") or " " in item:
            bad.append(raw)
            continue
        is_dir = item.endswith("/") or (repo_root is not None and (repo_root / item).is_dir())
        item = item.rstrip("/")
        paths.append(f"{item}/**" if is_dir and not item.endswith("**") else item)
    return paths, bad


def design_from_mdm(text: str, repo_root: Optional[Path] = None, source: str = "PROJECT.mdm") -> DesignMap:
    """A DesignMap from mDm text: subsystems for whatever has `код:`, dependencies along the scheme's wiring."""
    scheme = parse_mdm(text)
    notes: list[str] = []
    subsystems: list[SubsystemSpec] = []
    fn_sub: dict[str, str] = {}
    obj_sub: dict[str, str] = {}

    for fn in scheme.functions:
        paths, bad = code_paths(fn.code, repo_root)
        if bad:
            notes.append(f"{source}: function {fn.number}: `код:` items that are not repository paths: {', '.join(bad)}")
        if not paths:
            continue
        sid = "f-" + fn.number.replace(".", "-")
        fn_sub[fn.number] = sid
        subsystems.append(SubsystemSpec(id=sid, name=f"{fn.number} {fn.title}", paths=paths,
                                        description=f"mDm function {fn.number}"))

    titles = {_key(o.title): o.title for o in scheme.objects}
    for fn in scheme.functions:
        for t in fn.inputs + fn.outputs:
            titles.setdefault(_key(t), t)
    used_ids: set[str] = set()
    # Objects with the same `код:` are one data type in the code: a scheme turns «Заявка» into «Проверенная
    # заявка», the code keeps one class. They share a subsystem, so a function may use it if it takes or gives
    # any of them.
    by_paths: dict[frozenset[str], SubsystemSpec] = {}
    for obj in scheme.objects:
        paths, bad = code_paths(obj.code, repo_root)
        if bad:
            notes.append(f"{source}: object «{obj.title}»: `код:` items that are not repository paths: {', '.join(bad)}")
        if not paths or _key(obj.title) in obj_sub:
            continue
        same = by_paths.get(frozenset(paths))
        if same is not None:
            obj_sub[_key(obj.title)] = same.id
            same.name = f"{same.name}, {obj.title}"
            continue
        base = "o-" + (slug(obj.title) or str(len(obj_sub) + 1))
        sid, n = base, 2
        while sid in used_ids:
            sid, n = f"{base}-{n}", n + 1
        used_ids.add(sid)
        obj_sub[_key(obj.title)] = sid
        sub = SubsystemSpec(id=sid, name=obj.title, paths=paths, description="mDm object")
        by_paths[frozenset(paths)] = sub
        subsystems.append(sub)

    deps: dict[tuple[str, str], AllowedDependencySpec] = {}

    def allow(a: str, b: str, why: str, required: bool = False) -> None:
        if a == b:
            return
        old = deps.get((a, b))
        if old is None:
            deps[(a, b)] = AllowedDependencySpec(from_subsystem=a, to_subsystem=b, description=why, required=required)
        elif required and not old.required:
            old.required, old.description = True, why

    by_number = {fn.number: fn for fn in scheme.functions}
    for fn in scheme.functions:
        touched = [_key(t) for t in fn.inputs + fn.outputs]
        if fn.number in fn_sub:
            for k in touched:
                if k in obj_sub:
                    verb = "takes" if k in {_key(t) for t in fn.inputs} else "gives"
                    allow(fn_sub[fn.number], obj_sub[k], f"{fn.number} {verb} «{titles.get(k, k)}»", required=True)
            parent = fn.parent
            while parent and parent not in fn_sub:
                parent = by_number[parent].parent if parent in by_number else None
            if parent:
                allow(fn_sub[fn.number], fn_sub[parent], f"{fn.number} is part of {parent}")
                allow(fn_sub[parent], fn_sub[fn.number], f"{fn.number} is part of {parent}")
        handled = [k for k in dict.fromkeys(touched) if k in obj_sub]
        for a in handled:
            for b in handled:
                allow(obj_sub[a], obj_sub[b], f"both handled by {fn.number}")
    for giver in scheme.functions:
        for taker in scheme.functions:
            if giver is taker or giver.number not in fn_sub or taker.number not in fn_sub:
                continue
            shared = {_key(t) for t in giver.outputs} & {_key(t) for t in taker.inputs}
            if shared:
                what = ", ".join(f"«{titles.get(k, k)}»" for k in sorted(shared))
                why = f"{giver.number} gives {what} to {taker.number}"
                allow(fn_sub[taker.number], fn_sub[giver.number], why)
                allow(fn_sub[giver.number], fn_sub[taker.number], why)

    unlinked = [fn.number for fn in scheme.functions if fn.number not in fn_sub]
    if not scheme.functions:
        notes.append(f"{source}: no functions in the scheme (section `## Функции`)")
    elif not fn_sub:
        notes.append(f"{source}: no function is linked to code yet (add `код: <paths>` under a function); "
                     "nothing to check")
    elif unlinked:
        notes.append(f"{source}: {len(unlinked)} function(s) without `код:` are not checked: "
                     + ", ".join(unlinked[:12]) + (" ..." if len(unlinked) > 12 else ""))

    return DesignMap(
        pmf_version=PMF_VERSION,
        project_name=scheme.title or "PROJECT.mdm",
        project_description=scheme.essence,
        subsystems=subsystems,
        allowed_dependencies=list(deps.values()),
        source="mdm",
        notes=notes,
    )


def load_mdm_design(path: Path) -> DesignMap:
    path = Path(path)
    repo_root = path.resolve().parent
    return design_from_mdm(path.read_text(encoding="utf-8"), repo_root=repo_root, source=path.name)
