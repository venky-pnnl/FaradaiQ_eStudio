from pathlib import Path
import numpy as np
from faradaiq.workbench_state import StateHistory, capture_settings, restore_settings


def test_settings_preserve_paths_but_exclude_runtime_results_and_credentials(tmp_path):
    state = {'w340_source': tmp_path/'scan.csv', 'w340_smoothing': {'window': 5},
             'w340_result': np.array([1,2]), 'w340_api_key': 'not-a-real-credential',
             'workbench_section': 'Compare and process', 'unrelated': 5}
    saved = capture_settings(state)
    assert set(saved) == {'w340_source','w340_smoothing','workbench_section'}
    restored = {}
    restore_settings(restored,saved)
    assert restored['w340_source'] == tmp_path/'scan.csv'
    assert restored['w340_smoothing'] == {'window':5}


def test_plot_history_undo_redo_and_new_edit_branch():
    history = StateHistory(limit=3)
    history.push({'title':'a'})
    history.push({'title':'a'})
    history.push({'title':'b'})
    history.push({'title':'c'})
    assert history.undo() == {'title':'b'}
    assert history.redo() == {'title':'c'}
    assert history.undo() == {'title':'b'}
    history.push({'title':'d'})
    assert history.redo() is None
    assert history.undo() == {'title':'b'}
    assert history.undo() == {'title':'a'}
    assert history.undo() is None


def test_version_matched_native_workbench_launch(tmp_path, monkeypatch):
    from faradaiq import workbench_launcher
    source = tmp_path/'scan.csv'
    source.write_text('time_s,current_A\n0,1\n1,2\n')
    seen = []
    monkeypatch.setattr(workbench_launcher.subprocess,'Popen',lambda *args,**kwargs:seen.append((args,kwargs)))
    command=workbench_launcher.launch(source)
    assert command[1].endswith('faradaiq_browser_v341.py')
    assert command[-3:] == ['--workbench','--source',str(source)]
    assert seen[0][1]['env']['FARADAIQ_QUIET_LAUNCH']=='1'


def test_native_core_import_keeps_workbench_public_release_identity():
    import subprocess,sys
    script='''
from faradaiq import workbench_reports, workbench_audit, workbench_launcher
import faradaiq_studio_core
from faradaiq.release_identity import APP_VERSION
assert APP_VERSION == 'v341'
assert workbench_reports.APP_VERSION == APP_VERSION
assert workbench_audit.APP_VERSION == APP_VERSION
assert workbench_launcher.APP_VERSION == APP_VERSION
'''
    result=subprocess.run([sys.executable,'-c',script],text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
