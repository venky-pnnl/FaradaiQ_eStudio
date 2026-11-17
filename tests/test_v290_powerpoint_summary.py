from __future__ import annotations

from pathlib import Path

from pptx import Presentation

from faradaiq.v290_powerpoint_summary import (
    PPTX_NAME,
    PPT_MANIFEST_NAME,
    FRONT_SLIDE_FOOTER,
    TECHNIQUE_SCENARIOS,
    write_powerpoint_summary,
)


def _dataset(did: str, technique: str, point: int, x: str, y: str, view_id: str) -> dict:
    rows = [[0.0, 1.0 + point], [1.0, 2.0 + point], [2.0, 1.5 + point]]
    return {
        "id": did,
        "display_name": f"{technique} point {point}",
        "technique_key": technique,
        "technique_label": technique,
        "family": "Test family",
        "data_kind": "analysis" if technique in {"DRT", "EAL", "LSB", "AFL"} else "raw",
        "ui_role": "analysis" if technique in {"DRT", "EAL", "LSB", "AFL"} else "measurement",
        "method_label": "Method 01",
        "point": point,
        "cycle": 1,
        "sweep": 1,
        "columns": [x, y],
        "preview": {"status": "plot_ready", "rows": rows, "columns": [x, y]},
        "plot_views": [{
            "id": view_id,
            "label": f"{x} vs {y}",
            "x_index": 0,
            "y_index": 1,
            "x_column": x,
            "y_column": y,
            "x_transform": "identity",
            "y_transform": "identity",
            "x_scale": "linear",
            "y_scale": "linear",
            "plot_type": "curve",
        }],
        "is_media": False,
    }


def test_consolidated_powerpoint_covers_detected_scientific_techniques_and_acronyms(tmp_path: Path) -> None:
    datasets = []
    specs = [
        ("CV", "potential_V", "current_A", "potential_current"),
        ("EIS", "Zreal_ohm", "neg_Zimag_ohm", "nyquist"),
        ("DRT", "tau_s", "gamma_ohm", "drt_tau"),
        ("EAL", "point_index", "eal_metric", "metric_eal"),
        ("LSB", "point_index", "lsb_metric", "metric_lsb"),
        ("AFL", "point_index", "afl_metric", "metric_afl"),
    ]
    matrix = []
    techniques = []
    for technique, x, y, view_id in specs:
        ids = []
        for point in (1, 2, 3):
            did = f"{technique}_{point}"
            datasets.append(_dataset(did, technique, point, x, y, view_id))
            ids.append(did)
        matrix.append({
            "technique_key": technique,
            "technique_label": technique,
            "family": "Test family",
            "view_id": view_id,
            "view_label": f"{x} vs {y}",
            "plot_type": "curve",
            "x_column": x,
            "y_column": y,
            "dataset_ids": ids,
            "dataset_count": 3,
            "unique_points": 3,
            "point_min": 1,
            "point_max": 3,
            "methods": ["Method 01"],
            "description": "Technique-aware comparison.",
            "score": 100,
        })
        techniques.append({
            "technique_key": technique,
            "technique_label": technique,
            "family": "Test family",
            "dataset_count": 3,
            "point_count": 3,
            "method_count": 1,
            "analysis_count": 3 if technique in {"DRT", "EAL", "LSB", "AFL"} else 0,
        })
    manifest = {
        "schema": "faradaiq-portable-browser-v290",
        "version": "v290",
        "experiment_name": "Technique aware QA",
        "datasets": datasets,
        "comparison": {"matrix": matrix, "recommended": matrix[:4]},
        "discovery": {
            "point_count": 3,
            "measurement_count": 6,
            "analysis_count": 12,
            "media_count": 0,
            "method_count": 1,
            "techniques": techniques,
            "recommended_views": matrix[:4],
            "trend_cards": [],
            "unusual_points": [],
            "analysis_cards": [],
        },
        "primary_downloads": {},
        "short_names": {"enabled": True},
    }
    receipt = write_powerpoint_summary(
        tmp_path,
        manifest=manifest,
        use_direct_llm=False,
        detail_level="comprehensive",
    )
    assert Path(receipt["pptx_path"]).is_file()
    assert (tmp_path / PPT_MANIFEST_NAME).is_file()
    assert receipt["all_available_scientific_comparison_views"] is False
    assert receipt["consolidated_results_per_technique"] is True
    assert receipt["professional_deck_mode"] is True
    assert receipt["raw_experimental_files_sent_to_llm"] is False
    assert receipt["slide_count"] <= len(specs) + 5
    prs = Presentation(tmp_path / PPTX_NAME)
    title_text = "\n".join(shape.text for shape in prs.slides[0].shapes if hasattr(shape, "text"))
    assert FRONT_SLIDE_FOOTER == "Made by FaradaIQ Studio v320"
    assert FRONT_SLIDE_FOOTER in title_text
    assert receipt["front_slide_footer"] == FRONT_SLIDE_FOOTER
    all_text = "\n".join(shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, "text"))
    for technique in ("CV", "EIS", "DRT", "EAL", "LSB", "AFL"):
        assert technique in all_text
        assert technique in TECHNIQUE_SCENARIOS


def test_powerpoint_llm_artifacts_are_compact_context_only(tmp_path: Path) -> None:
    manifest = {
        "schema": "faradaiq-portable-browser-v290",
        "experiment_name": "No upload QA",
        "datasets": [],
        "comparison": {"matrix": [], "recommended": []},
        "discovery": {"point_count": 0, "measurement_count": 0, "analysis_count": 0, "media_count": 0, "method_count": 0, "techniques": [], "recommended_views": [], "trend_cards": [], "unusual_points": [], "analysis_cards": []},
        "primary_downloads": {},
        "short_names": {"enabled": False},
    }
    receipt = write_powerpoint_summary(tmp_path, manifest=manifest, use_direct_llm=False)
    assert receipt["raw_experimental_files_sent_to_llm"] is False
    context = Path(receipt["compact_context_path"]).read_text(encoding="utf-8")
    assert "compact manifest summaries" in context
    assert "raw experimental files" not in context.lower() or "does not contain raw experimental files" in context.lower()
