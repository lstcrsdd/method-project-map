"""Summarise bench results: per task and mode, then map vs baseline.

Usage: python3 bench/report.py bench/results/<run-dir> [--markdown out.md]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

METRICS = [
    ("input_tokens_total", "input tok"),
    ("output_tokens", "output tok"),
    ("cost_usd", "cost $"),
    ("tool_calls", "tools"),
    ("num_turns", "turns"),
    ("wall_seconds", "time s"),
]


def load(run_dir: Path) -> list[dict]:
    """Latest record per (task, mode, repeat): `--resume` appends re-runs to the same file."""
    latest: dict[tuple, dict] = {}
    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            latest[(rec["task"], rec["mode"], rec["repeat"])] = rec
    return list(latest.values())


def median(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return statistics.median(clean) if clean else None


def fmt(value: float | None, metric: str) -> str:
    if value is None:
        return "–"
    if metric == "cost_usd":
        return f"{value:.2f}"
    if metric in ("input_tokens_total", "output_tokens"):
        return f"{value / 1000:.0f}k" if value >= 1000 else f"{value:.0f}"
    return f"{value:.0f}"


def quartiles(values: list[float]) -> tuple[float, float]:
    clean = sorted(v for v in values if v is not None)
    if len(clean) < 2:
        return (clean[0], clean[0]) if clean else (float("nan"), float("nan"))
    q = statistics.quantiles(clean, n=4, method="inclusive")
    return q[0], q[2]


def geo_mean_ratio(groups: dict, metric: str, variant: str, pick=None) -> float | None:
    """Geometric mean over tasks of median(variant) / median(baseline)."""
    logs = []
    for task, by_mode in groups.items():
        base = [r.get(metric) for r in (pick(by_mode["baseline"]) if pick else by_mode["baseline"])]
        var = [r.get(metric) for r in (pick(by_mode[variant]) if pick else by_mode[variant])]
        b, v = median(base), median(var)
        if b and v:
            logs.append(math.log(v / b))
    return math.exp(sum(logs) / len(logs)) if logs else None


def bootstrap_ci(groups: dict, metric: str, variant: str, rounds: int = 2000, seed: int = 7) -> tuple[float, float]:
    """95% interval of the geometric-mean ratio, resampling runs within each (task, mode)."""
    rng = random.Random(seed)
    estimates = []
    for _ in range(rounds):
        value = geo_mean_ratio(groups, metric, variant, pick=lambda rows: [rng.choice(rows) for _ in rows])
        if value is not None:
            estimates.append(value)
    estimates.sort()
    if not estimates:
        return float("nan"), float("nan")
    return estimates[int(0.025 * len(estimates))], estimates[int(0.975 * len(estimates)) - 1]


def statistics_section(valid: list[dict], modes: list[str]) -> list[str]:
    tasks = sorted({r["task"] for r in valid})
    variants = [m for m in modes if m != "baseline"]
    groups = {
        t: {m: [r for r in valid if r["task"] == t and r["mode"] == m] for m in modes}
        for t in tasks
    }
    if "baseline" not in modes or any(len(g[m]) < 3 for g in groups.values() for m in modes):
        return []
    out = ["", "## Statistics (5+ repeats)", "",
           "Per task: median [interquartile range] of input tokens.", ""]
    header = "| task | " + " | ".join(modes) + " |"
    out += [header, "|" + "---|" * (len(modes) + 1)]
    for t in tasks:
        cells = []
        for m in modes:
            vals = [r.get("input_tokens_total") for r in groups[t][m]]
            q1, q3 = quartiles(vals)
            cells.append(f"{median(vals) / 1000:.0f}k [{q1 / 1000:.0f}–{q3 / 1000:.0f}k]")
        out.append(f"| {t} | " + " | ".join(cells) + " |")
    for variant in variants:
        out += ["", f"**{variant} vs baseline**: geometric mean over tasks of the median ratio, with a bootstrap "
                    "95% interval (an interval that excludes 1.00 is a real effect at this sample size).", "",
                "| metric | ratio | 95% interval |", "|---|---|---|"]
        for key, label in METRICS:
            if not any(r.get(key) for r in valid):
                continue
            point = geo_mean_ratio(groups, key, variant)
            lo, hi = bootstrap_ci(groups, key, variant)
            out.append(f"| {label} | {point:.2f} | {lo:.2f} – {hi:.2f} |")
    return out


def summarise(rows: list[dict]) -> str:
    valid = [r for r in rows if not r.get("harness_error") and not r.get("dry_run") and not r.get("infra_error")]
    excluded = [r for r in rows if r.get("harness_error") or r.get("infra_error")]
    flagged = [r for r in valid if r.get("outside_access")]
    tasks = sorted({r["task"] for r in valid})
    modes = [m for m in ("baseline", "map", "map-context") if any(r["mode"] == m for r in valid)]

    out: list[str] = ["# Method Project Map benchmark", ""]
    out.append(f"Runs: {len(valid)} valid, {len(excluded)} excluded (harness or provider errors), "
               f"{len(flagged)} flagged for looking outside the workspace (kept, see below).")
    out.append("Values are medians over repeats. Success = hidden tests and regression suite pass.")
    out.append("")

    header = "| task | mode | n | success | " + " | ".join(label for _, label in METRICS) + " | pmap calls |"
    out += [header, "|" + "---|" * (header.count("|") - 1)]
    for task in tasks:
        for mode in modes:
            group = [r for r in valid if r["task"] == task and r["mode"] == mode]
            if not group:
                continue
            ok = sum(1 for r in group if r.get("success"))
            cells = [fmt(median([r.get(k) for r in group]), k) for k, _ in METRICS]
            pmap = fmt(median([r.get("pmap_calls") for r in group]), "pmap")
            out.append(f"| {task} | {mode} | {len(group)} | {ok}/{len(group)} | " + " | ".join(cells) + f" | {pmap} |")

    for variant in [m for m in modes if m != "baseline" and "baseline" in modes]:
        out += ["", f"## {variant} vs baseline (ratio of medians; < 1 means the {variant} run used less)", ""]
        header = "| task | " + " | ".join(label for _, label in METRICS) + " | success base → map |"
        out += [header, "|" + "---|" * (header.count("|") - 1)]
        log_ratios: dict[str, list[float]] = {k: [] for k, _ in METRICS}
        for task in tasks:
            base = [r for r in valid if r["task"] == task and r["mode"] == "baseline"]
            mapped = [r for r in valid if r["task"] == task and r["mode"] == variant]
            if not base or not mapped:
                continue
            cells = []
            for key, _ in METRICS:
                b, m = median([r.get(key) for r in base]), median([r.get(key) for r in mapped])
                if b and m:
                    cells.append(f"{m / b:.2f}")
                    log_ratios[key].append(math.log(m / b))
                else:
                    cells.append("–")
            sb = sum(1 for r in base if r.get("success"))
            sm = sum(1 for r in mapped if r.get("success"))
            out.append(f"| {task} | " + " | ".join(cells) + f" | {sb}/{len(base)} → {sm}/{len(mapped)} |")
        geo = [f"{math.exp(sum(v) / len(v)):.2f}" if v else "–" for v in log_ratios.values()]
        out.append("| **geometric mean** | " + " | ".join(geo) + " | |")

    out += statistics_section(valid, modes)

    if flagged:
        out += ["", "## Runs flagged for outside access", ""]
        for r in flagged:
            out.append(f"- {r['task']} / {r['mode']} #{r['repeat']}: {r['outside_access'][0][:160]}")
    failed = [r for r in valid if not r.get("success")]
    if failed:
        out += ["", "## Failed runs", ""]
        for r in failed:
            why = "timeout" if r.get("timed_out") else (
                "hidden tests" if not (r.get("hidden") or {}).get("passed") else "regression suite")
            out.append(f"- {r['task']} / {r['mode']} #{r['repeat']}: {why}; changed {r.get('changed_files')}")
    if excluded:
        out += ["", "## Excluded runs", ""]
        out += [f"- {r['task']} / {r['mode']} #{r['repeat']}: {r.get('harness_error') or r.get('infra_error')}"
                for r in excluded]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("--markdown", help="Also write the report to this file")
    args = parser.parse_args()
    text = summarise(load(Path(args.run_dir)))
    print(text, end="")
    if args.markdown:
        Path(args.markdown).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
