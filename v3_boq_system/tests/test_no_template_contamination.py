"""
test_no_template_contamination.py

CRITICAL: Verifies that no BOQ quantities are sourced from reference/template files.
This is the most important invariant in the system.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from v3_boq_system.mapping.boq_mapper import map_to_boq
from v3_boq_system.qa.qa_engine import run_qa


FORBIDDEN_SOURCE_KEYWORDS = [
    "boq_template",
    "benchmark",
    "approved_boq",
    "reference_boq",
    "template_quantity",
    "copied_from",
]


def _make_clean_row(name="Test Item", status="calculated", evidence="dxf_geometry: area=10.0"):
    return {
        "item_name": name, "item_code": "", "unit": "m2", "quantity": 10.0,
        "package": "roof_cladding",
        "quantity_status": status,
        "quantity_basis": "derived from geometry",
        "source_evidence": evidence,
        "derivation_rule": "area × 1.0",
        "confidence": "HIGH",
        "manual_review": False,
        "notes": "",
    }


class TestNoTemplateContamination:

    def test_clean_rows_pass_contamination_check(self):
        rows = [_make_clean_row(), _make_clean_row("Item 2", evidence="framecad_bom: 100 lm")]
        boq_items = map_to_boq(rows, {})
        for item in boq_items:
            evidence = item.get("source_evidence", "").lower()
            for kw in FORBIDDEN_SOURCE_KEYWORDS:
                assert kw not in evidence, (
                    f"Contamination detected: item '{item['item_name']}' "
                    f"has forbidden keyword '{kw}' in source_evidence"
                )

    def test_qa_engine_detects_contamination(self):
        contaminated_row = _make_clean_row(
            name="Contaminated Item",
            evidence="boq_template: row 42",
        )
        boq_items = map_to_boq([contaminated_row], {})
        # Manually inject contaminated evidence
        boq_items[0]["source_evidence"] = "boq_template: row 42"
        qa = run_qa(boq_items, {}, None, {"project": {"type": "pharmacy"}})
        warnings = " ".join(qa.get("warnings", []))
        assert "TEMPLATE CONTAMINATION" in warnings.upper(), (
            "QA engine should detect template contamination"
        )

    def test_benchmark_items_never_become_quantities(self):
        """Benchmark comparison must not transfer quantities to output."""
        benchmark = [
            {"description": "Roof Cladding", "quantity": 999.9, "unit": "m2",
             "section": "ROOF CLADDING"},
        ]
        rows = [_make_clean_row("Roof Cladding — CGI", status="measured")]
        boq_items = map_to_boq(rows, {})
        qa = run_qa(boq_items, {}, benchmark, {"project": {"type": "pharmacy"}})

        # Verify no item has the benchmark quantity
        for item in boq_items:
            assert item.get("quantity") != 999.9, (
                "Benchmark quantity 999.9 must never appear in output"
            )

    def test_all_items_have_non_template_evidence(self):
        """Every BOQ item must have source_evidence that does not reference BOQ templates."""
        rows = [
            _make_clean_row("Floor Finish", evidence="dxf_geometry: floor_area=86.4"),
            _make_clean_row("Wall Lining", evidence="ifc_model: wall_stud_lm=722"),
            _make_clean_row("Roof Sheet", evidence="framecad_bom: roof_panel=481.74"),
        ]
        boq_items = map_to_boq(rows, {})
        for item in boq_items:
            ev = item.get("source_evidence", "")
            assert ev, f"Item '{item['item_name']}' has empty source_evidence"
            for kw in FORBIDDEN_SOURCE_KEYWORDS:
                assert kw not in ev.lower(), (
                    f"Item '{item['item_name']}' source_evidence contains forbidden keyword '{kw}'"
                )
