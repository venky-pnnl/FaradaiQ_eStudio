from pathlib import Path
import numpy as np
import pandas as pd

from faradaiq.corrosion_metrics_v298 import CorrosionSettings, analyze_trace, _apply_material_constants


def _write(tmp_path: Path, E, I, name='point_001_Tafel_corrosion.csv'):
    p=tmp_path/name
    pd.DataFrame({'potential_V':E,'current_A':I}).to_csv(p,index=False)
    return p


def _clean_curve(n=601, ecorr=-0.55, icorr=2e-8, bc=0.12, ba=0.09):
    E=np.linspace(ecorr-0.30,ecorr+0.30,n); eta=E-ecorr
    I=icorr*(10**(eta/ba)-10**(-eta/bc))
    return E,I


def test_clean_curve_returns_ecorr_slopes_and_icorr(tmp_path):
    E,I=_clean_curve(); r=analyze_trace(_write(tmp_path,E,I), spatial={'point_index':1})
    assert r['ambiguity_status'] in {'CLEAR','REVIEW'}
    assert abs(r['ecorr_V']+0.55)<0.03
    assert r['icorr_A'] is not None and r['icorr_A']>0
    assert r['cathodic_tafel_mV_dec'] is not None
    assert r['anodic_tafel_mV_dec'] is not None


def test_multiple_distinct_zero_crossings_are_flagged(tmp_path):
    E,I=_clean_curve()
    near=np.abs(E+0.55)<0.025
    I[near] += 1.2e-8*np.sin(np.linspace(0,8*np.pi,near.sum()))
    r=analyze_trace(_write(tmp_path,E,I))
    assert r['zero_crossing_count_raw']>1
    assert 'MULTIPLE_ZERO_CROSSINGS' in r['flags'] or 'MULTIPLE_DIPS' in r['flags']
    assert r['ambiguity_status']=='AMBIGUOUS'


def test_no_crossing_boundary_minimum_is_not_normal_ecorr(tmp_path):
    E=np.linspace(-0.9,-0.2,500); I=-1e-8*(1.0+(-0.2-E)*8.0)
    r=analyze_trace(_write(tmp_path,E,I))
    assert r['zero_crossing_cluster_count']==0
    assert 'NO_ZERO_CROSSING' in r['flags']
    assert 'BOUNDARY_MINIMUM' in r['flags']
    assert r['ambiguity_status']=='NO_CLEAR_ECORR'


def test_corrosion_rate_requires_explicit_material_constants(tmp_path):
    E,I=_clean_curve(); r=analyze_trace(_write(tmp_path,E,I))
    assert r['corrosion_rate_mm_per_year'] is None
    result={'rows':[r],'settings':{}}
    _apply_material_constants(result,0.02,27.0,2.7)
    row=result['rows'][0]
    assert row['icorr_A_cm2'] is not None
    assert row['corrosion_rate_mm_per_year'] is not None and row['corrosion_rate_mm_per_year']>0


def test_point_spatial_metadata_preserved(tmp_path):
    E,I=_clean_curve(); r=analyze_trace(_write(tmp_path,E,I),spatial={'point_index':8,'x_position_m':1e-4,'y_position_m':2e-4,'scan_distance_m':3e-4})
    assert r['point_index']==8
    assert r['x_position_m']==1e-4 and r['y_position_m']==2e-4 and r['scan_distance_m']==3e-4

def test_no_clear_ecorr_leaves_reported_ecorr_and_icorr_blank(tmp_path):
    E=np.linspace(-0.9,-0.2,500); I=-1e-8*(1.0+(-0.2-E)*8.0)
    r=analyze_trace(_write(tmp_path,E,I,'point_007_Tafel_corrosion.csv'))
    assert r['ambiguity_status']=='NO_CLEAR_ECORR'
    assert r['ecorr_V'] is None
    assert r['icorr_A'] is None
    assert r['ecorr_seed_V'] is not None
