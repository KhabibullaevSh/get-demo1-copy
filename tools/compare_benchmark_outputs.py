"""
compare_benchmark_outputs.py — Compare a newly generated BOQ output against a frozen benchmark.

Usage:
    python tools/compare_benchmark_outputs.py \\
        --live    v3_boq_system/outputs/project_2/project_2_boq_items_v3.json \\
        --benchmark benchmarks/project2_v3/project2_benchmark.json

Or shorthand (auto-resolves paths for project_2):
    python tools/compare_benchmark_outputs.py --project project_2

Exit codes:
    0 — No regressions detected
    1 — One or more regressions detected (see output)
    2 — Files not found / parse error
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _pct(a: float, b: float) -> str:
    if b == 0:
        return "∞%"
    return f"{(a - b) / b * 100:+.1f}%"


# ── Comparison checks ─────────────────────────────────────────────────────────

def check_item_count(live: list[dict], bm: dict) -> list[str]:
    issues = []
    frozen = bm["item_count"]
    live_n = len(live)
    lo = int(frozen * 0.90)
    hi = frozen * 2
    if live_n < lo:
        issues.append(
            f"REGRESSION  Item count collapsed: {live_n} < {lo} (90% of benchmark {frozen})"
        )
    elif live_n > hi:
        issues.append(
            f"REGRESSION  Item count exploded: {live_n} > {hi} (200% of benchmark {frozen})"
        )
    else:
        print(f"  OK  Item count: {live_n} (benchmark {frozen}, {_pct(live_n, frozen)})")
    return issues


def check_packages(live: list[dict], bm: dict) -> list[str]:
    issues = []
    live_sections = {i["boq_section"] for i in live}
    for pkg in bm["packages_covered"]:
        if pkg not in live_sections:
            issues.append(f"REGRESSION  Package missing: '{pkg}'")
        else:
            print(f"  OK  Package present: {pkg}")
    # Z-Unclassified
    z_rows = [i["item_name"] for i in live if "Z -" in i.get("boq_section", "")]
    if z_rows:
        issues.append(f"REGRESSION  Z-Unclassified rows ({len(z_rows)}): {z_rows[:3]}")
    else:
        print(f"  OK  No Z-Unclassified rows")
    return issues


def check_package_item_counts(live: list[dict], bm: dict) -> list[str]:
    issues = []
    live_counts = Counter(i["boq_section"] for i in live)
    for sec, frozen_count in bm["package_item_counts"].items():
        live_count = live_counts.get(sec, 0)
        if live_count == 0:
            issues.append(f"REGRESSION  Section vanished: '{sec}' (was {frozen_count})")
        else:
            ratio = live_count / frozen_count
            tag = "OK " if 0.5 <= ratio <= 3.0 else "WARN"
            sym = "  " if tag == "OK " else "!"
            print(
                f"  {tag}{sym} {sec}: {live_count} items "
                f"(benchmark {frozen_count}, {_pct(live_count, frozen_count)})"
            )
            if tag != "OK ":
                issues.append(
                    f"WARNING     {sec} item count changed dramatically: "
                    f"{live_count} vs benchmark {frozen_count}"
                )
    return issues


def check_placeholders(live: list[dict], bm: dict) -> list[str]:
    issues = []
    frozen = bm["placeholder_count"]
    live_phs = [i for i in live if i.get("quantity_status") == "placeholder"]
    count = len(live_phs)
    if count == 0:
        issues.append("REGRESSION  All placeholder rows removed — verify intentional")
    elif count > frozen + 5:
        issues.append(
            f"WARNING     Placeholder count jumped: {count} vs benchmark {frozen}"
        )
    else:
        print(f"  OK  Placeholder count: {count} (benchmark {frozen})")

    # Non-zero placeholder quantities
    bad_qty = [i["item_name"] for i in live_phs if i.get("quantity", 0) != 0]
    if bad_qty:
        issues.append(f"REGRESSION  Placeholder rows have non-zero qty: {bad_qty}")
    else:
        print(f"  OK  All placeholder rows have qty=0")

    # Named placeholders
    live_names = {i["item_name"] for i in live_phs}
    for ph_name in bm.get("placeholder_items", []):
        if ph_name not in live_names:
            issues.append(f"WARNING     Expected placeholder not found: '{ph_name}'")
    return issues


def check_traceability(live: list[dict]) -> list[str]:
    issues = []
    REQUIRED = ["source_evidence", "quantity_basis", "derivation_rule", "confidence"]
    gaps = []
    for item in live:
        for field in REQUIRED:
            if not item.get(field):
                gaps.append(f"'{item['item_name']}' missing {field}")
    if gaps:
        issues.append(
            f"REGRESSION  Traceability gaps ({len(gaps)}): {gaps[:5]}"
        )
    else:
        print(f"  OK  All rows have traceability fields")

    # Confidence values
    valid = {"HIGH", "MEDIUM", "LOW"}
    bad_conf = [
        f"'{i['item_name']}': '{i.get('confidence')}'"
        for i in live if i.get("confidence") not in valid
    ]
    if bad_conf:
        issues.append(f"REGRESSION  Invalid confidence values: {bad_conf[:5]}")
    else:
        print(f"  OK  All confidence values valid")
    return issues


def check_confidence_distribution(live: list[dict], bm: dict) -> list[str]:
    issues = []
    frozen = bm["confidence_breakdown"]
    live_dist = Counter(i.get("confidence") for i in live)
    for level in ("HIGH", "MEDIUM", "LOW"):
        fl = frozen.get(level, 0)
        ll = live_dist.get(level, 0)
        change = _pct(ll, fl) if fl else "n/a"
        tag = "  OK " if fl == 0 or abs(ll - fl) / fl <= 0.25 else "  WARN"
        print(f"  {tag}  Confidence {level}: {ll} (benchmark {fl}, {change})")
        if fl > 0 and ll < fl * 0.5:
            issues.append(
                f"WARNING     Confidence {level} count dropped sharply: {ll} vs {fl}"
            )
    return issues


def check_duplication(live: list[dict]) -> list[str]:
    issues = []
    by_section: dict[str, list] = {}
    for item in live:
        by_section.setdefault(item["boq_section"], []).append(item["item_name"])
    problems = []
    for sec, names in by_section.items():
        counts = Counter(names)
        for name, cnt in counts.items():
            if cnt > 3 and "Placeholder" not in name and "PLACEHOLDER" not in name:
                problems.append(f"{sec}: '{name}' ×{cnt}")
    if problems:
        issues.append(f"WARNING     Potential duplication: {problems}")
    else:
        print(f"  OK  No excessive duplication within sections")

    # Batten-specific check
    batten_rows = [i for i in live if i["item_name"] == "Roof Battens (FRAMECAD BATTEN)"]
    if len(batten_rows) > 1:
        issues.append(
            f"REGRESSION  Roof Battens row duplicated: {len(batten_rows)} rows"
        )
    elif len(batten_rows) == 1:
        print(f"  OK  Roof Battens row present exactly once")
    return issues


def check_invariants(live: list[dict], bm: dict) -> list[str]:
    issues = []
    inv = bm.get("invariants", {})
    # No template contamination (keyword check)
    BANNED = ["3br", "3-bedroom", "three bedroom", "g303", "master bedroom", "ensuite"]
    found = []
    for item in live:
        name_low = item["item_name"].lower()
        for kw in BANNED:
            if kw in name_low:
                found.append(f"'{item['item_name']}' contains '{kw}'")
    if found:
        issues.append(f"REGRESSION  Template contamination: {found}")
    else:
        print(f"  OK  No template contamination")

    # Reference BOQ sentinel quantities
    SENTINELS = {490, 3500, 753}
    for item in live:
        qty = item.get("quantity")
        if isinstance(qty, (int, float)) and qty in SENTINELS:
            if item.get("boq_section") in ("A - Structural Frame", "B - Roof"):
                issues.append(
                    f"WARNING     Quantity {qty} matches reference BOQ sentinel in '{item['item_name']}'"
                )
    return issues


# ── Main ──────────────────────────────────────────────────────────────────────

def compare(live_path: Path, benchmark_path: Path) -> int:
    print(f"\nBenchmark comparison")
    print(f"  Live output : {live_path}")
    print(f"  Benchmark   : {benchmark_path}")
    print()

    if not live_path.exists():
        print(f"ERROR  Live output not found: {live_path}", file=sys.stderr)
        return 2
    if not benchmark_path.exists():
        print(f"ERROR  Benchmark not found: {benchmark_path}", file=sys.stderr)
        return 2

    live: list[dict] = _load_json(live_path)
    bm:   dict       = _load_json(benchmark_path)

    all_issues: list[str] = []

    print("── Item count ──────────────────────────────────────────────────────")
    all_issues += check_item_count(live, bm)

    print("\n── Package coverage ────────────────────────────────────────────────")
    all_issues += check_packages(live, bm)

    print("\n── Package item counts ─────────────────────────────────────────────")
    all_issues += check_package_item_counts(live, bm)

    print("\n── Placeholders ────────────────────────────────────────────────────")
    all_issues += check_placeholders(live, bm)

    print("\n── Traceability ────────────────────────────────────────────────────")
    all_issues += check_traceability(live)

    print("\n── Confidence distribution ─────────────────────────────────────────")
    all_issues += check_confidence_distribution(live, bm)

    print("\n── Duplication ─────────────────────────────────────────────────────")
    all_issues += check_duplication(live)

    print("\n── Invariants ──────────────────────────────────────────────────────")
    all_issues += check_invariants(live, bm)

    print()
    regressions = [i for i in all_issues if i.startswith("REGRESSION")]
    warnings    = [i for i in all_issues if i.startswith("WARNING")]

    if regressions:
        print(f"FAILED  {len(regressions)} regression(s), {len(warnings)} warning(s)")
        print()
        for r in regressions:
            print(f"  {r}")
        for w in warnings:
            print(f"  {w}")
        return 1
    elif warnings:
        print(f"PASSED with {len(warnings)} warning(s)")
        for w in warnings:
            print(f"  {w}")
        return 0
    else:
        print(f"PASSED  No regressions, no warnings.")
        return 0


def _resolve_paths(project: str) -> tuple[Path, Path]:
    """Resolve standard paths for a named project."""
    slug = project.replace(" ", "_").lower()
    live = _REPO_ROOT / "v3_boq_system" / "outputs" / slug / f"{slug}_boq_items_v3.json"
    bm   = _REPO_ROOT / "benchmarks" / f"{slug}_v3" / f"{slug}_benchmark.json"
    return live, bm


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare BOQ output against frozen benchmark")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project", help="Project slug (e.g. project_2) — auto-resolves paths")
    group.add_argument("--live",    help="Path to live BOQ items JSON")
    parser.add_argument("--benchmark", help="Path to benchmark JSON (required if --live is used)")
    args = parser.parse_args()

    if args.project:
        live_path, bm_path = _resolve_paths(args.project)
    else:
        if not args.benchmark:
            parser.error("--benchmark is required when using --live")
        live_path = Path(args.live)
        bm_path   = Path(args.benchmark)

    sys.exit(compare(live_path, bm_path))


if __name__ == "__main__":
    main()
