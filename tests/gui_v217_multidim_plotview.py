import os,time,tkinter as tk
from pathlib import Path
os.environ.setdefault('MPLBACKEND','TkAgg')
import faradaiq_studio_core as core
import faradaiq.multidim_v216 as md
cache=Path('/tmp/fq217_plotview_cache')
(cache/'CV_LSV'/'scanrate_50'/'point_001'/'cycle_01').mkdir(parents=True,exist_ok=True)
p=cache/'CV_LSV'/'scanrate_50'/'point_001'/'cycle_01'/'cv.csv'
p.write_text('potential_V,current_A,time_s\n-0.5,-1e-6,0\n0,0,1\n0.5,1e-6,2\n')
root=tk.Tk(); root.withdraw(); b=core.FaradaIQH5Browser(root,{'cache_root':str(cache),'manifest':{'base_name':'pv'}},'pv')
for _ in range(18): root.update_idletasks(); root.update(); time.sleep(.03)
# Select the first leaf.
def leaves(tree,parent=''):
 out=[]
 for i in tree.get_children(parent):
  ch=tree.get_children(i)
  out.extend(leaves(tree,i) if ch else [i])
 return out
lf=leaves(b.tree); assert lf
b.tree.selection_set(lf[0]); root.update()
b.selected_paths=lambda plottable_only=True:[p]
html=Path('/tmp/fq217_interactive.html'); html.write_text('<html>test</html>',encoding='utf-8')
orig=md.generate
md.generate=lambda browser,mode,metric,opts:{'html':str(html),'engine':'Plotly test'}
b._v216_viz_mode_var.set('Plotly true 3D'); b._v217_run_multidim()
for _ in range(30): root.update_idletasks(); root.update(); time.sleep(.05)
assert len(getattr(b,'plot_views',[]))>=1
v=b.plot_views[-1]
assert v.get('source')=='v217_multidimensional'
assert v.get('interactive_html')==str(html)
opened=[]
orig_open=md._open_html_process
md._open_html_process=lambda path: opened.append(str(path))
b.plot_view_list.selection_clear(0,'end'); b.plot_view_list.selection_set(b.plot_view_list.size()-1)
b.load_selected_plot_view(); root.update()
assert opened and Path(opened[-1])==html
md._open_html_process=orig_open
md.generate=orig
b.destroy(); root.destroy(); print('GUI_V217_PLOTVIEW_PASS')
