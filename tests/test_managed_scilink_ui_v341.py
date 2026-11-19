"""Managed SciLink activation through both eStudio interfaces."""
from collections import deque
from pathlib import Path
import sys
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    import faradaiq

    actions = []
    runtime_paths = tmp_path / 'runtime' / ('Scripts' if sys.platform == 'win32' else 'bin')
    runtime_paths.mkdir(parents=True)
    executable = runtime_paths / ('scilink.exe' if sys.platform == 'win32' else 'scilink')
    executable.touch()
    python = runtime_paths / ('python.exe' if sys.platform == 'win32' else 'python')
    python.touch()
    metadata = {'version': '0.0.80', 'commit': '9dff180ddd7eb05dec73fecad44605c291d993688', 'changed': True}
    state = {'installed': False, 'running': False}
    url = 'http://127.0.0.1:8815'

    def update(*, progress=None):
        if progress:
            progress('Installing the reviewed SciLink runtime.')
        actions.append('update')
        state['installed'] = True
        return dict(metadata)

    def launch(*, no_open=False):
        assert state['installed']
        actions.append(('launch', no_open))
        state['running'] = True
        return url

    runtime = SimpleNamespace(update=update, launch=launch,
                              current=lambda: dict(metadata) if state['installed'] else None,
                              running_url=lambda: url if state['running'] else None,
                              runtime_python=lambda: python if state['installed'] else None,
                              runtime_cli=lambda: executable if state['installed'] else None,
                              state=state, actions=actions, url=url, python=python, cli=executable)
    monkeypatch.setitem(sys.modules, 'faradaiq.managed_scilink', runtime)
    monkeypatch.setattr(faradaiq, 'managed_scilink', runtime, raising=False)
    return runtime


def _drain_browser_job(app):
    end = monotonic() + 12
    while 'job' in app.session_state and monotonic() < end:
        sleep(.05)
        app.run()
    assert 'job' not in app.session_state
    assert not app.exception
    assert not app.error


def test_browser_updates_and_opens_scilink_without_a_manual_url(fake_runtime):
    app = AppTest.from_file(str(ROOT / 'faradaiq' / 'browser_app.py')).run()
    next(widget for widget in app.radio if widget.label == 'Workspace').set_value('SciLink').run()
    next(widget for widget in app.button if widget.label == 'Update SciLink').click().run()
    _drain_browser_job(app)
    assert fake_runtime.actions == ['update', ('launch', False)]
    assert any('9dff180ddd7e' in widget.value for widget in app.caption)
    assert any(fake_runtime.url in element.proto.url for element in app.get('link_button'))


def test_browser_update_failure_keeps_installed_scilink_openable(fake_runtime, monkeypatch):
    fake_runtime.state['installed'] = True

    def unavailable(**_kwargs):
        raise RuntimeError('The official source could not be reached.')

    monkeypatch.setattr(fake_runtime, 'update', unavailable)
    app = AppTest.from_file(str(ROOT / 'faradaiq' / 'browser_app.py')).run()
    next(widget for widget in app.radio if widget.label == 'Workspace').set_value('SciLink').run()
    next(widget for widget in app.button if widget.label == 'Update SciLink').click().run()
    deadline = monotonic() + 12
    while 'job' in app.session_state and monotonic() < deadline:
        sleep(.05)
        app.run()
    assert any('official source could not be reached' in widget.value for widget in app.error)
    assert not fake_runtime.actions
    next(widget for widget in app.button if widget.label == 'Open SciLink').click().run()
    _drain_browser_job(app)
    assert fake_runtime.actions == [('launch', False)]


def test_reviewed_browser_python_prefers_current_managed_install(fake_runtime, monkeypatch):
    from faradaiq.browser_service_ui import _scilink_python_default

    monkeypatch.delenv('SCILINK_PYTHON', raising=False)
    monkeypatch.delenv('FARADAIQ_SCILINK_PYTHON', raising=False)
    assert _scilink_python_default() == sys.executable
    fake_runtime.state['installed'] = True
    assert _scilink_python_default() == str(fake_runtime.python)
    monkeypatch.setenv('SCILINK_PYTHON', '/operator/scilink/python')
    assert _scilink_python_default() == '/operator/scilink/python'


def test_desktop_cli_uses_managed_runtime_after_an_explicit_override(fake_runtime, monkeypatch, tmp_path):
    from faradaiq_scilink_cli_job import _candidate_commands, _scilink_python

    monkeypatch.delenv('SCILINK_EXE', raising=False)
    monkeypatch.delenv('SCILINK_CLI', raising=False)
    fake_runtime.state['installed'] = True
    assert _candidate_commands({})[0] == [str(fake_runtime.cli)]
    override = tmp_path / 'my-scilink'
    override.touch()
    assert _candidate_commands({'scilink_executable_override': str(override)})[0] == [str(override)]
    if sys.platform != 'win32':
        fake_runtime.python.unlink()
        fake_runtime.python.symlink_to(sys.executable)
    assert _scilink_python([str(fake_runtime.cli)]) == fake_runtime.python


def test_desktop_background_update_reports_version_and_opens(fake_runtime):
    from faradaiq.scilink_runtime_ui import start_desktop_scilink

    pending, messages, receipt = deque(), [], []
    gui = SimpleNamespace(after=lambda _ms, callback: pending.append(callback), set_status=messages.append)
    assert start_desktop_scilink(gui, update=True, on_done=receipt.append)
    deadline = monotonic() + 10
    while getattr(gui, '_estudio_scilink_busy', False) and monotonic() < deadline:
        if pending:
            pending.popleft()()
        sleep(.01)
    assert not gui._estudio_scilink_busy
    assert fake_runtime.actions == ['update', ('launch', False)]
    assert receipt[0]['url'] == fake_runtime.url
    assert any('9dff180ddd7e' in message for message in messages)
