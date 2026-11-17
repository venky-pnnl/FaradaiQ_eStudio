import numpy as np
from faradaiq import multidim_v216 as m
from faradaiq.v246_contour_fix import install


def _sparse_layout():
    # 24-point elongated serpentine-like SECCM layout matching the reported view.
    x=[]; y=[]
    for row, yy in enumerate([-110., -165., -220., -275., -330., -385.]):
        xs=np.array([-6000., -5000., -4000., -3000.])
        if row % 2: xs=xs[::-1]
        x.extend(xs.tolist())
        y.extend((yy + np.array([0., -8., 6., -4.])).tolist())
    x=np.asarray(x); y=np.asarray(y)
    v=np.sin((x+6000.)/1300.) + np.cos((y+400.)/80.)
    return x,y,v


def test_v246_default_is_contour_plus_points():
    install({})
    opts=m._v216_options()
    assert opts['render_mode'] == 'Contour + points'


def test_sparse_elongated_layout_generates_contour_surface():
    install({})
    x,y,v=_sparse_layout()
    gx,gy,z,regular=m._v216_grid_values(x,y,v,'Linear')
    assert not regular
    assert z.shape == (len(gy),len(gx))
    assert np.isfinite(z).any()
    traces,effective=m._v216_spatial_traces(x,y,v,m._v216_options(), 'Signal')
    assert traces[0].type == 'contour'
    assert 'contour' in effective.lower()
    assert len(traces) == 2


def test_degenerate_triangulation_uses_contour_ready_fallback():
    install({})
    x=np.linspace(0, 10, 12)
    y=0.03*x + 1e-10*np.sin(x)
    v=np.sin(x)
    gx,gy,z,regular=m._v216_grid_values(x,y,v,'Cubic')
    assert z.size and np.isfinite(z).any()
