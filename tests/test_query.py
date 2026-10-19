"""Tests for selective map queries (`pmap find`, `pmap show`)."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from pmap.query import describe_node, find_nodes, resolve_ref
from pmap.scanner import ProjectScanner

ROOT_DIR = Path(__file__).resolve().parent.parent
TOY_REPO = ROOT_DIR / "examples" / "toy_project"


class TestQuery(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.actual = ProjectScanner().scan_repository(TOY_REPO)

    def test_find_prefers_exact_name(self) -> None:
        found = find_nodes(self.actual, "save_order")
        self.assertEqual(found[0].id, "sym:app.db.repository:OrderRepository.save_order")
        dotted = find_nodes(self.actual, "OrderService.create_order")
        self.assertEqual(dotted[0].id, "sym:app.services.order_service:OrderService.create_order")

    def test_suggestions_for_guessed_names(self) -> None:
        # Seen in the benchmark: an agent guessed `calculate_tax` for `TaxCalculator.compute`.
        from pmap.query import suggest_nodes

        suggested = [n.id for n in suggest_nodes(self.actual, "create_order_service")]
        self.assertEqual(suggested[0], "sym:app.services.order_service:OrderService.create_order")
        self.assertIn("sym:app.db.repository:OrderRepository.save_order", [n.id for n in suggest_nodes(self.actual, "saveOrder")])
        self.assertEqual(suggest_nodes(self.actual, "zzz"), [])

    def test_resolve_ref_accepts_id_path_and_qualified_name(self) -> None:
        self.assertEqual(resolve_ref(self.actual, "app/ui/cli.py").id, "mod:app.ui.cli")  # type: ignore[union-attr]
        self.assertEqual(resolve_ref(self.actual, "mod:app.ui.cli").id, "mod:app.ui.cli")  # type: ignore[union-attr]
        self.assertEqual(
            resolve_ref(self.actual, "app.db.connection.execute_raw_sql").id,  # type: ignore[union-attr]
            "sym:app.db.connection:execute_raw_sql",
        )
        self.assertIsNone(resolve_ref(self.actual, "nope"))

    def test_show_lists_callers_and_unresolved(self) -> None:
        raw_sql = resolve_ref(self.actual, "sym:app.db.connection:execute_raw_sql")
        assert raw_sql is not None
        text = describe_node(self.actual, raw_sql)
        self.assertIn("used by (1):", text)
        self.assertIn("sym:app.ui.cli:debug_dump_database  @ app/ui/cli.py:24", text)

        create = resolve_ref(self.actual, "sym:app.services.order_service:OrderService.create_order")
        assert create is not None
        text = describe_node(self.actual, create)
        self.assertIn("unresolved (1): hook.on_order_created", text)
        self.assertIn("resolved calls    sym:app.db.repository:OrderRepository.save_order", text)

    def test_cli_find_and_show(self) -> None:
        run = lambda *a: subprocess.run(  # noqa: E731
            [sys.executable, "-m", "pmap", *a, "--repo", str(TOY_REPO)],
            cwd=ROOT_DIR, capture_output=True, text=True,
        )
        self.assertIn("sym:app.models.order:Order.is_valid", run("find", "is_valid").stdout)
        self.assertIn("contains", run("show", "app/models/order.py").stdout)
        self.assertEqual(run("show", "missing").returncode, 2)


if __name__ == "__main__":
    unittest.main()
