from pathlib import Path
import json
import pandas as pd

from faradaiq.potential_identity_v309 import resolve_potential_identity, legend_labels
from faradaiq.v309_drt_spectrum_inputs import rebuild_drt_inputs_from_parsed_groups, validate_drt_outputs


def test_structured_fixed_potential():
    p=resolve_potential_identity(technique="EIS",metadata={"bias_potential_V":-0.4})
    assert p.set_potential_V == -0.4 and p.potential_mode == "fixed" and p.potential_label == "-0.4 V"

def test_ocp_preserves_mode():
    p=resolve_potential_identity(technique="EIS",metadata={"potential_mode":"OCP","ocp_V":-0.043})
    assert p.potential_mode == "ocp" and p.potential_label.startswith("OCP") and "-0.043 V" in p.potential_label

def test_filename_fallback_and_pt_notation():
    assert resolve_potential_identity(technique="EIS",path="x__-0.400_V__freq_zreal_zimag.csv").set_potential_V == -0.4
    assert resolve_potential_identity(technique="EIS",path="eis_bias_0pt05V.csv").set_potential_V == 0.05

def test_scan_rate_and_amplitude_are_not_set_potential():
    assert resolve_potential_identity(technique="CV",path="sample_CV-50mV_method03.asc").potential_mode == "unknown"
    assert resolve_potential_identity(technique="EIS",path="sample_amplitude_10mV_eis.csv").potential_mode == "unknown"

def test_auto_legend_hierarchy():
    labels,title=legend_labels([
        {"point":1,"method_index":1,"potential_label":"-0.4 V"},
        {"point":2,"method_index":1,"potential_label":"-0.4 V"},
    ],"Auto",["old1","old2"])
    assert labels == ["P001 · -0.4 V","P002 · -0.4 V"] and title == "Point · Potential"
    labels,title=legend_labels([
        {"point":3,"method_index":1,"potential_label":"-0.2 V"},
        {"point":3,"method_index":1,"potential_label":"-0.4 V"},
    ],"Auto",["old1","old2"])
    assert labels == ["-0.2 V","-0.4 V"] and title == "Potential"

def test_drt_inherits_source_eis_potential(tmp_path):
    data=tmp_path/"Parsed"; groups=data/"generic_Groups"; groups.mkdir(parents=True)
    df=pd.DataFrame({"frequency_Hz":[1000,100,10,1],"Zreal_ohm":[1,2,3,4],"Zimag_ohm":[-1,-2,-3,-4],"set_potential_V":[-0.6]*4})
    df.to_csv(groups/"spectrum_001.csv",index=False)
    result=rebuild_drt_inputs_from_parsed_groups([data])
    assert result["spectra_written"] == 1
    drt_dir=tmp_path/"DRT_input_csv"; manifest=json.loads((drt_dir/"DRT_input_spectrum_manifest.json").read_text())
    item=manifest["spectra"][0]; assert item["set_potential_V"] == -0.6 and item["potential_label"] == "-0.6 V"
    outdir=drt_dir/"DRT"; outdir.mkdir()
    inp=Path(item["file"])
    out=outdir/f"{inp.stem}_simple_drt_data.csv"
    pd.DataFrame({"tau_s":[.1,1.0],"gamma_ohm":[2,3]}).to_csv(out,index=False)
    validation=validate_drt_outputs(drt_dir)
    assert validation["complete"]
    child=pd.read_csv(out)
    assert set(child["potential_label"]) == {"-0.6 V"}
    assert set(child["source_eis"]) == {"spectrum_001.csv"}

def test_unknown_falls_back_without_crash():
    p=resolve_potential_identity(technique="EIS",path="method01_point003.csv")
    assert p.potential_mode == "unknown"
    labels,title=legend_labels([{}],"Auto",["M01 | P003"])
    assert labels == ["M01 | P003"]

def test_portable_browser_carries_potential_labels(tmp_path):
    from faradaiq.v290_portable_browser import build_manifest, write_portable_browser
    for v in (-0.2,-0.4):
        pd.DataFrame({"frequency_Hz":[1000,100,10,1],"Zreal_ohm":[1,2,3,4],"Zimag_ohm":[-1,-2,-3,-4],"set_potential_V":[v]*4}).to_csv(tmp_path/f"EIS_bias_{v:+.1f}V.csv",index=False)
    m=build_manifest(tmp_path,experiment_name="generic",create_batch_zips=False)
    eis=[d for d in m["datasets"] if d.get("technique_key") in {"EIS","GEIS"}]
    assert {d.get("potential_label") for d in eis} >= {"-0.2 V","-0.4 V"}
    result=write_portable_browser(tmp_path,experiment_name="generic",create_batch_zips=False)
    html=Path(result["html_path"]).read_text(encoding="utf-8")
    assert "potentialCurveLabel" in html and "-0.2 V" in html and "-0.4 V" in html


def test_powerpoint_comparison_uses_potential_legend(tmp_path, monkeypatch):
    import matplotlib.axes
    from faradaiq.v290_powerpoint_summary import _plot_comparison
    captured=[]
    original=matplotlib.axes.Axes.legend
    def spy(self,*a,**k):
        captured.extend(list(self.get_legend_handles_labels()[1]))
        return original(self,*a,**k)
    monkeypatch.setattr(matplotlib.axes.Axes,"legend",spy)
    datasets=[]
    for i,v in enumerate((-0.2,-0.4),1):
        datasets.append({"id":f"d{i}","technique_key":"EIS","technique_label":"EIS","point":3,"method_index":1,"method_label":"Method 01","set_potential_V":v,"potential_label":f"{v:.1f} V","potential_mode":"fixed","preview":{"rows":[[1000,1,-1],[100,2,-2],[10,3,-3],[1,4,-4]]},"columns":["frequency_Hz","Zreal_ohm","Zimag_ohm"],"plot_views":[{"id":"nyquist","x_index":1,"y_index":2,"x_column":"Zreal_ohm","y_column":"Zimag_ohm","y_transform":"negate"}]})
    manifest={"datasets":datasets}
    view={"dataset_ids":["d1","d2"],"view_id":"nyquist","technique_key":"EIS","technique_label":"EIS","view_label":"Nyquist","x_column":"Zreal_ohm","y_column":"Zimag_ohm"}
    out=tmp_path/"comparison.png"
    r=_plot_comparison(manifest,view,out)
    assert r["created"] and out.exists()
    assert "-0.2 V" in captured and "-0.4 V" in captured

def test_filename_date_or_sample_token_is_not_mislabeled_as_set_potential():
    p = resolve_potential_identity(technique="EIS", path="20240101_GenericSample_EIS_point.csv")
    assert p.potential_mode == "unknown"
    assert p.set_potential_V is None
