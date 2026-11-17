import numpy as np
from faradaiq import multidim_v216 as m

def test_smart_contour_controls_and_point_count():
    x=np.array([0,1,2,0,1,2,0,1,2.],float)
    y=np.array([0,0,0,1,1,1,2,2,2.],float)
    v=np.array([1,2,3,2,4,6,1,3,5.],float)
    opts=m._v216_options({"render_mode":"Filled contour","palette":"Cividis","range_mode":"Robust (2–98%)","smoothing":"Light","adaptive_contours":True,"mask_outside_hull":True})
    traces,effective=m._v216_spatial_traces(x,y,v,opts,"Signal")
    assert len(traces)==2
    assert "n=9" in traces[1].name
    assert traces[0].colorscale is not None
    assert "hull masked" in effective or "regular grid" in effective

def test_manual_palette_range_and_reverse():
    x=np.array([0,1,0,1.]); y=np.array([0,0,1,1.]); v=np.array([0,1,2,3.])
    opts=m._v216_options({"render_mode":"Contour lines","palette":"RdBu","reverse_palette":True,"range_mode":"Manual","range_min":"-1","range_max":"4","adaptive_contours":False,"contour_levels":7,"contour_labels":True})
    traces,_=m._v216_spatial_traces(x,y,v,opts,"V")
    assert traces[0].zmin == -1
    assert traces[0].zmax == 4
    assert traces[0].ncontours == 7
