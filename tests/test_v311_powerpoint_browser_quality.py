from __future__ import annotations

import json
from pathlib import Path
from pptx import Presentation


def _manifest(techniques=("GCD", "DRT", "RDE")):
    datasets=[]; matrix=[]; tech_rows=[]
    specs={
        "GCD": ("time_s", "capacity_mAh", "charge_discharge"),
        "DRT": ("tau_s", "gamma_ohm", "drt_tau"),
        "RDE": ("potential_V", "current_A", "potential_current"),
    }
    for tech in techniques:
        x,y,view=specs[tech]
        ids=[]
        for point in (1,2):
            did=f"{tech}_{point}"; ids.append(did)
            datasets.append({
                "id":did,"display_name":did,"technique_key":tech,"technique_label":tech,
                "family":"QA","data_kind":"analysis" if tech=="DRT" else "raw",
                "ui_role":"analysis" if tech=="DRT" else "measurement","method_label":"Method 01",
                "point":point,"cycle":1,"sweep":1,"columns":[x,y],
                "preview":{"status":"plot_ready","rows":[[0.0,1.0+point],[1.0,2.0+point],[2.0,1.5+point]],"columns":[x,y]},
                "plot_views":[{"id":view,"label":f"{x} vs {y}","x_index":0,"y_index":1,"x_column":x,"y_column":y,"x_transform":"identity","y_transform":"identity","x_scale":"linear","y_scale":"linear","plot_type":"curve"}],
                "is_media":False,
            })
        matrix.append({"technique_key":tech,"technique_label":tech,"family":"QA","view_id":view,"view_label":f"{x} vs {y}","plot_type":"curve","x_column":x,"y_column":y,"dataset_ids":ids,"dataset_count":2,"unique_points":2,"point_min":1,"point_max":2,"methods":["Method 01"],"description":"QA comparison","score":100})
        tech_rows.append({"technique_key":tech,"technique_label":tech,"family":"QA","dataset_count":2,"point_count":2,"method_count":1,"analysis_count":2 if tech=="DRT" else 0})
    return {"schema":"faradaiq-portable-browser-v318","version":"v316","experiment_name":"Generic technique-aware QA","datasets":datasets,"comparison":{"matrix":matrix,"recommended":matrix},"discovery":{"point_count":2,"measurement_count":4,"analysis_count":2,"media_count":0,"method_count":1,"techniques":tech_rows,"recommended_views":matrix,"trend_cards":[],"unusual_points":[],"analysis_cards":[]},"primary_downloads":{},"short_names":{"enabled":False},"electrochemistry_results":{"ocp":[],"corrosion":[],"takedata":[]}}


def test_active_v311_prompt_has_dynamic_presenter_buckets(tmp_path: Path):
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary
    r=write_powerpoint_summary(tmp_path, manifest=_manifest(), use_direct_llm=False, detail_level="standard")
    prompt=Path(r["prompt_path"]).read_text(encoding="utf-8")
    for key in ("gcd","drt","rde","opening","ocp","corrosion","takedata","eis","cv_lsv","conclusions"):
        assert f'"{key}"' in prompt
    payload=json.loads(Path(r["response_path"]).read_text(encoding="utf-8"))
    notes=payload["narrative"]["presenter_notes"]
    for key in ("gcd","drt","rde"):
        assert key in notes and notes[key]
    assert r["dynamic_presenter_note_buckets"] is True


def test_nonlegacy_technique_notes_reach_generated_slides(tmp_path: Path):
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary
    r=write_powerpoint_summary(tmp_path, manifest=_manifest(("GCD",)), use_direct_llm=False, detail_level="standard")
    prs=Presentation(r["pptx_path"])
    notes="\n".join(sl.notes_slide.notes_text_frame.text for sl in prs.slides)
    assert "Present the GCD evidence" in notes
    assert "do not infer quantities" in notes.lower()


def test_llm_presenter_bucket_routes_when_slide_specific_note_is_omitted(tmp_path: Path, monkeypatch):
    import faradaiq.v290_powerpoint_summary as pptmod
    def fake(_prompt):
        return ({
            "executive_summary":["Grounded summary."],
            "cross_technique_observations":[],
            "conclusions":["Grounded conclusion."],
            "global_cautions":["Grounded caution."],
            "slide_content":{},
            "technique_discussions":{"gcd":{"observations":["Observed."],"interpretations":[],"limitations":[],"recommended_checks":[]}},
            "presenter_notes":{"gcd":["GCD-specific routed narrative from the active v311 LLM response."]},
        }, {"status":"completed","provider":{"name":"QA"}})
    monkeypatch.setattr(pptmod,"_call_direct_llm",fake)
    r=pptmod.write_powerpoint_summary(tmp_path, manifest=_manifest(("GCD",)), use_direct_llm=True, detail_level="standard")
    prs=Presentation(r["pptx_path"])
    notes="\n".join(sl.notes_slide.notes_text_frame.text for sl in prs.slides)
    assert "GCD-specific routed narrative" in notes


def test_browser_registry_matches_promoted_analysis_acronyms():
    from faradaiq.v290_portable_browser import TECHNIQUE_REGISTRY, SCHEMA
    from faradaiq import release_identity
    assert SCHEMA == f"faradaiq-portable-browser-{release_identity.APP_VERSION}"
    for key in ("EAL","LSB","AFL"):
        assert key in TECHNIQUE_REGISTRY
        assert TECHNIQUE_REGISTRY[key]["kind"] == "analysis"


def test_portable_browser_has_runtime_potential_label_helper_and_numeric_sort():
    from faradaiq import v290_portable_browser as browser
    js = browser._JS
    assert "function potentialCurveLabel" in js
    assert "function potentialSortRows" in js
    assert "return potentialSortRows(rows)" in js
    assert "potentialCurveLabel(d,rows)" in js
    assert "visibleTechs.length===1" in js
