"""Regression tests for the confidence rules: the scanner must never present a guess as a fact.

Each test pins the exact `confidence` of a specific edge. Numbers in test names refer to the
findings in docs/history/review-stage-1.md.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from pmap.models import ActualEdge, ActualMap, DesignMap
from pmap.reflexion import check_reflexion
from pmap.scanner import ProjectScanner
from pmap.stale import find_unscanned_files, is_map_fresh
from pmap.yaml_loader import validate_pmf_document

ROOT_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT_DIR / "spec" / "pmf-v0.schema.json"

BASE_PY = """
class Repo:
    def save(self):
        pass

    def run(self):
        self.save()


class SqlRepo(Repo):
    def save(self):
        pass


class Mongo:
    def save(self):
        pass


class Plain:
    def ping(self):
        pass

    def go(self):
        self.ping()


def helper():
    pass
"""

VIEWS_PY = """
from typing import TYPE_CHECKING
from app.core.base import Repo, SqlRepo, Mongo, Plain, helper
if TYPE_CHECKING:
    from app.core import base as typing_only


def shadow(helper):
    helper()


def poly(r: Repo):
    r.save()


def typed_plain(p: Plain):
    p.ping()


def reassign(flag):
    x = SqlRepo()
    if flag:
        x = Mongo()
    x.save()


def single():
    x = SqlRepo()
    x.save()


def outer():
    def inner():
        helper()
    inner()


class Svc:
    def __init__(self, cfg):
        self.repo = SqlRepo()

    def swap(self):
        self.repo = Mongo()

    def go(self):
        self.repo.save()
"""


def write_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return root


def calls_from(actual: ActualMap, from_id: str, expr: str | None = None) -> list[ActualEdge]:
    return [
        e for e in actual.edges
        if e.kind == "calls" and e.from_id == from_id and (expr is None or e.target_symbol == expr)
    ]


def only_call(test: unittest.TestCase, actual: ActualMap, from_id: str, expr: str) -> ActualEdge:
    edges = calls_from(actual, from_id, expr)
    test.assertEqual(len(edges), 1, f"expected one call edge {from_id} -> {expr}, got {edges}")
    return edges[0]


class ResolutionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def scan(self, files: dict[str, str], roots: list[str] | None = None) -> ActualMap:
        repo = write_repo(self.tmp / "repo", files)
        actual = ProjectScanner(import_roots=roots).scan_repository(repo)
        self.assertEqual(validate_pmf_document(actual.to_dict(), schema_path=SCHEMA_PATH), [])
        return actual

    def scan_probe(self, prefix: str = "") -> ActualMap:
        return self.scan({
            f"{prefix}app/__init__.py": "",
            f"{prefix}app/core/__init__.py": "",
            f"{prefix}app/core/base.py": BASE_PY,
            f"{prefix}app/web/__init__.py": "",
            f"{prefix}app/web/views.py": VIEWS_PY,
        })


class TestNoFalseResolved(ResolutionTestCase):
    """P0 findings 1-5: bindings that are not unique must not produce `resolved` edges."""

    def test_01_parameter_shadows_module_function(self) -> None:
        edge = only_call(self, self.scan_probe(), "sym:app.web.views:shadow", "helper")
        self.assertEqual(edge.confidence, "unresolved")
        self.assertEqual(edge.to_id, "unresolved:helper")

    def test_02_reassigned_local_is_ambiguous(self) -> None:
        edge = only_call(self, self.scan_probe(), "sym:app.web.views:reassign", "x.save")
        self.assertEqual(edge.confidence, "inferred")
        self.assertEqual(edge.to_id, "ambiguous:x.save")
        self.assertEqual(
            set(edge.candidates or []),
            {"sym:app.core.base:SqlRepo.save", "sym:app.core.base:Mongo.save"},
        )

    def test_02b_single_constructor_assignment_stays_resolved(self) -> None:
        edge = only_call(self, self.scan_probe(), "sym:app.web.views:single", "x.save")
        self.assertEqual(edge.confidence, "resolved")
        self.assertEqual(edge.to_id, "sym:app.core.base:SqlRepo.save")

    def test_03_self_attribute_assigned_in_two_methods_is_ambiguous(self) -> None:
        edge = only_call(self, self.scan_probe(), "sym:app.web.views:Svc.go", "self.repo.save")
        self.assertEqual(edge.confidence, "inferred")
        self.assertEqual(
            set(edge.candidates or []),
            {"sym:app.core.base:SqlRepo.save", "sym:app.core.base:Mongo.save"},
        )

    def test_04_annotation_is_never_proof(self) -> None:
        actual = self.scan_probe()
        poly = only_call(self, actual, "sym:app.web.views:poly", "r.save")
        self.assertEqual(poly.confidence, "inferred")
        self.assertEqual(
            set(poly.candidates or []),
            {"sym:app.core.base:Repo.save", "sym:app.core.base:SqlRepo.save"},
        )
        # Even without any overriding subclass an annotation is only a promise.
        plain = only_call(self, actual, "sym:app.web.views:typed_plain", "p.ping")
        self.assertEqual(plain.confidence, "inferred")
        self.assertEqual(plain.to_id, "sym:app.core.base:Plain.ping")
        self.assertEqual(plain.candidates, ["sym:app.core.base:Plain.ping"])

    def test_05_self_call_overridden_in_subclass_is_inferred(self) -> None:
        actual = self.scan_probe()
        overridden = only_call(self, actual, "sym:app.core.base:Repo.run", "self.save")
        self.assertEqual(overridden.confidence, "inferred")
        self.assertEqual(
            set(overridden.candidates or []),
            {"sym:app.core.base:Repo.save", "sym:app.core.base:SqlRepo.save"},
        )
        not_overridden = only_call(self, actual, "sym:app.core.base:Plain.go", "self.ping")
        self.assertEqual(not_overridden.confidence, "resolved")
        self.assertEqual(not_overridden.to_id, "sym:app.core.base:Plain.ping")


class TestImports(ResolutionTestCase):
    """P0 findings 6-7 and P1 findings 11-12."""

    def test_06_src_layout_matches_flat_layout(self) -> None:
        flat = self.scan_probe()
        shutil.rmtree(self.tmp / "repo")
        src = self.scan_probe(prefix="src/")
        self.assertEqual(src.import_roots, ["src", "."])
        self.assertNotIn("ext:app", {n.id for n in src.nodes})

        def deps(m: ActualMap) -> set[tuple[str, str, str, str]]:
            return {(e.kind, e.confidence, e.from_id, e.to_id) for e in m.edges}

        self.assertEqual(deps(flat), deps(src))

    def test_06b_explicit_import_root(self) -> None:
        actual = self.scan({
            "lib/pkg/__init__.py": "",
            "lib/pkg/a.py": "from pkg.b import f\n\ndef g():\n    f()\n",
            "lib/pkg/b.py": "def f():\n    pass\n",
        }, roots=["lib"])
        self.assertEqual(only_call(self, actual, "sym:pkg.a:g", "f").confidence, "resolved")

    def test_06c_unknown_import_is_unresolved_not_external(self) -> None:
        actual = self.scan({
            "m.py": "import json\nimport surely_not_installed_pkg_xyz\nfrom surely_not_installed_pkg_xyz import thing\n",
        })
        imports = {e.to_id: e for e in actual.edges if e.kind == "imports"}
        self.assertEqual(imports["ext:json"].confidence, "resolved")
        self.assertEqual(
            imports["unresolved:import:surely_not_installed_pkg_xyz"].confidence, "unresolved"
        )
        self.assertNotIn("ext:surely_not_installed_pkg_xyz", {n.id for n in actual.nodes})

    def test_06d_missing_project_module_is_unresolved(self) -> None:
        actual = self.scan({
            "app/__init__.py": "",
            "app/a.py": "from app.missing import x\nfrom . import nope\n\ndef go():\n    nope()\n",
        })
        unresolved = {e.to_id for e in actual.edges if e.kind == "imports" and e.confidence == "unresolved"}
        self.assertIn("unresolved:import:app.missing.x", unresolved)
        # The package itself is really loaded, but the name it should provide does not exist.
        self.assertEqual(only_call(self, actual, "sym:app.a:go", "nope").confidence, "unresolved")

    def test_07_type_checking_imports_are_type_only(self) -> None:
        actual = self.scan_probe()
        type_only = [e for e in actual.edges if e.kind == "imports" and e.type_only]
        self.assertEqual(len(type_only), 1)
        self.assertEqual(type_only[0].location.line if type_only[0].location else None, 4)

        design = DesignMap.from_dict({
            "pmf_version": "0.1.0",
            "kind": "design",
            "project": {"name": "probe"},
            "subsystems": [
                {"id": "core", "name": "Core", "paths": ["app/core/**"]},
                {"id": "web", "name": "Web", "paths": ["app/web/**"]},
            ],
            "allowed_dependencies": [{"from": "web", "to": "core"}],
        })
        default = check_reflexion(design, actual)
        with_types = check_reflexion(design, actual, include_type_only=True)
        default_count = sum(len(c.evidence) for c in default.convergences)
        typed_count = sum(len(c.evidence) for c in with_types.convergences)
        self.assertEqual(typed_count, default_count + 1)
        self.assertTrue(any("type-checking-only" in w for w in default.warnings))

    def test_11_multi_name_import_is_one_edge(self) -> None:
        actual = self.scan_probe()
        edges = [
            e for e in actual.edges
            if e.kind == "imports" and e.from_id == "mod:app.web.views" and e.to_id == "mod:app.core.base"
            and not e.type_only
        ]
        self.assertEqual(len(edges), 1)

    def test_12_reexport_resolves_to_defining_symbol(self) -> None:
        actual = self.scan({
            "pkg/__init__.py": "from .core import Thing, make\n",
            "pkg/core.py": "class Thing:\n    def run(self):\n        pass\n\ndef make():\n    pass\n",
            "user.py": "from pkg import Thing, make\n\ndef go():\n    make()\n    Thing().run()\n",
        })
        self.assertEqual(only_call(self, actual, "sym:user:go", "make").to_id, "sym:pkg.core:make")
        self.assertEqual(only_call(self, actual, "sym:user:go", "make").confidence, "resolved")
        self.assertEqual(only_call(self, actual, "sym:user:go", "Thing").to_id, "sym:pkg.core:Thing")
        call_targets = {e.to_id for e in calls_from(actual, "sym:user:go")}
        self.assertNotIn("mod:pkg", call_targets)

    def test_import_fallback_is_not_resolved(self) -> None:
        actual = self.scan({
            "fast.py": "def dumps():\n    pass\n",
            "slow.py": "def dumps():\n    pass\n",
            "m.py": "try:\n    from fast import dumps\nexcept ImportError:\n    from slow import dumps\n\n"
                    "def go():\n    dumps()\n",
        })
        edge = only_call(self, actual, "sym:m:go", "dumps")
        self.assertEqual(edge.confidence, "inferred")
        self.assertEqual(set(edge.candidates or []), {"sym:fast:dumps", "sym:slow:dumps"})

    def test_function_local_import_does_not_bind_module_name(self) -> None:
        actual = self.scan({
            "a.py": "def f():\n    pass\n",
            "b.py": "def f():\n    pass\n",
            "m.py": "from a import f\n\ndef lazy():\n    from b import f\n    f()\n\ndef eager():\n    f()\n",
        })
        self.assertEqual(only_call(self, actual, "sym:m:eager", "f").to_id, "sym:a:f")
        self.assertEqual(only_call(self, actual, "sym:m:lazy", "f").confidence, "unresolved")
        lazy_imports = [e for e in actual.edges if e.kind == "imports" and e.to_id == "mod:b"]
        self.assertEqual(len(lazy_imports), 1)


class TestRobustnessAndCompleteness(ResolutionTestCase):
    """P1 findings 8-10."""

    def test_08_broken_files_are_reported_not_fatal(self) -> None:
        repo = write_repo(self.tmp / "repo", {
            "good.py": "def ok():\n    pass\n",
            "broken.py": "def broken(:\n    pass\n",
        })
        (repo / "latin1.py").write_bytes("s = 'caf\xe9'\n".encode("latin-1"))
        actual = ProjectScanner().scan_repository(repo)
        self.assertIn("mod:good", {n.id for n in actual.nodes})
        errors = {e.path: e for e in actual.errors}
        self.assertEqual(errors["broken.py"].kind, "syntax_error")
        self.assertEqual(errors["broken.py"].line, 1)
        self.assertEqual(errors["latin1.py"].kind, "decode_error")
        self.assertEqual(validate_pmf_document(actual.to_dict(), schema_path=SCHEMA_PATH), [])
        # Unchanged broken files are not reported as unscanned; a fixed one is.
        self.assertEqual(find_unscanned_files(repo, actual), [])
        (repo / "broken.py").write_text("def fixed():\n    pass\n", encoding="utf-8")
        self.assertEqual(find_unscanned_files(repo, actual), ["broken.py"])

    def test_09_calls_in_nested_functions_are_kept(self) -> None:
        actual = self.scan_probe()
        edge = only_call(self, actual, "sym:app.web.views:outer", "helper")
        self.assertEqual(edge.confidence, "resolved")
        self.assertEqual(edge.to_id, "sym:app.core.base:helper")
        # The nested function itself is not a node, so calling it stays unresolved.
        self.assertEqual(only_call(self, actual, "sym:app.web.views:outer", "inner").confidence, "unresolved")

    def test_09b_self_inside_nested_function_is_not_trusted(self) -> None:
        actual = self.scan({
            "m.py": "class A:\n    def a(self):\n        pass\n\n    def b(self):\n"
                    "        def cb():\n            self.a()\n        return cb\n",
        })
        self.assertEqual(only_call(self, actual, "sym:m:A.b", "self.a").confidence, "unresolved")

    def test_10_conditional_definitions_become_nodes(self) -> None:
        actual = self.scan({
            "m.py": "import sys\n\nif sys.platform == 'win32':\n    def impl():\n        pass\nelse:\n"
                    "    def impl():\n        pass\n\ntry:\n    class Cfg:\n        pass\nexcept Exception:\n"
                    "    Cfg = None\n\ndef go():\n    impl()\n    Cfg()\n",
        })
        node_ids = {n.id for n in actual.nodes}
        self.assertIn("sym:m:impl", node_ids)
        self.assertIn("sym:m:impl#2", node_ids)
        self.assertIn("sym:m:Cfg", node_ids)
        impl = only_call(self, actual, "sym:m:go", "impl")
        self.assertEqual(impl.confidence, "inferred")
        self.assertEqual(set(impl.candidates or []), {"sym:m:impl", "sym:m:impl#2"})
        # `Cfg` may also be None.
        self.assertEqual(only_call(self, actual, "sym:m:go", "Cfg").confidence, "unresolved")

    def test_super_call_in_single_inheritance(self) -> None:
        actual = self.scan({
            "m.py": "class A:\n    def save(self):\n        pass\n\nclass B(A):\n    def save(self):\n"
                    "        super().save()\n",
        })
        edge = only_call(self, actual, "sym:m:B.save", "super.save")
        self.assertEqual(edge.confidence, "resolved")
        self.assertEqual(edge.to_id, "sym:m:A.save")

    def test_method_from_external_base_is_not_claimed(self) -> None:
        actual = self.scan({
            "m.py": "import unittest\n\nclass T(unittest.TestCase):\n    def helper(self):\n        pass\n\n"
                    "    def test_x(self):\n        self.helper()\n        self.assertEqual(1, 1)\n",
        })
        # A method an external base could override is inferred; an external method gets no edge.
        self.assertEqual(only_call(self, actual, "sym:m:T.test_x", "self.helper").confidence, "resolved")
        self.assertEqual(calls_from(actual, "sym:m:T.test_x", "self.assertEqual"), [])

    def test_method_shadowed_by_external_base_is_inferred(self) -> None:
        actual = self.scan({
            "m.py": "import json\n\nclass Base:\n    def encode(self, o):\n        pass\n\n"
                    "class Enc(json.JSONEncoder, Base):\n    def go(self):\n        self.encode(1)\n",
        })
        self.assertEqual(only_call(self, actual, "sym:m:Enc.go", "self.encode").confidence, "inferred")

    def test_any_annotation_stays_unresolved(self) -> None:
        actual = self.scan({
            "m.py": "from typing import Any, Optional\n\nclass A:\n    def run(self):\n        pass\n\n"
                    "def f(h: Any, o: Optional[A], s: str):\n    h.run()\n    o.run()\n    s.strip()\n",
        })
        self.assertEqual(only_call(self, actual, "sym:m:f", "h.run").confidence, "unresolved")
        self.assertEqual(only_call(self, actual, "sym:m:f", "o.run").confidence, "inferred")
        self.assertEqual(calls_from(actual, "sym:m:f", "s.strip"), [])


class TestStaleAndCli(ResolutionTestCase):
    def test_pmapignore_skips_vendored_code(self) -> None:
        repo = write_repo(self.tmp / "repo", {
            "app/core.py": "def f():\n    pass\n",
            "vendor/lib/thing.py": "def g():\n    pass\n",
            "app/generated_pb2.py": "X = 1\n",
            ".pmapignore": "# vendored and generated code\nvendor/\n*_pb2.py\n",
        })
        actual = ProjectScanner().scan_repository(repo)
        self.assertEqual({n.location.path for n in actual.nodes if n.kind == "module"}, {"app/core.py"})
        self.assertTrue(is_map_fresh(repo, actual))

    def test_new_file_makes_map_stale(self) -> None:
        repo = write_repo(self.tmp / "repo", {"a.py": "def f():\n    pass\n"})
        actual = ProjectScanner().scan_repository(repo)
        self.assertTrue(is_map_fresh(repo, actual))
        (repo / "b.py").write_text("def g():\n    pass\n", encoding="utf-8")
        self.assertEqual(find_unscanned_files(repo, actual), ["b.py"])
        self.assertFalse(is_map_fresh(repo, actual))

    def test_check_never_uses_stale_cache_silently(self) -> None:
        repo = write_repo(self.tmp / "repo", {
            "ui/view.py": "def show():\n    pass\n",
            "db/conn.py": "def query():\n    pass\n",
        })
        design = self.tmp / "design.yaml"
        design.write_text(
            'pmf_version: "0.1.0"\nkind: "design"\nproject:\n  name: "t"\nsubsystems:\n'
            '  - id: "ui"\n    name: "UI"\n    paths:\n      - "ui/**"\n'
            '  - id: "db"\n    name: "DB"\n    paths:\n      - "db/**"\n'
            "allowed_dependencies: []\n",
            encoding="utf-8",
        )
        run = lambda *args: subprocess.run(  # noqa: E731
            [sys.executable, "-m", "pmap", *args], cwd=ROOT_DIR, capture_output=True, text=True, check=True
        )
        run("scan", str(repo))
        (repo / "ui" / "view.py").write_text("from db.conn import query\n\ndef show():\n    query()\n", encoding="utf-8")
        res = run("check", str(repo), "--design", str(design), "--json")
        self.assertIn("out of date", res.stderr)
        self.assertEqual(json.loads(res.stdout)["summary"]["divergences"], 1)

    def test_validator_rejects_resolved_placeholder(self) -> None:
        doc = {
            "pmf_version": "0.1.0",
            "kind": "actual",
            "generated_at": "2026-10-19T00:00:00Z",
            "scanner": {"name": "x", "version": "1"},
            "nodes": [],
            "edges": [{"from": "a", "to": "unresolved:x", "kind": "calls", "confidence": "resolved", "source": "x"}],
        }
        self.assertTrue(any("placeholder" in e for e in validate_pmf_document(doc)))


if __name__ == "__main__":
    unittest.main()
