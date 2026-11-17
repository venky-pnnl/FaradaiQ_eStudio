from pathlib import Path
import json
import numpy as np
import pandas as pd

from faradaiq import v228_intelligence as v


class Selection:
    def __init__(self, records):
        self.records = list(records)
        self.paths = [Path(r['path']) for r in records]
        self.source = 'test'
    def refresh(self):
        return None
    def selected_paths(self, plottable_only=True):
        return list(self.paths)


class DummyBrowser:
    def __init__(self, root, records):
        self.base_name = 'synthetic_v228'
        self.analyzed_root = Path(root) / 'analysis'
        self.cache_root = Path(root)
        self.h5_path = Path(root) / 'synthetic_faradaiq_bundle.h5'
        self._catalog_records = list(records)
        self._catalog_by_path = {str(Path(r['path']).resolve()).lower(): r for r in records}
        self._v228_selection = Selection(records)
        self._v228_action_history = []
    def after(self, _delay, fn):
        return fn()
    def log(self, _msg):
        pass


def make_cv_files(tmp_path, n=7):
    records=[]
    for idx in range(n):
        e1=np.linspace(0.6,-0.6,70)
        e2=np.linspace(-0.6,0.6,70)
        e=np.r_[e1,e2]
        current=(idx+1)*1e-8 + 2e-7*np.exp(-((e-0.18)/0.08)**2)-2.5e-7*np.exp(-((e+0.22)/0.09)**2)
        p=tmp_path/f'cv_{idx+1:02d}.csv'
        pd.DataFrame({'potential_V':e,'current_A':current}).to_csv(p,index=False)
        records.append({'path':str(p),'technique_key':'CV','method_index':1,'point':idx+1,'cycle':1,'scan_rate':'100 mV/s','x_position':idx*5e-6,'y_position':(idx%3)*5e-6})
    return records


def test_incremental_index_and_quality(tmp_path):
    records=make_cv_files(tmp_path,4)
    b=DummyBrowser(tmp_path,records)
    first=v.build_scientific_index(b)
    second=v.build_scientific_index(b)
    assert first['record_count']==4
    assert second['reused_profiles']==4
    assert second['quality']['score'] >= 0
    assert Path(second['cache_health']['index_path']).exists()


def test_visualization_advice_and_reconstruction(tmp_path):
    records=make_cv_files(tmp_path,9)
    b=DummyBrowser(tmp_path,records)
    advice=v.visualization_advice(b)
    assert advice['coordinates_available'] >= 4
    result=v.validate_reconstruction(b)
    assert result['best_model']
    assert Path(result['output_dir'],'model_cross_validation.csv').exists()
    assert Path(result['output_dir'],'reconstruction_and_uncertainty.npz').exists()


def test_cv_analysis(tmp_path):
    records=make_cv_files(tmp_path,5)
    b=DummyBrowser(tmp_path,records)
    result=v.analyze_cv(b)
    table=pd.read_csv(result['metrics_csv'])
    assert len(table) >= 10
    assert {'anodic_peak_current_A','cathodic_peak_current_A','branch'}.issubset(table.columns)
    assert table['charge_C'].notna().any()


def test_eis_and_drt_analysis(tmp_path):
    f=np.logspace(5,-1,48)
    rs=12.0; rct=180.0; c=8e-5
    z=rs+1/(1/rct+1j*2*np.pi*f*c)
    eis=tmp_path/'eis.csv'
    pd.DataFrame({'frequency_Hz':f,'zreal_ohm':z.real,'zimag_ohm':z.imag}).to_csv(eis,index=False)
    rec_eis={'path':str(eis),'technique_key':'EIS','method_index':1,'point':1}
    b=DummyBrowser(tmp_path,[rec_eis])
    result=v.analyze_eis(b)
    fits=pd.read_csv(result['circuit_ranking_csv'])
    assert 'R-RC' in set(fits['model'])
    assert fits['aic'].notna().any()

    tau=np.logspace(-5,2,200)
    gamma=np.exp(-((np.log10(tau)+2)/0.25)**2)+0.6*np.exp(-((np.log10(tau)-0.5)/0.35)**2)
    drt=tmp_path/'drt.csv'
    pd.DataFrame({'tau_s':tau,'gamma_ohm':gamma}).to_csv(drt,index=False)
    rec_drt={'path':str(drt),'technique_key':'DRT','method_index':1,'point':1}
    b2=DummyBrowser(tmp_path,[rec_drt])
    d=v.analyze_drt(b2)
    peaks=pd.read_csv(d['peaks_csv'])
    assert len(peaks)>=2


def test_ai_context_action_plan_and_report(tmp_path):
    records=make_cv_files(tmp_path,3)
    b=DummyBrowser(tmp_path,records)
    v.build_scientific_index(b)
    context=v.build_ai_context(b)
    assert Path(context['path']).exists()
    plan=v.plan_action(b,'analyze CV peaks and charge')
    assert plan['actions'][0]['id']=='analyze_cv'
    report=v.generate_report(b)
    assert Path(report['html']).exists()
    assert Path(report['output_dir'] if 'output_dir' in report else Path(report['html']).parent).exists()
