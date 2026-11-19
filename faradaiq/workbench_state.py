"""Persistent JSON-safe settings and reversible plot state."""
from pathlib import Path
from copy import deepcopy
import math


def encode(value):
    if isinstance(value, Path): return {'__estudio_path__': str(value)}
    if value is None or isinstance(value, (str, bool, int)): return value
    if isinstance(value, float) and math.isfinite(value): return value
    if isinstance(value, (list, tuple)): return [encode(v) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: encode(v) for k, v in value.items()}
    raise TypeError('Runtime object is not a persistent setting.')


def decode(value):
    if isinstance(value, dict):
        if set(value) == {'__estudio_path__'}: return Path(value['__estudio_path__'])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list): return [decode(v) for v in value]
    return value


def capture_settings(state, excluded=()):
    saved = {}
    for key in state:
        if key in excluded: continue
        if not key.startswith('w340_') and key not in {'workbench_section', 'analysis_filter'}: continue
        if any(s in key.casefold() for s in ('secret', 'password', 'token', 'api_key', 'credential')): continue
        try: saved[key] = encode(state[key])
        except (TypeError, ValueError): pass
    return saved


def ephemeral_widget_keys():
    """Exclude events/upload/editor buffers from saved settings in the pinned runtime."""
    from streamlit.runtime.state import get_session_state
    internal = get_session_state()._state
    excluded = set()
    for key, widget_id in internal._key_id_mapper._key_id_mapping.items():
        metadata = internal._new_widget_state.widget_metadata.get(widget_id)
        if metadata is None: continue
        module = getattr(metadata.serializer, '__module__', '')
        if (metadata.value_type in {'trigger_value', 'json_trigger_value', 'chat_input_value', 'file_uploader_state_value'}
                or any(name in module for name in ('button', 'data_editor', 'file_uploader', 'plotly_chart'))):
            excluded.add(key)
    return excluded


def restore_settings(state, saved):
    if not isinstance(saved, dict): return
    for key, value in saved.items():
        if key.startswith('w340_') or key in {'workbench_section', 'analysis_filter'}:
            state[key] = decode(value)


class StateHistory:
    def __init__(self, limit=30):
        self.entries, self.cursor, self.limit = [], -1, max(2, int(limit))

    def push(self, value):
        item = encode(value)
        if self.cursor >= 0 and self.entries[self.cursor] == item: return
        self.entries = (self.entries[:self.cursor+1] + [deepcopy(item)])[-self.limit:]
        self.cursor = len(self.entries)-1

    def undo(self):
        if self.cursor <= 0: return None
        self.cursor -= 1
        return decode(deepcopy(self.entries[self.cursor]))

    def redo(self):
        if self.cursor >= len(self.entries)-1: return None
        self.cursor += 1
        return decode(deepcopy(self.entries[self.cursor]))
