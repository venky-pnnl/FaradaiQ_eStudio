from __future__ import annotations

import numpy as np
from pathlib import Path

from faradaiq.v315_potential_eis_modeling import analyze_mott_schottky_potential_eis, install


def _synthetic_ms(nsteps=4, nfreq=20):
    cols = [
        "potential_step_index", "frequency_index", "vendor_point_index", "elapsed_time_s", "potential_V", "frequency_Hz", "ac_amplitude_V",
        "E_real_V", "E_imag_V", "I_real_A", "I_imag_A", "I_dc_A", "Zreal_ohm", "Zimag_ohm", "minus_Zimag_ohm", "Zmod_ohm", "phase_deg", "minus_phase_deg",
        "admittance_real_S", "admittance_imag_S", "capacitance_parallel_F", "capacitance_series_F", "inverse_capacitance_parallel_squared_F_minus2", "inverse_capacitance_series_squared_F_minus2", "loss_tangent_abs",
    ]
    rows=[]
    freqs=np.geomspace(1e5,1, nfreq)
    for si, v in enumerate(np.linspace(0.5, -0.2, nsteps), start=1):
        rs=12; rct=100+80*si; c=2e-6*(1+0.2*si)
        for fi, f in enumerate(freqs, start=1):
            w=2*np.pi*f
            z=rs + 1/(1/rct + 1j*w*c)
            zr=float(z.real); zi=float(z.imag); zm=float(abs(z)); phase=float(np.degrees(np.angle(z)))
            den=zr*zr+zi*zi; b=-zi/den; cp=abs(b)/w; cs=1/(w*abs(zi)) if zi else np.nan
            rows.append([si,fi,fi,0,v,f,0.01,0,0,0,0,0,zr,zi,-zi,zm,phase,-phase,zr/den,b,cp,cs,1/(cp*cp),1/(cs*cs),abs(zr/zi)])
    return {"array":np.asarray(rows,float),"columns":cols,"n_potential_steps":nsteps,"n_frequencies":nfreq,"title":"synthetic MS"}


def test_v315_analyzes_potential_dependent_eis_models():
    result=analyze_mott_schottky_potential_eis(_synthetic_ms())
    assert result["status"] == "ok"
    assert len(result["summary_rows"]) == 4
    assert len(result["model_rows"]) == 12
    assert "Rct_or_Rpolarization_ohm" in result["summary_columns"]
    best_models=[row[2] for row in result["summary_rows"]]
    assert all(str(x).startswith("Rs-") for x in best_models)


def test_v315_export_wrapper_adds_three_modeling_csvs(tmp_path):
    class Parser:
        decoded={"mott_schottky":[_synthetic_ms()]}
        def export_analysis_ready_outputs(self, output_dir, base_name):
            return {"n_csv_files":0,"csv_entries":[],"skipped_entries":[]}
    core={"VersaStudioParser":Parser}
    install(core)
    p=Parser()
    result=p.export_analysis_ready_outputs(tmp_path, "demo")
    assert result["n_csv_files"] == 3
    assert (tmp_path/"demo_Potential_EIS_Modeling"/"demo__potential_eis_bestfit_parameters.csv").exists()
    assert (tmp_path/"demo_Potential_EIS_Modeling"/"demo__mott_schottky_auto_linear_windows.csv").exists()
