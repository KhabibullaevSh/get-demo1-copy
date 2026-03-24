"""test_stair_ramp_logic.py — Tests for stair, ramp, and balustrade quantifier."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from v3_boq_system.normalize.element_model import (
    ProjectElementModel, StairElement, VerandahElement
)
from v3_boq_system.quantify.stair_ramp_quantifier import quantify_stairs

_CFG = {}


def _model_with_stair(
    stair_type="prefab",
    flights=1,
    risers=10,
    tread_depth_mm=250,
    riser_height_mm=175,
    width_m=1.2,
    balustrade_lm=0.0,
    handrail_lm=0.0,
    landing_area_m2=0.0,
    source="pdf_schedule",
    confidence="HIGH",
) -> ProjectElementModel:
    m = ProjectElementModel()
    m.stairs.append(StairElement(
        element_id="ST1",
        stair_type=stair_type,
        flights=flights,
        risers_per_flight=risers,
        tread_depth_mm=tread_depth_mm,
        riser_height_mm=riser_height_mm,
        width_m=width_m,
        balustrade_lm=balustrade_lm,
        handrail_lm=handrail_lm,
        landing_area_m2=landing_area_m2,
        source=source,
        confidence=confidence,
    ))
    return m


class TestStairFlightDecomposition:

    def test_stair_flight_row_emitted(self):
        m = _model_with_stair()
        rows = quantify_stairs(m, _CFG)
        flights = [r for r in rows if "Stringer" in r["item_name"] or "Stair Flight" in r["item_name"]]
        assert flights, "Expected stair flight / stringer row"
        assert flights[0]["quantity"] == 1

    def test_tread_count_equals_flights_times_risers(self):
        """10 risers × 1 flight → 10 treads."""
        m = _model_with_stair(risers=10)
        rows = quantify_stairs(m, _CFG)
        treads = [r for r in rows if "Tread" in r["item_name"]]
        assert treads, "Expected stair tread row"
        assert treads[0]["quantity"] == 10

    def test_newel_posts_two_per_flight(self):
        """1 flight → 2 newel posts (top + bottom)."""
        m = _model_with_stair(flights=1)
        rows = quantify_stairs(m, _CFG)
        newels = [r for r in rows if "Newel" in r["item_name"]]
        assert newels, "Expected Newel Post row"
        assert newels[0]["quantity"] == 2

    def test_newel_posts_four_for_two_flights(self):
        m = _model_with_stair(flights=2)
        rows = quantify_stairs(m, _CFG)
        newels = [r for r in rows if "Newel" in r["item_name"]]
        assert newels
        assert newels[0]["quantity"] == 4

    def test_landing_row_when_area_provided(self):
        m = _model_with_stair(landing_area_m2=3.0)
        rows = quantify_stairs(m, _CFG)
        landing = [r for r in rows if "Landing" in r["item_name"]]
        assert landing, "Expected Landing row"
        assert landing[0]["quantity"] == pytest.approx(3.0, 0.01)


class TestBalustradeDecomposition:

    def test_balustrade_top_rail_from_schedule(self):
        """When balustrade_lm from schedule, top rail = that lm."""
        m = _model_with_stair(balustrade_lm=2.5, risers=10)
        rows = quantify_stairs(m, _CFG)
        rail = [r for r in rows if "Top Rail" in r["item_name"]]
        assert rail, "Expected Balustrade Top Rail row"
        assert rail[0]["quantity"] == pytest.approx(2.5, 0.01)

    def test_balustrade_posts_derived_from_lm(self):
        """Posts = ceil(balustrade_lm / 1.2)."""
        m = _model_with_stair(balustrade_lm=2.5, risers=10)
        rows = quantify_stairs(m, _CFG)
        posts = [r for r in rows if "Balustrade Post" in r["item_name"]]
        assert posts, "Expected Balustrade Post row"
        import math
        assert posts[0]["quantity"] == math.ceil(2.5 / 1.2)

    def test_balustrade_infill_emitted(self):
        """Infill panels row must be present when balustrade_lm > 0."""
        m = _model_with_stair(balustrade_lm=2.5, risers=10)
        rows = quantify_stairs(m, _CFG)
        infill = [r for r in rows if "Infill" in r["item_name"]]
        assert infill, "Expected Balustrade Infill row"

    def test_handrail_equals_balustrade_when_no_separate_lm(self):
        """When handrail_lm=0, handrail falls back to run estimate (same as balustrade)."""
        m = _model_with_stair(risers=10, tread_depth_mm=250, balustrade_lm=0, handrail_lm=0)
        rows = quantify_stairs(m, _CFG)
        handrail = [r for r in rows if "Handrail" in r["item_name"] and "Ramp" not in r["item_name"]]
        assert handrail, "Expected Stair Handrail row"
        # run_est = 10 × 0.250 = 2.5 m
        assert handrail[0]["quantity"] == pytest.approx(2.5, abs=0.1)


class TestRampRows:

    def test_ramp_surface_row_emitted_when_stair_present(self):
        """Access ramp rows must be emitted whenever stair evidence is present."""
        m = _model_with_stair()
        rows = quantify_stairs(m, _CFG)
        ramp = [r for r in rows if "Ramp" in r["item_name"] and "Surface" in r["item_name"]]
        assert ramp, "Expected Access Ramp Surface row"
        assert ramp[0]["quantity"] > 0

    def test_ramp_handrail_emitted(self):
        m = _model_with_stair()
        rows = quantify_stairs(m, _CFG)
        ramp_hr = [r for r in rows if "Ramp" in r["item_name"] and "Handrail" in r["item_name"]]
        assert ramp_hr, "Expected Ramp Handrail row"

    def test_ramp_kerb_emitted(self):
        m = _model_with_stair()
        rows = quantify_stairs(m, _CFG)
        kerb = [r for r in rows if "Ramp" in r["item_name"] and "Kerb" in r["item_name"]]
        assert kerb, "Expected Ramp Edge / Kerb Guard row"

    def test_ramp_manual_review_true(self):
        m = _model_with_stair()
        rows = quantify_stairs(m, _CFG)
        ramp = [r for r in rows if "Ramp" in r["item_name"]]
        for r in ramp:
            assert r["manual_review"] is True, f"Ramp row '{r['item_name']}' must have manual_review=True"


class TestVerandahBalustrade:

    def test_verandah_balustrade_emitted(self):
        m = ProjectElementModel()
        m.verandahs.append(VerandahElement(
            element_id="v1", perimeter_m=12.0, area_m2=9.0,
            source="dxf_geometry", confidence="MEDIUM",
        ))
        rows = quantify_stairs(m, _CFG)
        bal = [r for r in rows if "Verandah Balustrade" in r["item_name"]]
        assert bal, "Expected Verandah Balustrade row"
        assert bal[0]["quantity"] == pytest.approx(12.0, 0.01)
