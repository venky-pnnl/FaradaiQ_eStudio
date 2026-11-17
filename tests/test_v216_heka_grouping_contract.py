from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace


def _write(path: Path, headers: list[str], row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader(); w.writerow(row)


def _fixture(cache: Path) -> None:
    records = []
    extracted = []
    rates = [100, 75, 50, 25]
    group = 1
    # Initial approach before point 1.
    p = cache / "HEKA" / "group_001_NewPoint" / "Approach_curve" / "point_000" / "series_001_TipDown" / "sweep_001" / "approach.csv"
    _write(p, ["distance_m", "current_A"], {"distance_m": 0.0, "current_A": 0.0})
    records.append({"record_type":"table","technique":"Approach curve","heka_technique":"Approach curve","point":0,"group_index":1,"group_title":"NewPoint","series_index":1,"series_index_in_group":1,"series_title":"TipDown","sweep_index":1,"heka_v216":True,"included_in_h5":True})
    extracted.append(str(p))
    group = 2
    for point in range(1, 12):
        # Takedata anchor.
        p = cache / "HEKA" / f"group_{group:03d}_NewPoint" / "Position_Takedata" / f"point_{point:03d}" / "series_002_Takedata" / "sweep_001" / "position.csv"
        _write(p, ["time_s", "x_position", "y_position"], {"time_s":0.0,"x_position":point,"y_position":0})
        records.append({"record_type":"table","technique":"Position/Takedata","heka_technique":"Position/Takedata","point":point,"group_index":group,"group_title":"NewPoint","series_index":point*10+2,"series_index_in_group":2,"series_title":"Takedata","sweep_index":1,"heka_v216":True,"included_in_h5":True})
        extracted.append(str(p))
        for rate in rates:
            group += 1
            for cycle in range(1, 4):
                p = cache / "HEKA" / f"group_{group:03d}_CV-{rate}mV" / "CV" / f"point_{point:03d}" / "series_001_CV" / f"sweep_{cycle:03d}" / f"cv_c{cycle}.csv"
                _write(p,["time_s","potential_V","current_A"],{"time_s":0.0,"potential_V":0.1,"current_A":1e-9})
                records.append({"record_type":"table","technique":"CV / LSV","heka_technique":"CV","point":point,"cycle":cycle,"group_index":group,"group_title":f"CV-{rate}mV","series_index":point*100+rate,"series_index_in_group":1,"series_title":"CV","sweep_index":cycle,"scan_rate_mV_s":rate,"scan_rate":f"{rate} mV/s","heka_v216":True,"included_in_h5":True})
                extracted.append(str(p))
        group += 1
        p = cache / "HEKA" / f"group_{group:03d}_E-{2500+2*point}" / "EIS" / f"point_{point:03d}" / "series_000_EIS_frequency_Series_aggregated_by_Group" / "sweep_000" / "eis.csv"
        _write(p,["frequency_Hz","Zreal_ohm","Zimag_ohm"],{"frequency_Hz":1000,"Zreal_ohm":10,"Zimag_ohm":-2})
        records.append({"record_type":"table","technique":"EIS","heka_technique":"EIS","point":point,"group_index":group,"group_title":f"E-{2500+2*point}","series_index":0,"series_index_in_group":0,"series_title":"EIS frequency Series aggregated by Group","sweep_index":0,"heka_v216":True,"included_in_h5":True})
        extracted.append(str(p))
        group += 1
    payload={"manifest":{"data_structure":{"records":records}},"extracted_files":extracted,"extract_policy_version":"v216"}
    (cache/"_bundle_source.json").write_text(json.dumps(payload),encoding="utf-8")


def test_heka_catalog_has_all_points_methods_and_cycles(tmp_path: Path) -> None:
    from faradaiq.browser_catalog_v209 import build_catalog
    cache=tmp_path/".browser_cache_from_h5"; _fixture(cache)
    cat=build_catalog(cache,base_name="GenericHEKA")
    assert cat["technique_counts"]["POSITION"] == 11
    assert cat["point_counts"]["POSITION"] == 11
    assert cat["technique_counts"]["CV"] == 132
    assert cat["method_counts"]["CV"] == 4
    assert cat["point_counts"]["CV"] == 11
    assert cat["technique_counts"]["EIS"] == 11
    cv=[r for r in cat["records"] if r["technique_key"]=="CV"]
    assert sorted({r["point"] for r in cv}) == list(range(1,12))
    assert sorted({r["cycle"] for r in cv}) == [1,2,3]
    assert sorted({r["scan_rate_mV_s"] for r in cv}) == [25.0,50.0,75.0,100.0]
    assert all(sum(1 for r in cv if r["point"]==pt)==12 for pt in range(1,12))


class _Tree:
    def __init__(self): self.rows={}; self.children={"":[]}; self._n=0
    def insert(self,parent,_where,text="",values=(),open=False):
        self._n+=1; iid=f"i{self._n}"; self.rows[iid]={"parent":parent,"text":text,"values":values,"open":open}; self.children.setdefault(parent,[]).append(iid); self.children.setdefault(iid,[]); return iid
    def get_children(self,parent=""): return tuple(self.children.get(parent,[]))
    def delete(self,*iids):
        for iid in iids: self.rows.pop(iid,None); self.children.pop(iid,None)
    def item(self,iid,option=None):
        row=self.rows[iid]
        return row.get(option) if option else row

class _Var:
    def __init__(self): self.value=""
    def set(self,v): self.value=v

class _Text:
    def configure(self,**_k): pass
    def delete(self,*_a): pass
    def insert(self,*_a): pass


def test_heka_browser_tree_exposes_all_position_and_cv_points(tmp_path: Path) -> None:
    from faradaiq.browser_ui_v209 import _refresh_tree
    cache=tmp_path/".browser_cache_from_h5"; _fixture(cache)
    b=SimpleNamespace(cache_root=cache,manifest={},base_name="GenericHEKA",tree=_Tree(),summary_var=_Var(),qa_text=_Text(),log=lambda *_a:None,_v209_core={"_v206_bundle_root":lambda _b:cache,"_v138_fmt_size":lambda n:str(n)})
    _refresh_tree(b)
    texts=[row["text"] for row in b.tree.rows.values()]
    assert "Acquisition positions / Takedata (11)" in texts
    assert "Cyclic voltammetry (CV) (132)" in texts
    assert "Electrochemical impedance spectroscopy (EIS) (11)" in texts
    assert "Grouped by method (4)" in texts
    assert "Grouped by point (11)" in texts
    for pt in range(1,12):
        assert any(t.startswith(f"Point {pt:03d}") for t in texts)
