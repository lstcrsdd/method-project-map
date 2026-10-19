"""Agent benchmark: the same bug-fix tasks with and without the Method Project Map.

Usage (from the repository root):
  python3 bench/run.py --validate                      # check the tasks themselves, no agent
  python3 bench/run.py --dry-run --repeats 1           # print the agent commands
  python3 bench/run.py --repeats 3 --model sonnet      # real run (spends agent usage!)
  python3 bench/report.py bench/results/<run-dir>      # summary table

Every run gets a fresh copy of the corpus with one bug injected. Modes:
  baseline - plain agent;
  map      - the same prompt, plus a short system-prompt note and a `pmap` command on PATH.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
ROOT_DIR = BENCH_DIR.parent
sys.path.insert(0, str(BENCH_DIR))

import tasks as stdlib_suite  # noqa: E402
from tasks import CORPUS_PACKAGES, MAP_SYSTEM_PROMPT, Task  # noqa: E402

DEFAULT_PYTHON = "/opt/homebrew/bin/python3"
DEFAULT_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
DEFAULT_MODELS = {"claude": "sonnet", "codex": "gpt-6.1-sol"}
MODES = ("baseline", "map", "map-context")
MAP_MODES = ("map", "map-context")
AGENT_TOOLS = "Bash,Read,Edit,Write,Glob,Grep"
IGNORED_PARTS = {".pmap", "__pycache__"}
# Provider-side failures: the run says nothing about the agent, so it is retried / excluded.
RETRYABLE_ERRORS = re.compile(r"at capacity|overloaded|rate.?limit|429|temporarily unavailable|try again later", re.I)
FATAL_INFRA_ERRORS = re.compile(r"usage limit|quota|insufficient credit|failed to authenticate|not logged in", re.I)


def infra_error(events_path: Path) -> tuple[str, bool] | None:
    """(message, retryable) when the agent stopped because of the provider, else None."""
    text = events_path.read_text(encoding="utf-8", errors="replace") if events_path.exists() else ""
    for raw in text.splitlines():
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        message = ""
        if ev.get("type") in ("error", "turn.failed"):
            message = ev.get("message") or (ev.get("error") or {}).get("message") or ""
        elif ev.get("type") == "result" and ev.get("is_error"):
            message = str(ev.get("result") or "")
        if FATAL_INFRA_ERRORS.search(message):
            return message, False
        if RETRYABLE_ERRORS.search(message):
            return message, True
    return None


# ---------------------------------------------------------------- environment


def find_claude_binary() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    pattern = os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"
    )
    candidates = sorted(glob.glob(pattern), key=lambda p: [int(x) for x in re.findall(r"\d+", p.split("/claude-code/")[1].split("/")[0])])
    return candidates[-1] if candidates else None


def stdlib_dir(python: str) -> Path:
    out = subprocess.run([python, "-c", "import sysconfig; print(sysconfig.get_paths()['stdlib'])"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


class Suite:
    """A task set: its tasks, prompt and how its corpus is built."""

    def __init__(self, name: str) -> None:
        self.name = name
        if name == "stdlib":
            module = stdlib_suite
            self.build_corpus = build_corpus
            self.visible_tests: list[str] = []
        elif name == "tallyhub":
            import tasks_tallyhub as module  # noqa: PLC0415
            self.build_corpus = module.build_corpus
            self.visible_tests = module.VISIBLE_TESTS
        elif name == "tallyhub-large":
            import tasks_tallyhub_large as module  # noqa: PLC0415
            self.build_corpus = module.build_corpus
            self.visible_tests = module.VISIBLE_TESTS
        else:
            raise SystemExit(f"unknown suite {name!r}")
        self.tasks: list[Task] = list(module.TASKS)
        self.by_id = {t.id: t for t in self.tasks}
        self.prompt_template: str = module.PROMPT_TEMPLATE


def build_corpus(python: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    src = stdlib_dir(python)
    dest.mkdir(parents=True)
    for pkg in CORPUS_PACKAGES:
        shutil.copytree(src / pkg, dest / pkg, ignore=shutil.ignore_patterns("__pycache__", "._*"))
    return dest


def inject(task: Task, workspace: Path) -> None:
    path = workspace / task.file
    text = path.read_text(encoding="utf-8")
    count = text.count(task.old)
    if count != 1:
        raise RuntimeError(f"{task.id}: expected exactly one occurrence of the injection anchor, found {count}")
    path.write_text(text.replace(task.old, task.new), encoding="utf-8")


def make_bin_dir(bin_dir: Path, python: str, with_pmap: bool) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("python3", "python"):
        link = bin_dir / name
        if not link.exists():
            link.symlink_to(python)
    if with_pmap:
        wrapper = bin_dir / "pmap"
        wrapper.write_text(
            "#!/bin/sh\n"
            f"PYTHONPATH='{ROOT_DIR}' exec '{python}' -m pmap \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)


HIDDEN_TESTS_DIR = BENCH_DIR / "hidden_tests"


def run_unittests(python: str, workspace: Path, test_ids: list[str], cwd: Path, timeout: int = 900) -> dict:
    # The workspace comes first so its package copies shadow the stdlib; bench-only tests come after.
    pythonpath = os.pathsep.join([str(workspace), str(HIDDEN_TESTS_DIR)])
    env = dict(os.environ, PYTHONPATH=pythonpath, PYTHONDONTWRITEBYTECODE="1")
    started = time.time()
    try:
        proc = subprocess.run([python, "-m", "unittest", *test_ids], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=timeout)
        output, code = proc.stderr + proc.stdout, proc.returncode
    except subprocess.TimeoutExpired:
        output, code = "TIMEOUT", -1
    ran = re.search(r"Ran (\d+) tests?", output)
    return {
        "passed": code == 0,
        "returncode": code,
        "tests_run": int(ran.group(1)) if ran else 0,
        "seconds": round(time.time() - started, 1),
        "tail": "\n".join(output.strip().splitlines()[-6:]),
    }


def run_repro(python: str, workspace: Path, code: str, cwd: Path) -> str:
    env = dict(os.environ, PYTHONPATH=str(workspace), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([python, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    return (proc.stdout + proc.stderr).strip()


def tree_files(root: Path) -> dict[str, Path]:
    result = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not any(p in IGNORED_PARTS or p.startswith("._") for p in rel.parts):
            result[rel.as_posix()] = path
    return result


def diff_trees(before: Path, after: Path) -> dict:
    a, b = tree_files(before), tree_files(after)
    changed, diff_lines = [], []
    for rel in sorted(set(a) | set(b)):
        old = a[rel].read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if rel in a else []
        new = b[rel].read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if rel in b else []
        if old != new:
            changed.append(rel)
            diff_lines.extend(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}"))
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    return {"changed_files": changed, "lines_added": added, "lines_removed": removed, "diff": "".join(diff_lines)}


# ---------------------------------------------------------------- agent output


def parse_codex_stream(events_path: Path, outside_markers: list[str]) -> dict:
    """Codex `exec --json`: items (command_execution, file_change, ...) and per-turn usage."""
    tool_calls: dict[str, int] = {}
    pmap_calls = 0
    outside_hits: list[str] = []
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    turns, failed, last_message = 0, False, ""
    for raw in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type", "")
        if kind == "turn.completed":
            turns += 1
            for key in usage:
                usage[key] += int((ev.get("usage") or {}).get(key) or 0)
        elif kind in ("turn.failed", "error"):
            failed = True
        elif kind == "item.completed":
            item = ev.get("item") or {}
            item_type = item.get("type", "?")
            if item_type == "agent_message":
                last_message = item.get("text") or ""
                continue
            if item_type in ("reasoning", "todo_list"):
                continue
            tool_calls[item_type] = tool_calls.get(item_type, 0) + 1
            payload = json.dumps(item, ensure_ascii=False)
            if item_type == "command_execution":
                payload = str(item.get("command", ""))
                if re.search(r"(^|[\s;&|('\"])pmap\s", payload):
                    pmap_calls += 1
            elif item_type == "file_change":
                payload = json.dumps(item.get("changes", []), ensure_ascii=False)
            for marker in outside_markers:
                if marker in payload:
                    outside_hits.append(payload[:300])
                    break
    return {
        "finished": turns > 0,
        "agent_error": failed or turns == 0,
        "result_subtype": None,
        "num_turns": None,
        "duration_ms": None,
        "cost_usd": None,
        "input_tokens": usage["input_tokens"] - usage["cached_input_tokens"],
        "cache_creation_tokens": 0,
        "cache_read_tokens": usage["cached_input_tokens"],
        "input_tokens_total": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "reasoning_tokens": usage["reasoning_output_tokens"],
        "tool_calls": sum(tool_calls.values()),
        "tool_calls_by_name": tool_calls,
        "pmap_calls": pmap_calls,
        "outside_access": outside_hits,
        "final_message": last_message[:500],
    }


def parse_stream(events_path: Path, outside_markers: list[str]) -> dict:
    tool_calls: dict[str, int] = {}
    pmap_calls = 0
    outside_hits: list[str] = []
    result: dict = {}
    for raw in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "?")
                tool_calls[name] = tool_calls.get(name, 0) + 1
                payload = json.dumps(block.get("input", {}), ensure_ascii=False)
                if name == "Bash" and re.search(r"(^|[\s;&|(])pmap\s", str(block.get("input", {}).get("command", ""))):
                    pmap_calls += 1
                for marker in outside_markers:
                    if marker in payload:
                        outside_hits.append(payload[:300])
                        break
        elif ev.get("type") == "result":
            result = ev
    usage = result.get("usage") or {}
    input_total = sum(int(usage.get(k) or 0) for k in
                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return {
        "finished": bool(result),
        "agent_error": bool(result.get("is_error")) if result else True,
        "result_subtype": result.get("subtype"),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "cost_usd": result.get("total_cost_usd"),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cache_creation_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "input_tokens_total": input_total,
        "output_tokens": int(usage.get("output_tokens") or 0),
        "tool_calls": sum(tool_calls.values()),
        "tool_calls_by_name": tool_calls,
        "pmap_calls": pmap_calls,
        "outside_access": outside_hits,
        "final_message": (result.get("result") or "")[:500],
    }


def build_prompt(task_prompt: str, mode: str, context: str | None) -> str:
    """The user prompt. `map-context` puts the map overview into the prompt itself."""
    if mode == "map-context":
        return (f"{MAP_SYSTEM_PROMPT}\n\nProject map overview (output of `pmap context .`):\n"
                f"```\n{context or ''}```\n\n{task_prompt}")
    return task_prompt


def agent_command(args: argparse.Namespace, prompt: str, mode: str, workspace: Path) -> list[str]:
    if args.agent == "codex":
        # Codex has no system-prompt flag: the map note goes in front of the same task prompt.
        full_prompt = f"{MAP_SYSTEM_PROMPT}\n\n{prompt}" if mode == "map" else prompt
        return [
            args.codex_bin, "exec", "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
            "-s", "workspace-write", "-C", str(workspace),
            "-m", args.model, "-c", f"model_reasoning_effort={args.effort}",
            full_prompt,
        ]
    cmd = [
        args.claude_bin, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--safe-mode", "--no-session-persistence",
        "--tools", AGENT_TOOLS, "--allowedTools", AGENT_TOOLS,
        "--max-budget-usd", str(args.budget_usd),
    ]
    if args.model:
        cmd += ["--model", args.model]
    if mode == "map":
        cmd += ["--append-system-prompt", MAP_SYSTEM_PROMPT]
    return cmd


def agent_env(bin_dir: Path) -> dict[str, str]:
    # Drop every CLAUDE* variable: when the harness itself runs inside Claude Code, they would make the
    # child a nested session (bare mode, host auth, shared session ids) instead of a standalone CLI run.
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("CLAUDE") and k not in ("ANTHROPIC_BASE_URL", "DISABLE_MICROCOMPACT")}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


# ---------------------------------------------------------------- one run


def execute_run(args: argparse.Namespace, task: Task, mode: str, repeat: int, corpus: Path, out_dir: Path) -> dict:
    """One run; provider capacity errors are retried from a fresh workspace with growing pauses."""
    for attempt in range(1, args.retries + 2):
        record = execute_attempt(args, task, mode, repeat, corpus, out_dir)
        record["attempt"] = attempt
        problem = record.get("infra_error")
        if not problem or not record.get("infra_retryable") or attempt > args.retries:
            return record
        time.sleep(args.retry_pause * attempt)
    return record


def execute_attempt(args: argparse.Namespace, task: Task, mode: str, repeat: int, corpus: Path, out_dir: Path) -> dict:
    run_dir = out_dir / "runs" / f"{task.id}__{mode}__{repeat}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    workspace, start_state, bin_dir = run_dir / "ws", run_dir / "start", run_dir / "bin"
    shutil.copytree(corpus, workspace)
    inject(task, workspace)
    shutil.copytree(workspace, start_state)
    make_bin_dir(bin_dir, args.python, with_pmap=(mode in MAP_MODES))

    record: dict = {"task": task.id, "mode": mode, "repeat": repeat, "agent": args.agent, "model": args.model,
                    "started_at": datetime.now().isoformat(timespec="seconds")}
    context = None
    if mode in MAP_MODES:
        t0 = time.time()
        scan = subprocess.run([str(bin_dir / "pmap"), "scan", str(workspace)], capture_output=True, text=True)
        record["map_scan_seconds"] = round(time.time() - t0, 2)
        record["map_scan_ok"] = scan.returncode == 0
        if mode == "map-context":
            context = subprocess.run([str(bin_dir / "pmap"), "context", str(workspace),
                                      "--max-chars", str(args.context_chars)],
                                     capture_output=True, text=True).stdout
            (run_dir / "context.txt").write_text(context, encoding="utf-8")
            record["context_chars"] = len(context)

    prompt = build_prompt(args.suite_obj.prompt_template.format(symptom=task.symptom), mode, context)
    cmd = agent_command(args, prompt, mode, workspace)
    (run_dir / "command.json").write_text(json.dumps(cmd, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.dry_run:
        record["dry_run"] = True
        return record

    events_path = run_dir / "events.jsonl"
    t0 = time.time()
    with events_path.open("w", encoding="utf-8") as out, (run_dir / "stderr.log").open("w") as err:
        try:
            proc = subprocess.run(cmd, cwd=workspace, env=agent_env(bin_dir), stdin=subprocess.DEVNULL,
                                  stdout=out, stderr=err, timeout=args.timeout)
            record["agent_returncode"] = proc.returncode
            record["timed_out"] = False
        except subprocess.TimeoutExpired:
            record["agent_returncode"] = None
            record["timed_out"] = True
    record["wall_seconds"] = round(time.time() - t0, 1)

    markers = [str(stdlib_dir(args.python)), "/opt/homebrew/Cellar", "/Library/Frameworks/Python",
               "site-packages", "sysconfig", "inspect.getsource",
               # the pristine corpus and the hidden tests live in the harness checkout
               str(BENCH_DIR), "hidden_tests", "corpora/"]
    parser = parse_codex_stream if args.agent == "codex" else parse_stream
    record.update(parser(events_path, markers))
    infra = infra_error(events_path)
    if infra:
        record["infra_error"], record["infra_retryable"] = infra

    changes = diff_trees(start_state, workspace)
    (run_dir / "fix.diff").write_text(changes.pop("diff"), encoding="utf-8")
    record.update(changes)
    record["only_target_file_changed"] = changes["changed_files"] == [task.file]
    original = (corpus / task.file).read_text(encoding="utf-8")
    record["bug_reverted_exactly"] = (workspace / task.file).read_text(encoding="utf-8") == original

    record["hidden"] = run_unittests(args.python, workspace, task.hidden_tests, run_dir)
    record["regression"] = run_unittests(args.python, workspace, task.regression_tests, run_dir)
    record["success"] = bool(record["hidden"]["passed"] and record["regression"]["passed"]
                             and not record.get("timed_out"))
    shutil.rmtree(start_state, ignore_errors=True)
    if not args.keep_workspaces:
        shutil.rmtree(workspace, ignore_errors=True)
    return record


# ---------------------------------------------------------------- validation


def validate(args: argparse.Namespace, corpus: Path, out_dir: Path) -> int:
    """Without any agent: each bug must break its hidden tests, and the pristine corpus must pass them."""
    failures = 0
    for task in selected_tasks(args):
        work = out_dir / "validate" / task.id
        shutil.rmtree(work, ignore_errors=True)
        pristine, buggy = work / "pristine", work / "buggy"
        shutil.copytree(corpus, pristine)
        shutil.copytree(corpus, buggy)
        inject(task, buggy)
        ok_hidden = run_unittests(args.python, pristine, task.hidden_tests, work)
        bad_hidden = run_unittests(args.python, buggy, task.hidden_tests, work)
        ok_regr = run_unittests(args.python, pristine, task.regression_tests, work)
        repro_ok = run_repro(args.python, pristine, task.repro, work)
        repro_bad = run_repro(args.python, buggy, task.repro, work)
        visible_bad = run_unittests(args.python, buggy, args.suite_obj.visible_tests, work) \
            if args.suite_obj.visible_tests else None
        good = ok_hidden["passed"] and not bad_hidden["passed"] and ok_regr["passed"] and repro_ok != repro_bad
        if visible_bad is not None:
            # A realistic bug slips past the visible suite; otherwise a failing test would point at it.
            good = good and visible_bad["passed"]
        failures += 0 if good else 1
        print(f"[{'OK' if good else 'FAIL'}] {task.id}")
        print(f"    hidden on pristine: {ok_hidden['passed']} ({ok_hidden['tests_run']} tests, {ok_hidden['seconds']}s)")
        print(f"    hidden with bug:    {bad_hidden['passed']} ({bad_hidden['tests_run']} tests)")
        print(f"    regression pristine:{ok_regr['passed']} ({ok_regr['tests_run']} tests, {ok_regr['seconds']}s)")
        if visible_bad is not None:
            print(f"    visible suite with bug: {visible_bad['passed']} ({visible_bad['tests_run']} tests)")
        print(f"    repro pristine: {repro_ok}")
        print(f"    repro with bug: {repro_bad}")
        if not good:
            print("    " + bad_hidden["tail"].replace("\n", "\n    "))
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failures else 0


def selected_tasks(args: argparse.Namespace) -> list[Task]:
    suite: Suite = args.suite_obj
    if not args.tasks or args.tasks == "all":
        return list(suite.tasks)
    return [suite.by_id[t.strip()] for t in args.tasks.split(",")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", choices=("stdlib", "tallyhub", "tallyhub-large"), default="stdlib")
    parser.add_argument("--context-chars", type=int, default=16000, help="pmap context budget for map-context mode")
    parser.add_argument("--python", default=DEFAULT_PYTHON, help="Interpreter whose stdlib forms the corpus")
    parser.add_argument("--agent", choices=("claude", "codex"), default="claude")
    parser.add_argument("--claude-bin", default=find_claude_binary())
    parser.add_argument("--codex-bin", default=DEFAULT_CODEX)
    parser.add_argument("--model", help="Default: sonnet (claude) / gpt-6.1-sol (codex)")
    parser.add_argument("--effort", default="high", help="Codex reasoning effort")
    parser.add_argument("--tasks", default="all", help="Comma-separated task ids")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--budget-usd", type=float, default=3.0, help="Per-run cap passed to the agent")
    parser.add_argument("--timeout", type=int, default=1500, help="Per-run wall-clock limit, seconds")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0, help="Shuffles run order so modes interleave")
    parser.add_argument("--out", help="Results directory (default: bench/results/<timestamp>)")
    parser.add_argument("--keep-workspaces", action="store_true")
    parser.add_argument("--retries", type=int, default=3, help="Retries after provider capacity errors")
    parser.add_argument("--retry-pause", type=int, default=60, help="Seconds; multiplied by the attempt number")
    parser.add_argument("--resume", action="store_true",
                        help="Skip runs already in results.jsonl unless they ended with an infra/harness error")
    parser.add_argument("--validate", action="store_true", help="Validate tasks without running agents")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else BENCH_DIR / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Keep the corpus next to the runs: workspaces must not live on volumes that add `._*` sidecar files.
    args.suite_obj = Suite(args.suite)
    corpus = args.suite_obj.build_corpus(args.python, out_dir.parent / f".corpus-{args.suite}")

    if args.validate:
        return validate(args, corpus, out_dir)
    args.model = args.model or DEFAULT_MODELS[args.agent]
    agent_bin = args.codex_bin if args.agent == "codex" else args.claude_bin
    if not agent_bin or not Path(agent_bin).exists():
        print(f"{args.agent} CLI not found; pass --{args.agent}-bin", file=sys.stderr)
        return 2

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    plan = [(t, m, r) for t in selected_tasks(args) for m in modes for r in range(1, args.repeats + 1)]
    random.Random(args.seed).shuffle(plan)
    if args.resume and (out_dir / "results.jsonl").exists():
        done = set()
        for line in (out_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
            rec = json.loads(line) if line.strip() else {}
            if rec and not rec.get("infra_error") and not rec.get("harness_error"):
                done.add((rec["task"], rec["mode"], rec["repeat"]))
        plan = [(t, m, r) for t, m, r in plan if (t.id, m, r) not in done]
    (out_dir / "config.json").write_text(json.dumps({
        "suite": args.suite, "context_chars": args.context_chars,
        "agent": args.agent, "model": args.model, "effort": args.effort if args.agent == "codex" else None,
        "repeats": args.repeats, "modes": modes, "budget_usd": args.budget_usd,
        "tasks": [t.id for t in selected_tasks(args)], "python": args.python, "agent_bin": agent_bin,
        "agent_version": subprocess.run([agent_bin, "--version"], capture_output=True, text=True).stdout.strip(),
    }, indent=2), encoding="utf-8")

    results_path = out_dir / "results.jsonl"
    print(f"{len(plan)} runs -> {out_dir}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(execute_run, args, t, m, r, corpus, out_dir): (t.id, m, r) for t, m, r in plan}
        for fut in concurrent.futures.as_completed(futures):
            task_id, mode, rep = futures[fut]
            try:
                record = fut.result()
            except Exception as exc:  # keep the batch going; the failure is recorded
                record = {"task": task_id, "mode": mode, "repeat": rep, "harness_error": repr(exc)}
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            status = "dry" if record.get("dry_run") else (
                "OK " if record.get("success") else
                "ERR" if record.get("harness_error") else
                "INF" if record.get("infra_error") else "BAD")
            print(f"[{status}] {task_id:32} {mode:8} #{rep}  cost=${record.get('cost_usd')}  "
                  f"in={record.get('input_tokens_total')} out={record.get('output_tokens')} "
                  f"tools={record.get('tool_calls')} pmap={record.get('pmap_calls')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
