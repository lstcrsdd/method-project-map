"""An mDm scheme (PROJECT.mdm) as the design map: functions and objects with `код:` are subsystems."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from pmap.loader import find_design
from pmap.mdm import code_paths, design_from_mdm, parse_mdm, slug
from pmap.mermaid import format_agent_context
from pmap.models import ActualMap, DesignMap
from pmap.reflexion import check_reflexion, format_reflexion_text
from pmap.scanner import ProjectScanner
from pmap.yaml_loader import validate_pmf_document

ROOT_DIR = Path(__file__).resolve().parent.parent
BOOKING = ROOT_DIR / "tests" / "fixtures" / "mdm_booking"


def pairs(results) -> set[tuple[str, str]]:
    return {(r.from_subsystem, r.to_subsystem) for r in results}


class TestMdmScheme(unittest.TestCase):
    def test_parse_reads_functions_objects_and_code(self) -> None:
        scheme = parse_mdm((BOOKING / "PROJECT.mdm").read_text(encoding="utf-8"))
        self.assertEqual(scheme.title, "Онлайн-запись в салон")
        self.assertEqual([f.number for f in scheme.functions], ["1", "2", "2.1", "3"])
        two = scheme.functions[1]
        self.assertEqual(two.inputs, ["Заявка", "Расписание мастера"])
        self.assertEqual(two.outputs, ["Бронь"])
        self.assertEqual(two.code, ["booking/slots/"])
        self.assertEqual(scheme.functions[2].parent, "2")
        self.assertEqual([(o.title, o.code) for o in scheme.objects], [
            ("Заявка", ["booking/models/request.py"]),
            ("Бронь", ["`booking/models/booking.py`"]),
            ("Расписание мастера", []),
        ])

    def test_quoted_names_and_inline_code(self) -> None:
        scheme = parse_mdm("## Функции\n1 Выставить счёт · код: billing/\n  из: «Счёт, акт», Заказ\n")
        self.assertEqual(scheme.functions[0].inputs, ["Счёт, акт", "Заказ"])
        self.assertEqual(scheme.functions[0].code, ["billing/"])

    def test_code_items_become_globs(self) -> None:
        paths, bad = code_paths(["app/x.py:Service.run", "./app/api/", "app/**/*.ts", "sym:src/a.ts:A",
                                 "sym:pkg.mod:Name", "/etc/passwd", "../up.py", "`lib/b.go`"])
        self.assertEqual(paths, ["app/x.py", "app/api/**", "app/**/*.ts", "src/a.ts", "pkg/mod.py", "lib/b.go"])
        self.assertEqual(bad, ["/etc/passwd", "../up.py"])
        self.assertEqual(code_paths(["booking", "mod:booking.models", "sym:booking.intake:accept"], BOOKING)[0],
                         ["booking/**", "booking/models/__init__.py", "booking/intake.py"])

    def test_slug(self) -> None:
        self.assertEqual(slug("Расписание мастера"), "raspisanie-mastera")
        self.assertEqual(slug("Бронь"), "bron")
        self.assertEqual(slug("Order #2"), "order-2")

    def test_design_follows_the_wiring(self) -> None:
        design = find_design(BOOKING)
        self.assertIsInstance(design, DesignMap)
        assert design is not None
        self.assertEqual(design.source, "mdm")
        self.assertEqual(validate_pmf_document(design.to_dict()), [])
        self.assertEqual([s.id for s in design.subsystems], ["f-1", "f-2", "f-2-1", "o-zayavka", "o-bron"])
        allowed = {(d.from_subsystem, d.to_subsystem): d.required for d in design.allowed_dependencies}
        # A function may use the objects it takes and gives — and an unused one is reported.
        self.assertTrue(allowed[("f-1", "o-zayavka")])
        self.assertTrue(allowed[("f-2", "o-bron")])
        # Functions wired by «Заявка», and a function with its subfunction: either way, never an absence.
        self.assertFalse(allowed[("f-1", "f-2")])
        self.assertFalse(allowed[("f-2", "f-1")])
        self.assertFalse(allowed[("f-2-1", "f-2")])
        # Objects handled by one function may refer to each other.
        self.assertFalse(allowed[("o-bron", "o-zayavka")])
        # An object never depends on a function.
        self.assertNotIn(("o-bron", "f-1"), allowed)
        self.assertIn("without `код:` are not checked: 3", design.notes[0])

    def test_check_reports_code_that_skips_the_scheme(self) -> None:
        actual = ProjectScanner().scan_repository(BOOKING)
        design = find_design(BOOKING)
        assert design is not None
        report = check_reflexion(design, actual)
        self.assertEqual(pairs(report.divergences), {("o-bron", "f-1")})
        self.assertEqual(pairs(report.absences), {("f-2", "o-zayavka")})
        self.assertEqual(pairs(report.convergences),
                         {("f-1", "o-zayavka"), ("f-2", "o-bron"), ("f-2-1", "o-zayavka"), ("f-2", "f-2-1")})
        self.assertEqual(report.node_to_subsystem["mod:booking.slots.windows"], "f-2-1")
        self.assertEqual(report.node_to_subsystem["mod:booking.slots.finder"], "f-2")
        text = format_reflexion_text(report)
        self.assertIn("o-bron -> f-1 [Бронь -> 1 Принять заявку]", text)
        self.assertIn("[warning] PROJECT.mdm: 1 function(s) without `код:`", text)
        self.assertEqual(report.to_dict()["labels"]["f-2-1"], "2.1 Проверить свободные окна")

    def test_overview_lists_the_scheme(self) -> None:
        actual = ProjectScanner().scan_repository(BOOKING)
        text = format_agent_context(actual, design=find_design(BOOKING), max_chars=3000)
        self.assertIn("Scheme (mDm, PROJECT.mdm)", text)
        self.assertIn("- 2.1 Проверить свободные окна (f-2-1): booking/slots/windows.py", text)
        self.assertIn("intake.py (f-1)", text)

    def test_scheme_without_code_checks_nothing(self) -> None:
        design = design_from_mdm("mdm 1\n# Пусто\n## Функции\n1 Принять заявку\n  в: Заявка\n")
        self.assertEqual(design.subsystems, [])
        self.assertIn("no function is linked to code yet", design.notes[0])
        actual = ProjectScanner().scan_repository(BOOKING)
        text = format_agent_context(actual, design=design, max_chars=2000)
        self.assertNotIn("Architecture status", text)
        self.assertIn("[mDm] PROJECT.mdm: no function is linked to code yet", text)

    def test_objects_with_the_same_code_are_one_type(self) -> None:
        design = design_from_mdm(
            "## Функции\n1 Принять заявку\n  в: Заявка\n  код: app/intake.py\n"
            "2 Проверить заявку\n  из: Заявка\n  в: Проверенная заявка\n  код: app/check.py\n"
            "3 Исполнить\n  из: Проверенная заявка\n  код: app/run.py\n"
            "## Объекты\nЗаявка · код: app/models.py\nПроверенная заявка · код: app/models.py\n")
        objects = [s for s in design.subsystems if s.id.startswith("o-")]
        self.assertEqual([(s.id, s.name) for s in objects], [("o-zayavka", "Заявка, Проверенная заявка")])
        allowed = {(d.from_subsystem, d.to_subsystem) for d in design.allowed_dependencies}
        self.assertTrue({("f-1", "o-zayavka"), ("f-2", "o-zayavka"), ("f-3", "o-zayavka")} <= allowed)
        self.assertNotIn(("o-zayavka", "o-zayavka"), allowed)

    def test_design_yaml_wins_over_the_scheme(self) -> None:
        self.assertEqual(find_design(ROOT_DIR / "examples").source, "yaml")  # type: ignore[union-attr]

    def test_cli_check_reads_project_mdm(self) -> None:
        proc = subprocess.run([sys.executable, "-m", "pmap", "check", str(BOOKING), "--json", "--strict"],
                              capture_output=True, text=True, cwd=ROOT_DIR)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["summary"]["divergences"], 1)


class TestHonestReport(unittest.TestCase):
    def test_check_names_languages_that_are_not_on_the_map(self) -> None:
        actual = ActualMap(pmf_version="0.2.0", generated_at="2026-10-20T00:00:00Z", repo_root=".",
                           languages={"python": {"files": 2, "mapped": True}, "javascript": {"files": 124, "mapped": False}})
        report = check_reflexion(find_design(BOOKING), actual)  # type: ignore[arg-type]
        self.assertTrue(report.is_clean)
        self.assertIn("124 JavaScript file(s) are not on the map and not checked", "\n".join(report.warnings))
        self.assertNotIn("Python file(s)", "\n".join(report.warnings))


class TestRequiredFlag(unittest.TestCase):
    def test_optional_dependency_is_never_an_absence(self) -> None:
        doc = {"pmf_version": "0.2.0", "kind": "design", "project": {"name": "x"},
               "subsystems": [{"id": "a", "name": "A", "paths": ["a/**"]}, {"id": "b", "name": "B", "paths": ["b/**"]}],
               "allowed_dependencies": [{"from": "a", "to": "b", "required": False}, {"from": "b", "to": "a"}]}
        self.assertEqual(validate_pmf_document(doc), [])
        design = DesignMap.from_dict(doc)
        self.assertEqual(design.to_dict()["allowed_dependencies"][0]["required"], False)
        self.assertNotIn("required", design.to_dict()["allowed_dependencies"][1])
        actual = ProjectScanner().scan_repository(BOOKING)
        self.assertEqual(pairs(check_reflexion(design, actual).absences), {("b", "a")})
        doc["allowed_dependencies"][0]["required"] = "no"
        self.assertTrue(any("required must be a boolean" in e for e in validate_pmf_document(doc)))


if __name__ == "__main__":
    unittest.main()
