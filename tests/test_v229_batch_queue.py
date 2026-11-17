from pathlib import Path
from types import SimpleNamespace
import json

from faradaiq.v229_batch_queue import (
    BatchJob,
    BatchQueueModel,
    _drt_command,
    default_drt_settings,
    run_batch_job,
)


class Var:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class DummyGUI:
    def __init__(self, root):
        self.output_dir_var = Var(str(root))
    def after(self, _delay, callback):
        return callback()


class FakeCollection:
    def __init__(self):
        self.mode = 'single'
        self.source_path = ''
        self.entries = []
    def load(self, path):
        self.source_path = str(path)
        self.entries = [{'entry_name': Path(path).name, 'parser': SimpleNamespace(session={'raw': True})}]
    def summarize(self):
        return {'source_file': self.source_path, 'mode': 'single', 'n_measurements': 2, 'technique_counts': {'CV': 1}}
    def build_parsed_npy_bundle(self, description):
        return {'description': description, 'source_file': self.source_path, 'values': [1, 2]}
    def build_summary_json(self, description):
        return {'description': description, 'source_file': self.source_path}
    def export_analysis_ready_csv(self, output_dir, base_name):
        root = Path(output_dir) / f'{base_name}_analysis_ready_csv'
        root.mkdir(parents=True, exist_ok=True)
        (root / 'cv.csv').write_text('potential_V,current_A\n0,0\n1,1\n', encoding='utf-8')
        manifest = Path(output_dir) / f'{base_name}_analysis_ready_manifest.json'
        manifest.write_text('{}', encoding='utf-8')
        return {'analysis_ready_root': root, 'manifest_path': manifest, 'n_csv_files': 1}


def test_job_roundtrip_validation_and_unique_session(tmp_path):
    src = tmp_path / 'run.pssession'; src.write_text('fixture', encoding='utf-8')
    job = BatchJob(str(src), 'Experiment A', str(tmp_path / 'out'))
    assert not job.validate()
    restored = BatchJob.from_dict(job.to_dict())
    assert restored.job_id == job.job_id
    assert restored.drt_settings['rbf_type'] == 'gaussian'

    model = BatchQueueModel(DummyGUI(tmp_path), {})
    model.jobs.append(job)
    session = model.write_job_session(job, include_browser=False)
    payload = json.loads(session.read_text(encoding='utf-8'))
    assert payload['schema'] == 'faradaiq_main_session_v199'
    assert payload['batch_job']['job_id'] == job.job_id
    assert payload['main_vars']['file_var'] == str(src)
    assert (session.parent / 'export_settings.json').exists()
    assert (session.parent / 'drt_settings.json').exists()


def test_queue_roundtrip_marks_interrupted_job(tmp_path):
    src = tmp_path / 'run.par'; src.write_text('fixture', encoding='utf-8')
    model = BatchQueueModel(DummyGUI(tmp_path), {})
    job = model.add_paths([src], open_window=False)[0]
    job.status = 'Running'
    path = model.save_queue_as(tmp_path / 'queue.faradaiq_queue.json')

    model2 = BatchQueueModel(DummyGUI(tmp_path), {})
    model2.load_queue(path)
    assert model2.jobs[0].status == 'Interrupted'
    assert model2.jobs[0].stage == 'resume_available'


def test_drt_command_carries_every_custom_setting(tmp_path):
    script = tmp_path / 'headless_pyDRTtools.py'; script.write_text('', encoding='utf-8')
    inp = tmp_path / 'DRT_input_csv'; inp.mkdir()
    src = tmp_path / 'source.par'; src.write_text('', encoding='utf-8')
    job = BatchJob(str(src), 'DRT Test', str(tmp_path / 'out'))
    settings = default_drt_settings()
    settings.update({'run_types': ['simple', 'bayesian'], 'rbf_type': 'c4', 'regularization': '2e-4', 'sample_number': '1234', 'run_id': 'custom_run'})
    args = _drt_command(script, inp, settings, job)
    text = ' '.join(args)
    assert '--run_types simple bayesian' in text
    assert '--rbf_type c4' in text
    assert '--regularization 2e-4' in text
    assert '--sample_number 1234' in text
    assert '--run_id custom_run' in text


def test_headless_job_export_and_resume_session(tmp_path):
    src = tmp_path / 'fake.pssession'; src.write_text('fixture', encoding='utf-8')
    job = BatchJob(str(src), 'Queued Experiment', str(tmp_path / 'result'))
    job.export_settings.update({
        'run_technique_parsers': False,
        'canonical_h5_json': False,
        'build_browser_cache': False,
        'raw_vendor_json': True,
    })
    job.drt_settings['enabled'] = False
    events = []
    run_batch_job(job, {'PSSessionCollection': FakeCollection}, lambda *args: events.append(args))
    assert job.status == 'Completed'
    assert Path(job.outputs['parsed_npy']).exists()
    assert Path(job.outputs['summary_json']).exists()
    assert Path(job.outputs['analysis_ready_root'], 'cv.csv').exists()
    assert Path(job.outputs['raw_vendor_json']).exists()
    assert any(stage == 'completed' for _status, stage, _percent, _message in events)


def test_browser_cache_is_embedded_in_normal_session_payload(tmp_path):
    src = tmp_path / 'run.dat'; src.write_text('', encoding='utf-8')
    job = BatchJob(str(src), 'Browser Resume', str(tmp_path / 'out'))
    cache = tmp_path / 'cache'; cache.mkdir(parents=True)
    h5 = tmp_path / 'bundle.h5'; h5.write_bytes(b'h5')
    js = tmp_path / 'bundle.json'; js.write_text('{}', encoding='utf-8')
    job.outputs = {
        'analysis_ready_root': str(tmp_path / 'analysis'),
        'canonical_bundle': {'h5_path': str(h5), 'json_path': str(js)},
        'browser_cache': {'cache_root': str(cache), 'h5_path': str(h5), 'json_path': str(js), 'n_tables': 4},
    }
    model = BatchQueueModel(DummyGUI(tmp_path), {})
    payload = model._main_session_payload(job, include_browser=True)
    assert payload['open_browser_sessions'][0]['class_name'] == 'FaradaIQH5Browser'
    assert payload['open_browser_sessions'][0]['cache_info']['n_tables'] == 4


def test_create_experiment_subfolder_does_not_mutate_configured_output(tmp_path):
    src = tmp_path / 'sample.pssession'; src.write_text('fixture', encoding='utf-8')
    configured = tmp_path / 'configured_output'
    job = BatchJob(str(src), 'Experiment Folder', str(configured))
    job.export_settings.update({
        'create_experiment_subfolder': True,
        'run_technique_parsers': False,
        'canonical_h5_json': False,
        'build_browser_cache': False,
    })
    job.drt_settings['enabled'] = False
    run_batch_job(job, {'PSSessionCollection': FakeCollection}, lambda *_args: None)
    assert job.output_dir == str(configured)
    assert Path(job.outputs['output_dir']) == configured / 'Experiment_Folder'


def test_drt_enabled_without_eis_input_skips_instead_of_failing(tmp_path):
    src = tmp_path / 'cv_only.pssession'; src.write_text('fixture', encoding='utf-8')
    job = BatchJob(str(src), 'CV only', str(tmp_path / 'out'))
    job.export_settings.update({
        'run_technique_parsers': False,
        'canonical_h5_json': False,
        'build_browser_cache': False,
    })
    # DRT stays enabled, but FakeCollection creates no DRT_input_csv directory.
    events = []
    run_batch_job(job, {'PSSessionCollection': FakeCollection}, lambda *args: events.append(args))
    assert job.status == 'Completed'
    assert any(stage == 'drt' and 'skipped' in message.lower() for _status, stage, _percent, message in events)


def test_resume_reuses_validated_core_export_checkpoint(tmp_path):
    src = tmp_path / 'resume.pssession'; src.write_text('fixture', encoding='utf-8')
    out = tmp_path / 'out'; out.mkdir()
    parsed = out / 'resume_parsed.npy'; parsed.write_bytes(b'npy')
    summary = out / 'resume_summary.json'; summary.write_text('{}', encoding='utf-8')
    analysis = out / 'resume_analysis_ready_csv'; analysis.mkdir()
    job = BatchJob(str(src), 'Resume', str(out))
    job.export_settings.update({
        'resume_completed_stages': True,
        'run_technique_parsers': False,
        'canonical_h5_json': False,
        'build_browser_cache': False,
    })
    job.drt_settings['enabled'] = False
    job.completed_stages = ['inspection', 'export']
    job.outputs = {
        'output_dir': str(out),
        'parsed_npy': str(parsed),
        'summary_json': str(summary),
        'analysis_ready_root': str(analysis),
    }
    events = []
    # No PSSessionCollection is provided: completion proves the checkpoint was reused.
    run_batch_job(job, {}, lambda *args: events.append(args))
    assert job.status == 'Completed'
    assert any('checkpoint' in message.lower() for _status, _stage, _percent, message in events)
