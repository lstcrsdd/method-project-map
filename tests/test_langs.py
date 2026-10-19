"""TypeScript / JavaScript, Go, Java, Kotlin and C# (tree-sitter): the same confidence rules as for Python.

Each fixture under tests/fixtures/langs is a small "shop" whose links are known in advance. The tests are
skipped when tree-sitter or a grammar is not installed (`pmap langs install`).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pmap import langs
from pmap.scanner import ProjectScanner

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "langs"


def needs(*languages: str):
    missing = [lang for lang in languages if not langs.available(lang)]
    return unittest.skipIf(missing, f"tree-sitter grammars not installed: {', '.join(missing)}")


class LangCase(unittest.TestCase):
    project = ""

    @classmethod
    def setUpClass(cls) -> None:
        if not cls.project:
            raise unittest.SkipTest("base class")
        cls.map = ProjectScanner().scan_repository(FIXTURES / cls.project)
        cls.ids = {n.id for n in cls.map.nodes}

    def edge(self, src: str, dst: str, kind: str = "calls"):
        found = [e for e in self.map.edges if e.from_id == src and e.to_id == dst and e.kind == kind]
        self.assertTrue(found, f"no {kind} edge {src} -> {dst}")
        return found[0]

    def assertEdge(self, src: str, dst: str, confidence: str, kind: str = "calls", candidates=None) -> None:
        e = self.edge(src, dst, kind)
        self.assertEqual(e.confidence, confidence, f"{src} -> {dst}")
        if candidates is not None:
            self.assertEqual(sorted(e.candidates or []), sorted(candidates))

    def assertNoEdge(self, src: str, dst: str, kind: str = "calls") -> None:
        self.assertFalse([e for e in self.map.edges if e.from_id == src and e.to_id == dst and e.kind == kind])


@needs("typescript", "javascript")
class TestTypeScript(LangCase):
    project = "ts_shop"
    INV = "sym:src/billing/invoice.ts:InvoiceService.issue"

    def test_languages_and_nodes(self) -> None:
        self.assertEqual(self.map.languages["typescript"], {"files": 8, "mapped": True})
        self.assertEqual(self.map.languages["javascript"], {"files": 1, "mapped": True})
        self.assertIn("sym:src/core/money.ts:Money.add", self.ids)
        node = next(n for n in self.map.nodes if n.id == "sym:src/billing/invoice.ts:InvoiceService")
        self.assertEqual(node.docstring_summary, "Creates invoices for orders.")

    def test_imports_through_alias_barrel_and_types(self) -> None:
        mod = "mod:src/billing/invoice.ts"
        self.assertEdge(mod, "mod:src/core/index.ts", "resolved", "imports")  # "@core/index" via tsconfig paths
        self.assertTrue(any(e.type_only for e in self.map.edges if e.from_id == mod and e.to_id == "mod:src/models/order.ts"))
        self.assertEdge("mod:src/app.tsx", "ext:react", "resolved", "imports")
        self.assertEdge("mod:src/broken.ts", "unresolved:import:./missing", "unresolved", "imports")

    def test_calls(self) -> None:
        self.assertEdge(self.INV, "sym:src/core/money.ts:sum", "resolved")  # re-exported by core/index
        self.assertEdge(self.INV, "sym:src/core/log.ts:log", "resolved")  # `export { default as log }`
        self.assertEdge(self.INV, "sym:src/billing/tax.ts:apply", "resolved")  # `import * as tax`
        self.assertEdge(self.INV, "sym:src/core/money.ts:Money", "resolved")  # new Money()
        # a subclass overrides describe(): the call may land in either
        self.assertEdge(self.INV, "ambiguous:this.describe", "inferred",
                        candidates=["sym:src/models/order.ts:BaseService.describe",
                                    "sym:src/billing/invoice.ts:SpecialInvoiceService.describe"])
        self.assertEdge(self.INV, "sym:src/billing/invoice.ts:Repo.save", "inferred")  # field typed by a parameter property
        self.assertEdge(self.INV, "sym:src/core/money.ts:Money.add", "inferred")  # local from `new Money()`
        self.assertEdge(self.INV, "unresolved:unknownThing.run", "unresolved")
        self.assertNoEdge("sym:src/core/money.ts:sum", "unresolved:values.reduce")  # array method: not a project link

    def test_jsx_and_commonjs(self) -> None:
        self.assertEdge("sym:src/app.tsx:App", "sym:src/app.tsx:Summary", "resolved")  # <Summary />
        self.assertEdge("sym:src/app.tsx:App", "sym:src/billing/invoice.ts:InvoiceService", "resolved")
        self.assertEdge("sym:src/legacy.js:total", "sym:src/core/money.ts:sum", "resolved")  # const { sum } = require()
        self.assertEdge("sym:src/legacy.js:total", "sym:src/billing/tax.ts:apply", "resolved")  # tax = require(); tax.apply

    def test_inheritance(self) -> None:
        self.assertEdge("sym:src/billing/invoice.ts:InvoiceService", "sym:src/models/order.ts:BaseService", "resolved",
                        "inherits")


@needs("go")
class TestGo(LangCase):
    project = "go_shop"
    NEW = "sym:billing/invoice.go:NewInvoice"

    def test_packages_and_methods(self) -> None:
        self.assertEdge("mod:billing/invoice.go", "mod:money/money.go", "resolved", "imports")  # aliased import
        self.assertEdge("mod:billing/invoice.go", "ext:fmt", "resolved", "imports")
        self.assertEdge("mod:main.go", "mod:billing/invoice.go", "resolved", "imports")
        self.assertEdge(self.NEW, "sym:billing/helper.go:helper", "resolved")  # same package, another file
        self.assertEdge(self.NEW, "sym:money/money.go:Sum", "resolved")  # m.Sum
        self.assertEdge(self.NEW, "sym:billing/invoice.go:Invoice", "resolved")  # &Invoice{}
        # concrete types have no virtual dispatch: exact, even through a parameter or an embedded struct
        self.assertEdge(self.NEW, "sym:orders/order.go:Order.Total", "resolved")
        self.assertEdge(self.NEW, "sym:billing/invoice.go:Base.Describe", "resolved")
        self.assertEdge(self.NEW, "sym:billing/invoice.go:Notifier.Notify", "inferred")  # through an interface
        self.assertEdge(self.NEW, "unresolved:mystery.Call", "unresolved")
        self.assertEdge("sym:billing/invoice.go:Invoice", "sym:billing/invoice.go:Base", "resolved", "inherits")


@needs("java", "kotlin")
class TestJvm(LangCase):
    project = "jvm_shop"
    J = "sym:src/main/java/com/shop/"
    K = "sym:src/main/kotlin/com/shop/"

    def test_java_calls(self) -> None:
        issue = self.J + "billing/InvoiceService.java:InvoiceService.issue"
        self.assertEdge(issue, self.J + "money/Money.java:Money.sum", "resolved")  # static, imported class
        self.assertEdge(issue, self.J + "billing/BaseService.java:BaseService.describe", "resolved")  # implicit this
        self.assertEdge(issue, self.J + "billing/Helper.java:Helper.log", "resolved")  # same package, no import
        self.assertEdge(issue, self.K + "orders/Order.kt:Order.count", "inferred")  # Kotlin class via wildcard import
        self.assertEdge(issue, "ambiguous:repo.save", "inferred",
                        candidates=[self.J + "billing/Repo.java:Repo.save", self.K + "app/App.kt:FakeRepo.save"])
        self.assertEdge(issue, "unresolved:unknown.call", "unresolved")

    def test_kotlin_and_imports(self) -> None:
        main = self.K + "app/App.kt:main"
        self.assertEdge(main, self.J + "billing/InvoiceService.java:InvoiceService", "resolved")
        self.assertEdge(main, self.K + "orders/Order.kt:formatOrder", "resolved")  # imported top-level function
        self.assertEdge(main, self.J + "billing/InvoiceService.java:InvoiceService.issue", "inferred")
        self.assertEdge(self.K + "app/App.kt:FakeRepo", self.J + "billing/Repo.java:Repo", "resolved", "inherits")
        self.assertEdge("mod:src/main/java/com/shop/billing/InvoiceService.java", "ext:java.util", "resolved", "imports")


@needs("csharp")
class TestCSharp(LangCase):
    project = "cs_shop"
    ISSUE = "sym:Billing/InvoiceService.cs:InvoiceService.Issue"

    def test_calls(self) -> None:
        self.assertEdge(self.ISSUE, "sym:Money/Money.cs:Money.Sum", "resolved")  # through `using M = Shop.Money`
        self.assertEdge(self.ISSUE, "sym:Billing/BaseService.cs:BaseService.Describe", "resolved")
        self.assertEdge(self.ISSUE, "sym:Billing/BaseService.cs:Helper.Log", "resolved")  # same namespace
        self.assertEdge(self.ISSUE, "sym:Orders/Order.cs:Order.Count", "inferred")
        self.assertEdge(self.ISSUE, "ambiguous:_repo.Save", "inferred",
                        candidates=["sym:Billing/BaseService.cs:IRepo.Save", "sym:Program.cs:FakeRepo.Save"])
        self.assertEdge(self.ISSUE, "unresolved:mystery.Call", "unresolved")
        self.assertNoEdge(self.ISSUE, "unresolved:Console.WriteLine")  # System is a library namespace

    def test_imports_and_top_level_statements(self) -> None:
        self.assertEdge("mod:Billing/InvoiceService.cs", "mod:Orders/Order.cs", "resolved", "imports")
        self.assertEdge("mod:Billing/InvoiceService.cs", "mod:Money/Money.cs", "resolved", "imports")
        self.assertEdge("mod:Billing/InvoiceService.cs", "ext:System", "resolved", "imports")
        self.assertEdge("mod:Program.cs", "sym:Billing/InvoiceService.cs:InvoiceService.Issue", "inferred")


class TestWithoutGrammars(unittest.TestCase):
    def test_files_are_counted_but_not_mapped(self) -> None:
        from unittest import mock

        with mock.patch.object(langs, "available", return_value=False):
            actual = ProjectScanner().scan_repository(FIXTURES / "go_shop")
        self.assertEqual(actual.languages["go"], {"files": 5, "mapped": False})
        self.assertFalse([n for n in actual.nodes if not n.external])


if __name__ == "__main__":
    unittest.main()
