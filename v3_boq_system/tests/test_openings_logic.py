"""test_openings_logic.py — Tests for opening schedule decomposition."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import yaml
from v3_boq_system.normalize.element_model import OpeningElement, ProjectElementModel
from v3_boq_system.quantify.opening_quantifier import quantify_openings
from v3_boq_system.assemblies.assembly_engine import apply_all_opening_assemblies

_CFG = {
    "openings": {"default_door_height_m": 2.04,
                 "door_block_width_map": {"DOOR_90": 0.9, "DOOR_82": 0.82, "DOOR_72": 0.72}},
    "finishes": {"architrave_door_lm_each": 6.0, "architrave_window_lm_each": 4.8},
}

def _load_rules():
    p = Path(__file__).parent.parent / "config" / "assembly_rules.yaml"
    if p.exists():
        with open(p) as f:
            return yaml.safe_load(f) or {}
    return {}


def _model_with_openings(doors=None, windows=None) -> ProjectElementModel:
    m = ProjectElementModel()
    for d in (doors or []):
        m.openings.append(OpeningElement(**d))
    for w in (windows or []):
        m.openings.append(OpeningElement(**w))
    return m


class TestOpeningsDecomposition:

    def test_single_door_produces_hardware(self):
        """One hinged door must produce lockset, hinges, door stop."""
        rules = _load_rules()
        m = _model_with_openings(doors=[{
            "element_id": "d1", "opening_type": "door", "mark": "DOOR_90",
            "width_m": 0.9, "quantity": 1, "swing_type": "hinged",
            "source": "dxf_blocks", "confidence": "HIGH",
        }])
        rows = quantify_openings(m, _CFG, rules)
        names = [r["item_name"] for r in rows]
        assert any("Lockset" in n or "lockset" in n for n in names)
        assert any("Hinge" in n or "hinge" in n for n in names)
        assert any("Stop" in n or "stop" in n for n in names)

    def test_louvre_window_produces_flyscreen(self):
        """Louvre window must produce a flyscreen row."""
        rules = _load_rules()
        m = _model_with_openings(windows=[{
            "element_id": "w1", "opening_type": "window", "mark": "WINDOW_LOUVRE",
            "quantity": 11, "swing_type": "louvre",
            "source": "dxf_blocks", "confidence": "HIGH",
        }])
        rows = quantify_openings(m, _CFG, rules)
        names = [r["item_name"] for r in rows]
        assert any("Flyscreen" in n or "flyscreen" in n for n in names)

    def test_door_count_multiplied_correctly(self):
        """Hardware quantities must equal door count × per-door qty."""
        rules = _load_rules()
        m = _model_with_openings(doors=[{
            "element_id": "d1", "opening_type": "door", "mark": "DOOR_90",
            "width_m": 0.9, "quantity": 6, "swing_type": "hinged",
            "source": "dxf_blocks", "confidence": "HIGH",
        }])
        rows = quantify_openings(m, _CFG, rules)
        locksets = [r for r in rows if "Lockset" in r["item_name"]]
        if locksets:
            assert locksets[0]["quantity"] == 6

    def test_sliding_door_uses_track_gear(self):
        """Sliding door should produce sliding track/gear, not hinge set."""
        rules = _load_rules()
        ops = [{"opening_type": "door", "swing_type": "sliding", "quantity": 1,
                "mark": "DOOR_SLD", "source": "dxf_blocks", "confidence": "HIGH"}]
        assembly_rows = apply_all_opening_assemblies(ops, rules)
        names = [r["item_name"] for r in assembly_rows]
        assert any("Track" in n or "track" in n or "Gear" in n for n in names), (
            f"Sliding door should have track/gear. Got: {names}"
        )

    def test_architrave_derived_from_count(self):
        """Architrave lm must be count × per-unit constant."""
        rules = _load_rules()
        m = _model_with_openings(doors=[{
            "element_id": "d1", "opening_type": "door", "mark": "DOOR_90",
            "width_m": 0.9, "quantity": 6, "swing_type": "hinged",
            "source": "dxf_blocks", "confidence": "HIGH",
        }])
        rows = quantify_openings(m, _CFG, rules)
        arch_rows = [r for r in rows if "Architrave" in r["item_name"] and "oor" in r["item_name"]]
        if arch_rows:
            assert arch_rows[0]["quantity"] == pytest.approx(6 * 6.0, 0.01)

    def test_all_opening_rows_have_traceability(self):
        rules = _load_rules()
        m = _model_with_openings(
            doors=[{"element_id":"d1","opening_type":"door","mark":"DOOR_90",
                    "quantity":3,"swing_type":"hinged","source":"dxf_blocks","confidence":"HIGH"}],
            windows=[{"element_id":"w1","opening_type":"window","mark":"WINDOW_LOUVRE",
                      "quantity":5,"swing_type":"louvre","source":"dxf_blocks","confidence":"HIGH"}],
        )
        rows = quantify_openings(m, _CFG, rules)
        for row in rows:
            assert row.get("source_evidence"), f"Missing source_evidence: {row['item_name']}"
            assert row.get("quantity_basis"),   f"Missing quantity_basis: {row['item_name']}"
