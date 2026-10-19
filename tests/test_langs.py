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
        self.assertEdge(self.INV, "sym:src/billing/invoice.ts:Repo.save", "inferred")  # an interface: any implementation
        # the compiler checks declared types: a class's method nobody overrides is exact
        self.assertEdge(self.INV, "sym:src/core/money.ts:Money.add", "resolved")  # local from `new Money()`
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
        self.assertEdge(issue, self.K + "orders/Order.kt:Order.count", "resolved")  # Kotlin class via wildcard import
        self.assertEdge(issue, self.J + "money/Money.java:Money.add", "resolved")  # typed local
        self.assertEdge(issue, "ambiguous:repo.save", "inferred",
                        candidates=[self.J + "billing/Repo.java:Repo.save", self.K + "app/App.kt:FakeRepo.save"])
        self.assertEdge(issue, "unresolved:unknown.call", "unresolved")

    def test_kotlin_and_imports(self) -> None:
        main = self.K + "app/App.kt:main"
        self.assertEdge(main, self.J + "billing/InvoiceService.java:InvoiceService", "resolved")
        self.assertEdge(main, self.K + "orders/Order.kt:formatOrder", "resolved")  # imported top-level function
        self.assertEdge(main, self.J + "billing/InvoiceService.java:InvoiceService.issue", "resolved")
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
        self.assertEdge(self.ISSUE, "sym:Orders/Order.cs:Order.Count", "resolved")
        self.assertEdge(self.ISSUE, "ambiguous:_repo.Save", "inferred",
                        candidates=["sym:Billing/BaseService.cs:IRepo.Save", "sym:Program.cs:FakeRepo.Save"])
        self.assertEdge(self.ISSUE, "unresolved:mystery.Call", "unresolved")
        self.assertNoEdge(self.ISSUE, "unresolved:Console.WriteLine")  # System is a library namespace

    def test_imports_and_top_level_statements(self) -> None:
        self.assertEdge("mod:Billing/InvoiceService.cs", "mod:Orders/Order.cs", "resolved", "imports")
        self.assertEdge("mod:Billing/InvoiceService.cs", "mod:Money/Money.cs", "resolved", "imports")
        self.assertEdge("mod:Billing/InvoiceService.cs", "ext:System", "resolved", "imports")
        self.assertEdge("mod:Program.cs", "sym:Billing/InvoiceService.cs:InvoiceService.Issue", "resolved")


@needs("rust")
class TestRust(LangCase):
    project = "rust_vpn"
    CONNECT = "sym:src/tunnel/mod.rs:Tunnel.connect"

    def test_modules_and_uses(self) -> None:
        self.assertEdge("mod:src/lib.rs", "mod:src/tunnel/mod.rs", "resolved", "imports")  # `pub mod tunnel;`
        self.assertEdge("mod:src/tunnel/mod.rs", "mod:src/tunnel/session.rs", "resolved", "imports")  # mod.rs layout
        self.assertEdge("mod:src/main.rs", "mod:src/config.rs", "resolved", "imports")  # `use vpn_core::config::...`
        self.assertEdge("sym:src/config.rs:ConfigStore.load", "sym:src/util.rs:default_server", "resolved")  # crate::

    def test_calls(self) -> None:
        self.assertEdge(self.CONNECT, "sym:src/config.rs:ConfigStore.load", "resolved")  # self.store: ConfigStore
        self.assertEdge(self.CONNECT, "sym:src/tunnel/session.rs:Session.open", "resolved")  # via `pub use`
        self.assertEdge(self.CONNECT, "sym:src/tunnel/session.rs:Session.handshake", "resolved")  # `-> Self`
        self.assertEdge(self.CONNECT, "sym:src/tunnel/mod.rs:Tunnel.log", "resolved")
        self.assertEdge(self.CONNECT, "ambiguous:self.transport.send", "inferred",  # Box<dyn Transport>
                        candidates=["sym:src/tunnel/mod.rs:Transport.send", "sym:src/tunnel/udp.rs:UdpTransport.send"])
        self.assertEdge(self.CONNECT, "unresolved:mystery.call", "unresolved")
        self.assertEdge("sym:src/main.rs:main", "sym:src/tunnel/mod.rs:Tunnel.connect", "resolved")  # let t = Tunnel::new()
        self.assertEdge("sym:src/tunnel/udp.rs:UdpTransport@impl", "sym:src/tunnel/mod.rs:Transport", "resolved",
                        "inherits")  # impl Transport for UdpTransport
        self.assertFalse([e for e in self.map.edges if "to_string" in e.to_id])  # &str: a library type


@needs("swift")
class TestSwift(LangCase):
    project = "swift_vpn"
    START = "sym:Sources/VPNCore/TunnelManager.swift:TunnelManager.start"

    def test_module_and_calls(self) -> None:
        self.assertEdge(self.START, "sym:Sources/VPNCore/ConfigStore.swift:ConfigStore.load", "resolved")  # same module
        self.assertEdge(self.START, "sym:Sources/VPNCore/Session.swift:Session", "resolved")  # Session(profile:)
        self.assertEdge(self.START, "sym:Sources/VPNCore/Session.swift:Session.connect", "resolved")
        self.assertEdge(self.START, "sym:Sources/VPNCore/Logger.swift:TunnelManager.log", "resolved")  # an extension
        self.assertEdge(self.START, "sym:Sources/VPNCore/TunnelManager.swift:BaseManager.describe", "resolved")
        self.assertEdge(self.START, "ambiguous:transport.send", "inferred",  # a protocol
                        candidates=["sym:Sources/VPNCore/TunnelManager.swift:Transport.send",
                                    "sym:Sources/VPNCore/UDPTransport.swift:UDPTransport.send"])
        self.assertEdge(self.START, "unresolved:mystery.call", "unresolved")

    def test_other_module_and_top_level_code(self) -> None:
        main = "mod:Sources/App/main.swift"
        self.assertEdge(main, "sym:Sources/VPNCore/TunnelManager.swift:TunnelManager.start", "resolved")
        self.assertEdge(main, "mod:Sources/VPNCore/TunnelManager.swift", "resolved", "imports")  # `import VPNCore`
        self.assertEdge("mod:Sources/VPNCore/TunnelManager.swift", "ext:Foundation", "resolved", "imports")


@needs("dart")
class TestDart(LangCase):
    project = "dart_vpn"
    CONNECT = "sym:lib/src/tunnel.dart:TunnelService.connect"

    def test_library_imports(self) -> None:
        self.assertEdge("mod:lib/vpn.dart", "mod:lib/src/tunnel.dart", "resolved", "imports")  # export
        self.assertEdge("mod:lib/ui/home.dart", "mod:lib/vpn.dart", "resolved", "imports")  # package:vpn_app/...
        self.assertEdge("mod:lib/ui/home.dart", "ext:flutter", "resolved", "imports")

    def test_calls_and_widgets(self) -> None:
        self.assertEdge(self.CONNECT, "sym:lib/src/config.dart:ConfigStore.load", "resolved")
        self.assertEdge(self.CONNECT, "sym:lib/src/tunnel.dart:Session.open", "resolved")
        self.assertEdge(self.CONNECT, "sym:lib/src/tunnel.dart:TunnelService._log", "resolved")  # implicit this
        self.assertEdge(self.CONNECT, "ambiguous:transport.send", "inferred")  # an abstract class with implementors
        self.assertEdge(self.CONNECT, "unresolved:mystery.call", "unresolved")
        self.assertEdge("sym:lib/ui/home.dart:HomePage.build", "sym:lib/src/tunnel.dart:TunnelService.connect",
                        "resolved")  # through the barrel export
        self.assertEdge("sym:lib/ui/home.dart:HomePage.build", "sym:lib/ui/home.dart:StatusCard", "resolved")  # widget
        self.assertEdge("sym:lib/main.dart:main", "sym:lib/src/tunnel.dart:TunnelService", "resolved")  # vpn.Tunnel...
        self.assertNoEdge("sym:lib/main.dart:main", "unresolved:runApp")  # Flutter's own function


@needs("cpp")
class TestCpp(LangCase):
    project = "cpp_vpn"
    START = "sym:src/manager.cpp:Manager.Start"

    def test_includes_and_out_of_line_methods(self) -> None:
        self.assertEdge("mod:src/manager.cpp", "mod:include/tunnel/manager.h", "resolved", "imports")
        self.assertEdge("mod:src/main.cpp", "ext:windows.h", "resolved", "imports")
        node = next(n for n in self.map.nodes if n.id == self.START)
        self.assertEqual((node.name, node.qualified_name), ("Manager::Start", "vpn::Manager::Start"))

    def test_calls(self) -> None:
        self.assertEdge(self.START, "sym:src/config.cpp:ConfigStore.Load", "resolved")  # the definition, not config.h
        self.assertEdge(self.START, "sym:src/session.cpp:Session.Open", "resolved")
        self.assertEdge(self.START, "sym:src/manager.cpp:Manager.Log", "resolved")  # implicit this
        self.assertEdge(self.START, "sym:src/util.cpp:Format", "resolved")  # util::Format inside namespace vpn
        self.assertEdge(self.START, "ambiguous:transport_.Send", "inferred",  # a virtual method with an override
                        candidates=["sym:include/tunnel/manager.h:Transport.Send",
                                    "sym:src/udp_transport.cpp:UdpTransport.Send"])
        self.assertNoEdge(self.START, "unresolved:MessageBoxW")  # Win32 comes from <windows.h>
        self.assertEdge("sym:src/main.cpp:main", "sym:include/tunnel/manager.h:Manager", "resolved")  # Manager m(...)
        self.assertEdge("sym:src/main.cpp:main", "sym:src/manager.cpp:Manager.Start", "resolved")


class TestWithoutGrammars(unittest.TestCase):
    def test_files_are_counted_but_not_mapped(self) -> None:
        from unittest import mock

        with mock.patch.object(langs, "available", return_value=False):
            actual = ProjectScanner().scan_repository(FIXTURES / "go_shop")
        self.assertEqual(actual.languages["go"], {"files": 5, "mapped": False})
        self.assertFalse([n for n in actual.nodes if not n.external])


if __name__ == "__main__":
    unittest.main()
