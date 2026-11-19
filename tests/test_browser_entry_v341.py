from pathlib import Path
import importlib.util
import subprocess
import sys

import pytest
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def test_startup_check_and_loopback_configuration():
    entry = ROOT / 'faradaiq_browser_v341.py'
    spec = importlib.util.spec_from_file_location('browser_entry', entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    command = module.streamlit_command(8512)
    assert '--server.address=127.0.0.1' in command
    assert '--server.enableXsrfProtection=true' in command
    assert '--browser.gatherUsageStats=false' in command
    assert '--server.port=8512' in command
    checked = subprocess.run([sys.executable, str(entry), '--check'], text=True, capture_output=True)
    assert checked.returncode == 0, checked.stderr
    assert 'v341' in checked.stdout


def test_browser_displays_local_data_and_scilink_workspaces():
    app = AppTest.from_file(str(ROOT / 'faradaiq/browser_app.py')).run()
    assert not app.exception
    assert 'FaradaIQ eStudio' in app.title[0].value
    assert any('SciLink' in str(option) for option in app.radio[0].options)


def _widget(widgets, label):
    return next(widget for widget in widgets if widget.label == label)


def _finish_job(app):
    import time

    deadline = time.monotonic() + 30
    while 'job' in app.session_state and time.monotonic() < deadline:
        time.sleep(0.1)
        app.run()
    assert 'job' not in app.session_state
    assert not app.exception
    assert not app.error, [item.value for item in app.error]


@pytest.mark.parametrize('imaginary_column', ['Zimag_ohm', 'neg_zimag_ohm'])
def test_browser_open_fit_save_and_scilink_handoff(tmp_path, imaginary_column):
    import hashlib
    import json

    import h5py
    import numpy as np
    import pandas as pd

    root = tmp_path / 'Specimen_analysis_ready_csv'
    root.mkdir()
    spectrum = root / 'eis.csv'
    frequency = np.geomspace(1e4, .5, 42)
    z = 5 + 1 / (1 / 40 + 2j * np.pi * frequency * 0.001)
    imaginary = -z.imag if imaginary_column.startswith('neg') else z.imag
    pd.DataFrame({'frequency_Hz': frequency, 'Zreal_ohm': z.real, imaginary_column: imaginary}).to_csv(spectrum, index=False)
    original = hashlib.sha256(spectrum.read_bytes()).hexdigest()

    app = AppTest.from_file(str(ROOT / 'faradaiq/browser_app.py'), default_timeout=20).run()
    app.session_state['preferences'] = {'workspace': str(tmp_path / 'Browser')}
    _widget(app.text_input, 'Local file or folder').set_value(str(root))
    _widget(app.button, 'Open data').click().run()
    assert not app.exception
    _widget(app.radio, 'Workspace').set_value('Analysis').run()
    assert _widget(app.checkbox, 'Invert Y').value == (imaginary_column == 'Zimag_ohm')
    _widget(app.button, 'Run analysis').click().run()
    assert not app.exception
    params = json.loads(app.json[0].value)['Fit']['parameters']
    assert params == pytest.approx({'Rs_ohm': 5, 'Rct_ohm': 40, 'C_F': .001})

    _widget(app.radio, 'Workspace').set_value('Data').run()
    session = tmp_path / 'case.faradaiq_session.json'
    _widget(app.text_input, 'Local session JSON path').set_value(str(session)).run()
    _widget(app.button, 'Save session').click().run()
    assert json.loads(session.read_text())['browser_workspace']['state']['active_table'] == spectrum.name
    _widget(app.button, 'Open session').click().run()
    assert not app.exception

    _widget(app.radio, 'Workspace').set_value('SciLink').run()
    _widget(app.button, 'Make SciLink-ready bundle').click().run()
    _finish_job(app)
    receipt = app.session_state['SciLink bundle']
    with h5py.File(receipt['h5_path']) as bundle:
        assert list(bundle['tables'].values())[0].shape == (len(frequency), 3)
    assert Path(receipt['streamlit_metadata_path']).is_file()
    assert hashlib.sha256(spectrum.read_bytes()).hexdigest() == original
    assert 'Review and attach' in [section.value for section in app.subheader]


def test_browser_batch_opens_solartron_data_in_existing_queue(tmp_path):
    root = tmp_path / 'solar'
    root.mkdir()
    source = root / 'spectrum.z'
    source.write_text(
        'ZPLOT2 ASCII\nFreq(Hz)\tAmpl\tBias\tTime\tZreal\tZimag\tRange\tError\nEnd Comments\n'
        + ''.join(f'{1000 / i:.6f}\t0.01\t0\t{i}\t{i + 5}\t{-i}\t0\t0\n' for i in range(1, 10)),
        encoding='utf-8',
    )
    app = AppTest.from_file(str(ROOT / 'faradaiq/browser_app.py'), default_timeout=30).run()
    app.session_state['preferences'] = {'workspace': str(tmp_path / 'Browser')}
    _widget(app.radio, 'Workspace').set_value('Batch jobs').run()
    _widget(app.text_area, 'Instrument files or folders, one local path per line').set_value(str(source)).run()
    _widget(app.button, 'Add batch jobs').click().run()
    assert not app.exception
    model = app.session_state['batch_queue']
    assert model.queue_path.is_file()
    job = model.jobs[0]
    job.drt_settings['enabled'] = False
    job.export_settings['run_technique_parsers'] = False
    job.export_settings['build_browser_cache'] = False
    _widget(app.multiselect, 'Jobs to run').set_value([job.job_id]).run()
    _widget(app.button, 'Run selected jobs').click().run()
    _finish_job(app)
    assert job.status == 'Completed', job.message
    assert Path(job.outputs['canonical_bundle']['h5_path']).is_file()
