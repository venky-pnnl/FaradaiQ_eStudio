from pathlib import Path


def test_v311_log_axis_labels_are_plain_mathtext_safe(tmp_path: Path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from faradaiq.v290_powerpoint_summary import _v307_disable_mathtext_for_figure, _v307_safe_savefig

    fig, ax = plt.subplots(figsize=(5, 3), dpi=100)
    ax.plot([1e-2, 1e0, 1e3], [1, 2, 3])
    ax.set_xscale('log')
    ax.set_title('EIS frequency axis')
    _v307_disable_mathtext_for_figure(fig)
    fig.canvas.draw()
    labels = [t.get_text() for t in ax.get_xticklabels() if t.get_text()]
    assert labels
    assert not any('$' in label or 'mathdefault' in label for label in labels)
    out = tmp_path / 'log_axis.png'
    _v307_safe_savefig(fig, out, bbox_inches='tight')
    plt.close(fig)
    assert out.is_file() and out.stat().st_size > 1000


def test_v311_powerpoint_generation_survives_log_scale_eis(tmp_path: Path):
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary, PPTX_NAME

    ds = {
        'id': 'eis-1',
        'display_name': 'generic EIS spectrum',
        'technique_key': 'EIS',
        'technique_label': 'Electrochemical impedance',
        'family': 'Impedance',
        'point': 1,
        'method_label': 'EIS',
        'columns': ['frequency_Hz', 'z_mod_ohm'],
        'table': {'frequency_Hz': [1e3, 1e2, 1e1, 1e0], 'z_mod_ohm': [10.0, 30.0, 100.0, 250.0]},
        'plot_views': [{'id': 'bode_mag', 'label': 'Bode magnitude', 'x': 'frequency_Hz', 'y': 'z_mod_ohm', 'x_scale': 'log'}],
    }
    view = {
        'view_id': 'bode_mag', 'view_label': 'Bode magnitude', 'technique_key': 'EIS',
        'technique_label': 'Electrochemical impedance', 'family': 'Impedance',
        'dataset_ids': ['eis-1'], 'dataset_count': 1, 'unique_points': [1],
        'x_column': 'frequency_Hz', 'y_column': 'z_mod_ohm', 'score': 100,
    }
    manifest = {
        'schema': 'faradaiq-portable-browser-v316', 'version': 'v316', 'experiment_name': 'Generic PPT QA',
        'datasets': [ds], 'comparison': {'matrix': [view], 'recommended': [view]},
        'discovery': {'point_count': 1, 'measurement_count': 1, 'analysis_count': 0, 'media_count': 0, 'method_count': 1,
                      'techniques': [{'technique_key': 'EIS', 'technique_label': 'Electrochemical impedance', 'dataset_count': 1, 'point_count': 1, 'method_count': 1, 'analysis_count': 0}],
                      'recommended_views': [view], 'trend_cards': [], 'unusual_points': [], 'analysis_cards': []},
        'electrochemistry_results': {},
    }
    receipt = write_powerpoint_summary(tmp_path, manifest=manifest, use_direct_llm=False, detail_level='comprehensive')
    assert receipt['pptx_name'] == PPTX_NAME
    assert (tmp_path / PPTX_NAME).is_file()
    assert receipt['slide_count'] >= 1
