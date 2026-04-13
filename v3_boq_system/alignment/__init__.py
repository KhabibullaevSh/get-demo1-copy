"""
alignment — Baseline alignment layer for the V3 BOQ pipeline.

Modules
-------
family_classifier   : Maps raw item descriptions to reusable family names.
baseline_profiler   : Reads a reference/estimator BOQ Excel and extracts structure.
ai_profiler         : Profiles the AI-generated BOQ JSON in the same vocabulary.
section_comparator  : Compares baseline vs AI profiles section-by-section.
unit_aligner        : Aligns unit presentation to baseline style where safe.
upgrade_rules       : Reusable export-layer transformation rules (stock-length
                      conversion, section splitting/merging, name normalisation).
scoring             : Commercial alignment scoring per section.
commercial_aligner  : Orchestrator — runs the full alignment pipeline and writes
                      results to a JSON report.

Entry point
-----------
Run from the project root:
    python -m alignment.commercial_aligner \\
        --reference  "../input/project 2_BOQ_20260323.xlsx" \\
        --ai-boq     "outputs/project2/project2_boq_items_v3.json" \\
        --output-dir "outputs/project2"
"""
