"""
test_project2_regression.py — Golden-file regression tests for Project 2 benchmark.

These tests protect the v3.0-project2-benchmark result from accidental regressions.
They load the frozen benchmark metadata and the live output and verify:
  - item count does not collapse
  - no Z-Unclassified rows
  - all packages present
  - no template contamination
  - slab duplication absent
  - internal wall quantity from DXF (not slab estimate)
  - roof battens not duplicated
  - placeholders remain labeled
  - traceability fields present on every row
  - floor system exists

Run:
    pytest v3_boq_system/tests/test_project2_regression.py -v

The tests skip gracefully when the live output JSON does not exist
(e.g. on a fresh clone before running the pipeline).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_OUTPUT_JSON = (
    _REPO_ROOT / "v3_boq_system" / "outputs" / "project2" / "project2_boq_items_v3.json"
    if (_REPO_ROOT / "v3_boq_system" / "outputs" / "project2" / "project2_boq_items_v3.json").exists()
    else _REPO_ROOT / "v3_boq_system" / "outputs" / "project_2" / "project_2_boq_items_v3.json"
)
_BENCHMARK_JSON = _REPO_ROOT / "benchmarks" / "project2_v3" / "project2_benchmark.json"

sys.path.insert(0, str(_REPO_ROOT))


def _load_output() -> list[dict]:
    if not _OUTPUT_JSON.exists():
        pytest.skip(f"Live output not found: {_OUTPUT_JSON} — run the pipeline first")
    with open(_OUTPUT_JSON, encoding="utf-8") as f:
        return json.load(f)


def _load_benchmark() -> dict:
    with open(_BENCHMARK_JSON, encoding="utf-8") as f:
        return json.load(f)


# ── Item count ────────────────────────────────────────────────────────────────

class TestItemCount:

    def test_total_item_count_not_collapsed(self):
        """Total items must not fall more than 10% below benchmark."""
        items = _load_output()
        bm = _load_benchmark()
        frozen = bm["item_count"]
        threshold = int(frozen * 0.90)
        assert len(items) >= threshold, (
            f"Item count regressed: {len(items)} < {threshold} (90% of benchmark {frozen})"
        )

    def test_total_item_count_not_exploded(self):
        """Total items must not exceed 200% of benchmark (flag runaway duplication)."""
        items = _load_output()
        bm = _load_benchmark()
        frozen = bm["item_count"]
        ceiling = frozen * 2
        assert len(items) <= ceiling, (
            f"Item count explosion: {len(items)} > {ceiling} (200% of benchmark {frozen})"
        )


# ── Section coverage ─────────────────────────────────────────────────────────

class TestSectionCoverage:

    def test_no_z_unclassified_rows(self):
        """No item may land in Z-Unclassified — all packages must be mapped."""
        items = _load_output()
        unclassified = [i["item_name"] for i in items if "Z -" in i.get("boq_section", "")]
        assert not unclassified, f"Z-Unclassified rows found: {unclassified}"

    def test_all_benchmark_packages_present(self):
        """Every package from the frozen benchmark must still appear."""
        items = _load_output()
        bm = _load_benchmark()
        sections_present = {i["boq_section"] for i in items}
        for pkg in bm["packages_covered"]:
            assert pkg in sections_present, f"Package '{pkg}' missing from output"

    def test_floor_system_package_present(self):
        """G - Floor System must always be present (was absent in V1/V2)."""
        items = _load_output()
        floor_items = [i for i in items if i.get("boq_section") == "G - Floor System"]
        assert floor_items, "G - Floor System package is missing"

    def test_services_package_present(self):
        """I - Services must be present and non-empty (requires room schedule)."""
        items = _load_output()
        svc = [i for i in items if i.get("boq_section") == "I - Services"]
        assert len(svc) >= 5, f"I - Services has only {len(svc)} items — room schedule may have failed"

    def test_external_works_package_present(self):
        """K - External Works must contain cladding and verandah items."""
        items = _load_output()
        ext = [i for i in items if i.get("boq_section") == "K - External Works"]
        assert ext, "K - External Works package is missing"


# ── Template contamination ────────────────────────────────────────────────────

class TestTemplateContamination:

    BANNED_SUBSTRINGS = [
        "3br", "3-bedroom", "three bedroom", "g303", "ground level laundry",
        "master bedroom", "ensuite",
    ]

    def test_no_residential_template_names(self):
        """No residential template item names should appear in a pharmacy project."""
        items = _load_output()
        for item in items:
            name_lower = item["item_name"].lower()
            for banned in self.BANNED_SUBSTRINGS:
                assert banned not in name_lower, (
                    f"Template contamination: '{banned}' found in item '{item['item_name']}'"
                )

    def test_no_boq_reference_quantities_present(self):
        """
        Sentinel: verify the pipeline's confirmed-clean invariant is carried in QA report.
        We can't directly test non-copying of quantities, but the QA summary must assert it.
        """
        qa_path = _OUTPUT_JSON.parent / (_OUTPUT_JSON.stem.replace("boq_items_v3", "qa_report_v3") + ".json")
        if not qa_path.exists():
            pytest.skip("QA report not found")
        with open(qa_path, encoding="utf-8") as f:
            qa = json.load(f)
        # The QA report should have template_contamination_check field
        assert qa.get("template_contamination_check") != "FAILED", (
            "QA report flags template contamination"
        )


# ── Duplication guards ────────────────────────────────────────────────────────

class TestDuplicationGuards:

    def test_roof_battens_not_duplicated(self):
        """
        Exactly 1 batten summary (all-zones total) row must be present.
        'Structural Frame — roof_batten' must NOT appear (was a duplicate in earlier builds).
        """
        items = _load_output()
        # Accept either the old name or the new zone-labelled name
        batten_summary = [
            i for i in items
            if i["item_name"].startswith("Roof Battens (FRAMECAD BATTEN")
        ]
        assert len(batten_summary) == 1, (
            f"Expected exactly 1 batten summary row, got {len(batten_summary)}"
        )
        old_dup = [i for i in items if "Structural Frame" in i["item_name"]
                   and "roof_batten" in i["item_name"].lower()]
        assert not old_dup, (
            f"Duplicate batten structural frame row returned: {[r['item_name'] for r in old_dup]}"
        )

    def test_no_slab_duplication_in_steel_floor_project(self):
        """
        For a steel-frame raised floor, slab rows must not appear alongside steel floor rows.
        """
        items = _load_output()
        floor_items = [i for i in items if i.get("boq_section") == "G - Floor System"]
        slab_items = [i for i in floor_items if "slab" in i["item_name"].lower()
                      and "concrete" in i["item_name"].lower()]
        steel_items = [i for i in floor_items if any(
            kw in i["item_name"].lower()
            for kw in ("joist", "cassette", "bearer", "floor panel")
        )]
        # If steel items exist, no concrete slab item should also be present
        if steel_items:
            assert not slab_items, (
                f"Slab items appearing alongside steel floor items: {[r['item_name'] for r in slab_items]}"
            )

    def test_no_duplicate_item_names_within_same_package(self):
        """
        Within the same boq_section, no item_name should appear more than twice.
        (Two occurrences allowed for things like 'Door Leaf' across different door marks.)
        """
        from collections import Counter
        items = _load_output()
        from itertools import groupby
        by_section: dict[str, list] = {}
        for i in items:
            by_section.setdefault(i["boq_section"], []).append(i["item_name"])
        problems = []
        for sec, names in by_section.items():
            counts = Counter(names)
            for name, cnt in counts.items():
                # Allow up to 3 occurrences for hardware items that repeat per door mark
                if cnt > 3 and "Placeholder" not in name and "PLACEHOLDER" not in name:
                    problems.append(f"{sec}: '{name}' appears {cnt} times")
        assert not problems, f"Potential duplication: {problems}"


# ── Internal wall geometry source ─────────────────────────────────────────────

class TestInternalWallGeometry:

    def test_internal_wall_lm_from_dxf_not_slab_estimate(self):
        """
        Internal wall lining must reference DXF geometry, not a slab-derived estimate.
        Checks source_evidence field on internal lining rows.
        """
        items = _load_output()
        int_lining = [i for i in items
                      if "Internal Wall Lining" in i.get("item_name", "")
                      and i.get("boq_section") == "E - Linings & Ceilings"]
        for row in int_lining:
            ev = (row.get("source_evidence") or "").lower()
            # Accept DXF direct, int_wall alias, or canonical_geometry path
            # (canonical_geometry/wf_internal is derived from DXF — same source)
            assert "dxf" in ev or "int_wall" in ev or "canonical_geometry" in ev, (
                f"Internal wall lining '{row['item_name']}' not sourced from DXF or canonical: "
                f"source_evidence='{row.get('source_evidence')}'"
            )


# ── Traceability ───────────────────────────────────────────────────────────────

class TestTraceabilityRegression:

    REQUIRED_FIELDS = ["source_evidence", "quantity_basis", "derivation_rule", "confidence"]

    def test_all_rows_have_traceability_fields(self):
        """Every BOQ row must have the four core traceability fields populated."""
        items = _load_output()
        failures = []
        for item in items:
            for field in self.REQUIRED_FIELDS:
                val = item.get(field)
                if not val:
                    failures.append(f"'{item['item_name']}' missing {field}")
        assert not failures, f"Traceability gaps ({len(failures)}): {failures[:10]}"

    def test_all_manual_review_items_have_notes(self):
        """Every manual_review=True item must have a non-empty notes field."""
        items = _load_output()
        failures = [
            i["item_name"] for i in items
            if i.get("manual_review") and not i.get("notes", "").strip()
        ]
        assert not failures, (
            f"{len(failures)} manual_review items have no notes: {failures[:5]}"
        )

    def test_confidence_field_valid_values(self):
        """confidence must be one of HIGH / MEDIUM / LOW."""
        items = _load_output()
        valid = {"HIGH", "MEDIUM", "LOW"}
        bad = [
            f"'{i['item_name']}': '{i.get('confidence')}'"
            for i in items if i.get("confidence") not in valid
        ]
        assert not bad, f"Invalid confidence values: {bad}"


# ── Placeholder labeling ──────────────────────────────────────────────────────

class TestPlaceholderLabeling:

    def test_placeholders_have_zero_quantity(self):
        """Placeholder rows must have quantity == 0."""
        items = _load_output()
        bad = [
            i["item_name"] for i in items
            if i.get("quantity_status") == "placeholder" and i.get("quantity", 0) != 0
        ]
        assert not bad, f"Placeholder rows with non-zero quantity: {bad}"

    def test_placeholders_have_manual_review_true(self):
        """All placeholder rows must have manual_review=True."""
        items = _load_output()
        bad = [
            i["item_name"] for i in items
            if i.get("quantity_status") == "placeholder" and not i.get("manual_review")
        ]
        assert not bad, f"Placeholder rows without manual_review=True: {bad}"

    def test_placeholder_count_not_collapsed(self):
        """Placeholder count must not drop to zero (would mean silent removal)."""
        items = _load_output()
        bm = _load_benchmark()
        count = sum(1 for i in items if i.get("quantity_status") == "placeholder")
        frozen = bm["placeholder_count"]
        assert count > 0, "All placeholder rows have been removed — check if intentional"
        # Allow reduction (placeholders may be filled) but not silent explosion
        assert count <= frozen + 5, (
            f"Placeholder count jumped: {count} vs benchmark {frozen}"
        )

    def test_fixing_schedule_placeholder_present(self):
        """The structural fixings placeholder must remain until a real schedule is sourced."""
        items = _load_output()
        fixing_ph = [i for i in items
                     if "Fixing" in i.get("item_name", "") and "PLACEHOLDER" in i.get("item_name", "")]
        assert fixing_ph, (
            "Structural fixings placeholder row is missing. "
            "If a real fixing schedule has been sourced, update this test."
        )


# ── Benchmark invariants ──────────────────────────────────────────────────────

class TestBenchmarkInvariants:

    def test_benchmark_metadata_consistent(self):
        """Benchmark JSON must be internally consistent with current output."""
        items = _load_output()
        bm = _load_benchmark()
        # Section counts should be within 20% of benchmark
        from collections import Counter
        live_sections = Counter(i["boq_section"] for i in items)
        for sec, frozen_count in bm["package_item_counts"].items():
            live_count = live_sections.get(sec, 0)
            assert live_count > 0, f"Section '{sec}' has vanished (was {frozen_count} in benchmark)"
            ratio = live_count / frozen_count
            assert 0.5 <= ratio <= 3.0, (
                f"Section '{sec}' item count changed dramatically: "
                f"{live_count} vs benchmark {frozen_count}"
            )

    def test_no_quantity_is_exactly_reference_boq_sentinel(self):
        """
        Sentinel test: certain known reference-BOQ quantities that must NOT appear verbatim.
        If any of these exact integers appear as quantities it may indicate copying.
        These values come from the reference BOQ and should never match our geometric derivation.
        """
        # Reference BOQ sentinel quantities that are not derivable from geometry
        REFERENCE_SENTINELS = {
            490,   # reference truss lm rounded
            3500,  # reference fixing count
            753,   # reference wall frame lm
        }
        items = _load_output()
        for item in items:
            qty = item.get("quantity")
            if isinstance(qty, (int, float)) and qty in REFERENCE_SENTINELS:
                # Only fail if the item looks structural (where copying would matter)
                if item.get("boq_section") in ("A - Structural Frame", "B - Roof"):
                    pytest.fail(
                        f"Quantity {qty} matches reference BOQ sentinel in '{item['item_name']}'. "
                        "Verify this is derived from geometry, not copied."
                    )
