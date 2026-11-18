"""The browser exports the displayed values and uses scientific impedance axes."""

import io
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import pytest

from faradaiq.browser_plotting import (
    build_impedance_figure, displayed_plot_csv, displayed_plot_hdf5, write_native_view_archive,
)


def test_bode_uses_conventional_phase_and_keeps_frequency_order(tmp_path):
    path = tmp_path / "anodic.csv"
    table = pd.DataFrame({"frequency_Hz": [100., 1.], "Zreal_ohm": [3., 4.],
                          "neg_zimag_ohm": [4., 3.]})
    figure = build_impedance_figure([(path, table)], representation="Bode")
    assert list(figure.data[0].x) == [1., 100.]
    assert list(figure.data[0].y) == [5., 5.]
    assert figure.data[1].y == pytest.approx(np.rad2deg(np.arctan2([-3., -4.], [4., 3.])))
    assert figure.layout.xaxis.type == "log"
    assert figure.layout.yaxis.type == "log"
    assert figure.layout.yaxis2.title.text == "Phase (°)"
    csv = pd.read_csv(io.BytesIO(displayed_plot_csv(figure)))
    assert set(csv["panel"]) == {1, 2}
    assert len(csv) == 4
    with h5py.File(io.BytesIO(displayed_plot_hdf5(figure))) as plots:
        assert plots.attrs["application_version"] == "v337"
        assert plots["curves/trace_002"][:, 1] == pytest.approx(figure.data[1].y)
    assert table["neg_zimag_ohm"].tolist() == [4., 3.]


def test_native_bundle_selects_only_generated_view_directory(tmp_path):
    from zipfile import ZipFile

    generated = tmp_path / "View_1" / "views"
    generated.mkdir(parents=True)
    (tmp_path / "not-for-export.txt").write_text("unrelated experiment")
    main = generated / "map.html"
    main.write_text("<html>map</html>")
    (generated / "assets.js").write_text("/* plot library */")
    archive = write_native_view_archive(main)
    assert archive.parent == generated.parent
    with ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {"map.html", "assets.js"}
        assert zipped.read("map.html") == main.read_bytes()
