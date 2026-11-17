from __future__ import annotations
import sys, tempfile, tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from faradaiq import ui_tabs_v226
from faradaiq import corrosion_metrics_v298 as cm

class Browser(ttk.Frame):
    def __init__(self,root,data):
        super().__init__(root); self.pack(); self.root=root; self.tree=ttk.Treeview(self); self.tree.pack(); self.ai_compare_entry=ttk.Entry(self)
        tech=self.tree.insert('', 'end', text='IV / Tafel'); self.point=self.tree.insert(tech,'end',text='Point 001'); self.tree.selection_set(self.point)
        self.selected_path=data; self._catalog_records=[]
    def selected_paths(self,plottable_only=False): return [self.selected_path]
    def _open_pysimple(self): pass
    def suggest_comparisons(self): pass
    def ask_ai_comparisons(self): pass
    def apply_selected_suggestion(self): pass

with tempfile.TemporaryDirectory() as tmp:
    data=Path(tmp)/'point_001_Tafel_corrosion.csv'; data.write_text('potential_V,current_A\n-0.6,-1e-8\n-0.5,1e-8\n',encoding='utf-8')
    root=tk.Tk(); root.geometry('1100x900+10+10'); b=Browser(root,data); panel=ttk.Frame(root); panel.pack(fill='both',expand=True)
    calls=[]
    core={'_v138_open_deareis_with_selected':lambda *a,**k:None,'_v225_open_pydrttools_with_selected':lambda *a,**k:None,'_v130_open_tafel_window':lambda *a,**k:None,'_v298_open_corrosion_metrics':lambda *a,**k:calls.append('corrosion'),'_v262_open_ca_cp_analysis':lambda *a,**k:None,'_v262_open_scilink_technique_prompt':lambda *a,**k:None,'_v291_open_ocp_tail_analysis':lambda *a,**k:None,'_v291_open_ocp_scilink_handoff':lambda *a,**k:None}
    ui_tabs_v226._build_analyze_panel(b,panel,core,ttk,tk); root.update_idletasks(); root.update()
    assert b._v298_corrosion_metrics_btn.winfo_viewable(); b._v298_corrosion_metrics_btn.invoke(); assert calls==['corrosion']
    cm.install({'tk':tk,'ttk':ttk,'messagebox':messagebox,'FARADAIQ_PUBLIC_VERSION':'v316'})
    result={'horizontal_axis':'Point','rows':[{'point_index':1,'x_position_m':0.0,'y_position_m':0.0,'scan_distance_m':0.0,'ecorr_V':-0.55,'icorr_A':1e-8,'icorr_A_cm2':None,'cathodic_tafel_mV_dec':120.0,'anodic_tafel_mV_dec':95.0,'corrosion_rate_mm_per_year':None,'ambiguity_status':'AMBIGUOUS','zero_crossing_cluster_count':2,'dip_count':2,'source_filename':'point_001.csv','source_path':str(data),'flags':['MULTIPLE_DIPS']}], 'settings':{}}
    win=cm._show_result(b,result); root.update_idletasks(); root.update(); assert win.winfo_exists(); win.destroy(); root.destroy()
print('v302 robust corrosion metrics Analyze button + result-window smoke: PASS')
