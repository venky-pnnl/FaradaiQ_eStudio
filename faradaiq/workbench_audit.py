"""Append explicit advanced analysis provenance without touching raw data."""
from datetime import datetime, timezone
from pathlib import Path
import json
import threading
from .release_identity import APP_VERSION
_LOCK = threading.Lock()


def record_operation(workspace, action, inputs=(), parameters=None, outputs=()):
    path = Path(workspace).expanduser().resolve() / 'Workbench' / 'analysis_history.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(version=APP_VERSION, time_utc=datetime.now(timezone.utc).isoformat(), action=str(action),
               inputs=list(map(str, inputs)), parameters=dict(parameters or {}), outputs=list(map(str, outputs)))
    with _LOCK:
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
    return row


def read_history(workspace):
    path = Path(workspace) / 'Workbench' / 'analysis_history.jsonl'
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict): rows.append(value)
        except ValueError: pass
    return rows
