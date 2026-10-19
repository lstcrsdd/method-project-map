"""Round 3 tasks: the six tallyhub tasks inside `tallyhub-large` (~29k lines, ~460 modules).

The bugs and hidden tests are identical to round 2; only the surrounding codebase is ~8x larger and
full of look-alike code (see corpora/gen_large.py). The regression suite also covers every generated domain.
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

from tasks_tallyhub import PROMPT_TEMPLATE  # noqa: F401  (same prompt as round 2)
from tasks_tallyhub import TASKS as CORE_TASKS
from tasks_tallyhub import VISIBLE_TESTS as CORE_VISIBLE_TESTS

SUITE = "tallyhub-large"

_spec = importlib.util.spec_from_file_location("gen_large", Path(__file__).resolve().parent / "corpora" / "gen_large.py")
gen_large = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_large)  # type: ignore[union-attr]

VISIBLE_TESTS = CORE_VISIBLE_TESTS + [f"tests.test_ext_{domain[0]}" for domain in gen_large.DOMAINS]
REGRESSION = ["test_hidden_tallyhub"] + VISIBLE_TESTS

TASKS = [replace(task, regression_tests=REGRESSION) for task in CORE_TASKS]
TASKS_BY_ID = {t.id: t for t in TASKS}


def build_corpus(python: str, dest: Path) -> Path:
    if not dest.exists():
        gen_large.build(dest)
    return dest
