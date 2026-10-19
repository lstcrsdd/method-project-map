"""Comprehensive test suite for Method Project Map (PMF v0) scanner, reflexion model, mermaid, and staleness detection."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pmap.mermaid import format_agent_context, generate_mermaid
from pmap.models import ActualMap
from pmap.reflexion import check_reflexion
from pmap.scanner import ProjectScanner
from pmap.stale import check_stale_nodes
from pmap.yaml_loader import load_design_map, validate_pmf_document

ROOT_DIR = Path(__file__).resolve().parent.parent
TOY_REPO = ROOT_DIR / "examples" / "toy_project"
TOY_DESIGN = ROOT_DIR / "examples" / "design.yaml"
SCHEMA_PATH = ROOT_DIR / "spec" / "pmf-v0.schema.json"


class TestPmapStage1(unittest.TestCase):
    """Tests for Stage 1 criteria: scanning, schema validation, reflexion check, mermaid, and stale detection."""

    def test_scan_ignores_macos_resource_forks(self) -> None:
        """External disks may contain binary AppleDouble files named ._*.py."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "example.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
            (root / "._example.py").write_bytes(b"\x00\x05\x16\x07\xff")
            actual = ProjectScanner().scan_repository(root)
            self.assertEqual(
                {node.id for node in actual.nodes if node.kind == "module"},
                {"mod:example"},
            )

    def test_scan_toy_project_and_validate_schema(self) -> None:
        """Criterion 1: `pmap scan examples/toy_project` succeeds and produces schema-valid PMF v0 JSON."""
        scanner = ProjectScanner()
        actual = scanner.scan_repository(TOY_REPO)
        doc = actual.to_dict()

        errors = validate_pmf_document(doc, schema_path=SCHEMA_PATH)
        self.assertEqual(errors, [], f"Schema validation failed: {errors}")

        # Verify expected module nodes
        node_ids = {n.id for n in actual.nodes}
        self.assertIn("mod:app.ui.cli", node_ids)
        self.assertIn("mod:app.services.order_service", node_ids)
        self.assertIn("mod:app.db.repository", node_ids)
        self.assertIn("mod:app.db.connection", node_ids)
        self.assertIn("mod:app.models.order", node_ids)

        # Verify external packages are marked external=True
        ext_nodes = {n.id: n for n in actual.nodes if n.external}
        self.assertIn("ext:dataclasses", ext_nodes)
        self.assertTrue(ext_nodes["ext:dataclasses"].external)
        self.assertIsNone(ext_nodes["ext:dataclasses"].location)

        # Verify internal nodes have valid sha256 file_hash and line bindings
        for node in actual.nodes:
            if not node.external:
                self.assertIsNotNone(node.location)
                assert node.location is not None
                self.assertTrue(node.location.file_hash.startswith("sha256:"))
                self.assertGreaterEqual(node.location.line_start, 1)
                self.assertGreaterEqual(node.location.line_end, node.location.line_start)

        # Verify static call resolution vs unresolved dynamic call
        resolved_calls = {
            (e.from_id, e.to_id)
            for e in actual.edges
            if e.kind == "calls" and e.confidence == "resolved"
        }
        self.assertIn(
            ("sym:app.ui.cli:handle_create_order", "sym:app.services.order_service:OrderService.create_order"),
            resolved_calls,
        )
        self.assertIn(
            ("sym:app.services.order_service:OrderService.create_order", "sym:app.db.repository:OrderRepository.save_order"),
            resolved_calls,
        )
        self.assertIn(
            ("sym:app.ui.cli:debug_dump_database", "sym:app.db.connection:execute_raw_sql"),
            resolved_calls,
        )

        # Dynamic hook call must NOT be marked resolved
        unresolved_calls = {
            (e.from_id, e.to_id)
            for e in actual.edges
            if e.kind == "calls" and e.confidence == "unresolved"
        }
        self.assertIn(
            ("sym:app.services.order_service:OrderService.create_order", "unresolved:hook.on_order_created"),
            unresolved_calls,
        )

    def test_reflexion_check_detects_exact_divergence_without_false_positives(self) -> None:
        """Criterion 2: `pmap check` finds the planted ui -> db divergence and reports zero false divergences."""
        design = load_design_map(TOY_DESIGN)
        design_errors = validate_pmf_document(design.to_dict(), schema_path=SCHEMA_PATH)
        self.assertEqual(design_errors, [])

        scanner = ProjectScanner()
        actual = scanner.scan_repository(TOY_REPO)
        report = check_reflexion(design, actual)

        # Exactly 1 divergence: ui -> db
        self.assertEqual(len(report.divergences), 1)
        div = report.divergences[0]
        self.assertEqual(div.from_subsystem, "ui")
        self.assertEqual(div.to_subsystem, "db")
        self.assertEqual(div.status, "divergence")
        self.assertGreaterEqual(len(div.evidence), 1)
        self.assertTrue(any(ev.from_module == "mod:app.ui.cli" and ev.to_module == "mod:app.db.connection" for ev in div.evidence))

        # All 5 allowed dependencies are present as convergences, 0 absences, 0 unmapped modules
        conv_pairs = {(c.from_subsystem, c.to_subsystem) for c in report.convergences}
        expected_convergences = {
            ("ui", "services"),
            ("ui", "models"),
            ("services", "db"),
            ("services", "models"),
            ("db", "models"),
        }
        self.assertEqual(conv_pairs, expected_convergences)
        self.assertEqual(len(report.absences), 0)
        self.assertEqual(len(report.unmapped_modules), 0)

    def test_mermaid_generation_subsystem_and_module_levels(self) -> None:
        """Criterion 3: `pmap mermaid` outputs valid Mermaid syntax with highlighted divergences."""
        design = load_design_map(TOY_DESIGN)
        scanner = ProjectScanner()
        actual = scanner.scan_repository(TOY_REPO)

        m_sub = generate_mermaid(actual, design=design, level="subsystem")
        self.assertTrue(m_sub.startswith("flowchart TD\n"))
        self.assertIn('sub_ui ==>|"DIVERGENCE:', m_sub)
        self.assertIn("stroke:#dc2626", m_sub)

        m_mod = generate_mermaid(actual, design=design, level="module")
        self.assertTrue(m_mod.startswith("flowchart TD\n"))
        self.assertIn("subgraph sg_ui", m_mod)
        self.assertIn('mod_app_ui_cli ==>|"DIVERGENCE:', m_mod)

        # Also test compact agent context generation with size budget
        ctx = format_agent_context(actual, design=design, max_chars=600)
        self.assertLessEqual(len(ctx), 650)
        self.assertIn("[DIVERGENCE] ui -> db", ctx)

    def test_stale_detection_after_file_modification_and_deletion(self) -> None:
        """Criterion 4: `pmap stale` detects modified and deleted files after initial scan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_repo = Path(tmpdir) / "toy_copy"
            shutil.copytree(TOY_REPO, tmp_repo)

            scanner = ProjectScanner()
            actual = scanner.scan_repository(tmp_repo)

            # Immediately after scan, 0 nodes should be stale
            initial_stale = check_stale_nodes(tmp_repo, actual)
            self.assertEqual(initial_stale, [])

            # Modify app/services/order_service.py
            service_file = tmp_repo / "app" / "services" / "order_service.py"
            service_file.write_text(
                service_file.read_text(encoding="utf-8") + "\n# Added comment triggering file hash change\n",
                encoding="utf-8",
            )

            stale_after_edit = check_stale_nodes(tmp_repo, actual)
            stale_ids = {s.node_id for s in stale_after_edit}
            # Every node inside app/services/order_service.py must be marked stale
            self.assertEqual(
                stale_ids,
                {
                    "mod:app.services.order_service",
                    "sym:app.services.order_service:validate_discount",
                    "sym:app.services.order_service:OrderService",
                    "sym:app.services.order_service:OrderService.__init__",
                    "sym:app.services.order_service:OrderService.create_order",
                },
            )
            self.assertTrue(all(s.reason == "hash_mismatch" for s in stale_after_edit))

            # Unmodified files must NOT be marked stale
            self.assertNotIn("mod:app.models.order", stale_ids)
            self.assertNotIn("mod:app.ui.cli", stale_ids)

            # Delete app/models/order.py and verify reason="file_missing"
            (tmp_repo / "app" / "models" / "order.py").unlink()
            stale_after_delete = check_stale_nodes(tmp_repo, actual)
            missing_nodes = [s for s in stale_after_delete if s.reason == "file_missing"]
            self.assertEqual(
                {s.node_id for s in missing_nodes},
                {
                    "mod:app.models.order",
                    "sym:app.models.order:Order",
                    "sym:app.models.order:Order.is_valid",
                },
            )

    def test_cli_commands_end_to_end(self) -> None:
        """Verify CLI entrypoints (`scan`, `check`, `mermaid`, `stale`) work via subprocess."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_repo = Path(tmpdir) / "toy_cli"
            shutil.copytree(TOY_REPO, tmp_repo)
            # Remove any pre-existing .pmap directory in the copy
            shutil.rmtree(tmp_repo / ".pmap", ignore_errors=True)

            # 1. pmap scan
            res_scan = subprocess.run(
                [sys.executable, "-m", "pmap", "scan", str(tmp_repo)],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Scanned toy_cli", res_scan.stdout)
            actual_json_path = tmp_repo / ".pmap" / "actual.json"
            self.assertTrue(actual_json_path.exists())

            # Validate generated JSON file against schema
            doc = json.loads(actual_json_path.read_text(encoding="utf-8"))
            self.assertEqual(validate_pmf_document(doc, schema_path=SCHEMA_PATH), [])

            # 2. pmap check --design
            res_check = subprocess.run(
                [sys.executable, "-m", "pmap", "check", str(tmp_repo), "--design", str(TOY_DESIGN), "--json"],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                check=True,
            )
            check_payload = json.loads(res_check.stdout)
            self.assertEqual(check_payload["summary"]["divergences"], 1)
            self.assertEqual(check_payload["divergences"][0]["from"], "ui")
            self.assertEqual(check_payload["divergences"][0]["to"], "db")

            # 3. pmap mermaid --level subsystem
            res_mermaid = subprocess.run(
                [sys.executable, "-m", "pmap", "mermaid", str(tmp_repo), "--level", "subsystem"],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("flowchart TD", res_mermaid.stdout)
            self.assertIn("DIVERGENCE", res_mermaid.stdout)

            # 4. pmap stale (before and after file modification)
            res_stale_clean = subprocess.run(
                [sys.executable, "-m", "pmap", "stale", str(tmp_repo)],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("0 stale nodes", res_stale_clean.stdout)

            cli_py = tmp_repo / "app" / "ui" / "cli.py"
            cli_py.write_text(cli_py.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")

            res_stale_dirty = subprocess.run(
                [sys.executable, "-m", "pmap", "stale", str(tmp_repo), "--json"],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                check=True,
            )
            stale_payload = json.loads(res_stale_dirty.stdout)
            self.assertEqual(len(stale_payload["stale_nodes"]), 4)
            self.assertTrue(all(item["path"] == "app/ui/cli.py" for item in stale_payload["stale_nodes"]))
            self.assertEqual(stale_payload["unscanned_files"], [])


if __name__ == "__main__":
    unittest.main()
