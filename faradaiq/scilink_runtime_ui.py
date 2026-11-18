"""Display and background actions shared by the eStudio SciLink launch controls."""
from __future__ import annotations

from queue import Empty, Queue
import threading


def runtime_identity(metadata):
    version = str(metadata.get('version') or '').strip()
    commit = str(metadata.get('commit') or '').strip()
    label = f'SciLink {version}' if version else 'SciLink'
    return f'{label} · commit {commit[:12]}' if commit else label


def runtime_status(result):
    label = runtime_identity(result)
    action = result['action']
    if action == 'open':
        return f'Opened {label}.'
    if result.get('changed'):
        verb = 'Installed' if action == 'install' else 'Updated to'
        return f'{verb} {label} and opened Streamlit.'
    return f'{label} is current. Opened Streamlit.'


def activate_scilink(*, update=False, progress=None):
    """Install or update if requested, then launch the existing managed checkout."""
    from . import managed_scilink

    installed = managed_scilink.current()
    if update:
        receipt = managed_scilink.update(progress=progress)
    elif installed:
        receipt = installed
    else:
        raise RuntimeError('Select Update SciLink to install the managed runtime first.')
    if progress is not None:
        progress(f'Starting {runtime_identity(receipt)} Streamlit…')
    url = managed_scilink.launch(no_open=False)
    return {**receipt, 'url': url, 'action': ('update' if installed else 'install') if update else 'open'}


def start_desktop_scilink(gui, *, update=False, on_progress=None, on_done=None, on_error=None):
    """Run the installation or startup off the Tk thread and dispatch status events."""
    if getattr(gui, '_estudio_scilink_busy', False):
        gui.set_status('SciLink installation or startup is already in progress.')
        return False
    events = Queue()
    gui._estudio_scilink_busy = True
    gui.set_status('Checking the SciLink runtime…' if update else 'Starting SciLink Streamlit…')

    def worker():
        try:
            result = activate_scilink(update=update, progress=lambda message: events.put(('progress', message)))
        except Exception as error:
            events.put(('error', str(error)))
        else:
            events.put(('done', result))

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'progress':
                    gui.set_status(str(value))
                    if on_progress:
                        on_progress(str(value))
                    continue
                gui._estudio_scilink_busy = False
                if kind == 'done':
                    gui.set_status(runtime_status(value))
                    if on_done:
                        on_done(value)
                else:
                    gui.set_status(f'SciLink: {value}')
                    if on_error:
                        on_error(value)
                return
        except Empty:
            gui.after(100, poll)

    gui._estudio_scilink_thread = thread = threading.Thread(target=worker, name='estudio-scilink-runtime')
    thread.start()
    gui.after(100, poll)
    return True
