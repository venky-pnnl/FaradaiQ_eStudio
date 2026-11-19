"""Persistent multi-instrument projects backed by canonical measurement CSVs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
from tempfile import NamedTemporaryFile, TemporaryDirectory
import threading
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from .browser_data import BrowserData, _portable_parts
from .readable_names import path_component, safe_component
from .workbench_import import (CANONICAL_COLUMNS, INSTRUMENT_NAMES, ImportProfile, IncompleteSourceError, _readable_frame, open_verified,
                               sha256_source, source_signature, temporary_import_dir)

PROJECT_SCHEMA = 'faradaiq-multi-instrument-project-1'
PROJECT_FILE = 'project.json'
CHECKPOINT_FILE = 'project.checkpoint.json'
MANIFEST_FILE = 'analysis_ready_manifest.json'
QUEUE_LABEL = 'Project import queue'
_UNDO_LIMIT = 50
_ID = re.compile('[a-f0-9]{32}')


def _json_copy(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
    try:
        with pending.open('w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)
    return path


def _read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _tags(tags):
    if isinstance(tags, str):
        tags = tags.split(',')
    normalized = {}
    for tag in tags or ():
        if value := str(tag).strip():
            normalized.setdefault(value.casefold(), value)
    return list(normalized.values())


def _safe_table(root, value, folder, *, storage='Measurements'):
    parts = _portable_parts(value)
    if len(parts) != 3 or parts[:2] != ('Measurements', folder) or Path(parts[-1]).suffix.lower() != '.csv':
        raise ValueError(f'Invalid project measurement location: {value}')
    path = root.joinpath(storage, *parts[1:])
    if not path.resolve().is_relative_to(root) or any(parent.is_symlink() for parent in (path, path.parent, path.parent.parent)):
        raise ValueError(f'Unsafe project measurement location: {value}')
    return path


def _iter_measurements(content):
    for handle in content['handles']:
        for measurement in handle['measurements']:
            yield handle, measurement


def _validate_content(content):
    if not isinstance(content, dict) or not isinstance(content.get('handles'), list) or not isinstance(content.get('state', {}), dict):
        raise ValueError('The project has invalid handles or comparison settings.')
    handles = set()
    folders = set()
    for handle in content['handles']:
        if (not isinstance(handle, dict) or not isinstance(handle.get('id'), str) or not _ID.fullmatch(handle['id'])
                or not isinstance(handle.get('folder'), str) or len(_portable_parts(handle['folder'])) != 1
                or not isinstance(handle.get('measurements'), list) or not isinstance(handle.get('source_path'), str)
                or not all(isinstance(handle.get(key), str) for key in ('label', 'instrument'))):
            raise ValueError('The project contains an invalid measurement source.')
        if handle['id'] in handles or handle['folder'].casefold() in folders:
            raise ValueError('The project contains duplicate measurement sources.')
        handles.add(handle['id'])
        folders.add(handle['folder'].casefold())
        seen = set()
        for measurement in handle['measurements']:
            if (not isinstance(measurement, dict) or not isinstance(measurement.get('path'), str)
                    or not isinstance(measurement.get('record'), dict)):
                raise ValueError('The project contains invalid measurement metadata.')
            if measurement['path'].casefold() in seen:
                raise ValueError('The project contains duplicate canonical measurement paths.')
            seen.add(measurement['path'].casefold())


def _validate_snapshot(payload):
    if (not isinstance(payload, dict) or payload.get('schema') != PROJECT_SCHEMA
            or not isinstance(payload.get('project_id'), str) or not _ID.fullmatch(payload['project_id'])):
        raise ValueError('Choose a FaradaIQ multi-instrument project or checkpoint.')
    _validate_content(payload)
    for key in ('undo', 'redo'):
        if not isinstance(payload.get(key, []), list):
            raise ValueError('The project contains invalid edit history.')
        for content in payload.get(key, []):
            _validate_content(content)
    if not isinstance(payload.get('history', []), list) or not all(isinstance(row, dict) for row in payload.get('history', [])):
        raise ValueError('The project contains invalid activity history.')


def _mapping(replacements):
    return sorted(((Path(old).expanduser().resolve(), Path(new).expanduser().resolve())
                   for old, new in replacements.items()), key=lambda pair: len(pair[0].parts), reverse=True)


def _replace_path(value, replacements):
    if not isinstance(value, str) or not value or not Path(value.split('::', 1)[0]).is_absolute():
        return value
    path, delimiter, suffix = value.partition('::')
    path = Path(path)
    for old, new in replacements:
        if path.is_relative_to(old):
            return str(new / path.relative_to(old)) + delimiter + suffix
    return value


def _replace_content(content, replacements):
    for handle in content['handles']:
        handle['source_path'] = _replace_path(handle['source_path'], replacements)
        for measurement in handle['measurements']:
            record = measurement['record']
            for key in ('source_path', 'original_table_path'):
                if key in record:
                    record[key] = _replace_path(record[key], replacements)
    if 'state' in content:
        content['state'] = _replace_settings(content['state'], replacements)


def _migrate_curve_settings(content, root):
    """Upgrade comparison widget and recipe keys from pre-portable project saves."""
    settings = content.get('state', {}).get('workbench_settings')
    if not root or not isinstance(settings, dict) or not any(key.startswith('w340_compare_') for key in settings):
        return
    from .workbench_curves import comparison_key, measurement_key

    root = Path(root)
    source_scope = comparison_key(str(root))
    previous_selection = f'w340_compare_comparison_files_{source_scope}'
    selection = settings.get(previous_selection)
    by_path = {}
    for handle, measurement in _iter_measurements(content):
        path = root / measurement['path']
        old = measurement_key(root, path, handle['id'])
        relative = path.relative_to(root / 'Measurements').as_posix()
        by_path[str(path)] = old, measurement_key(root, path, handle['id'], relative)
    aliases = dict(by_path.values())
    if isinstance(selection, list):
        paths = [value.get('__estudio_path__') if isinstance(value, dict) else value for value in selection]
        if paths and all(isinstance(path, str) and path in by_path for path in paths):
            old, new = zip(*(by_path[path] for path in paths))
            aliases[comparison_key(old)] = comparison_key(new)
    picker_aliases = {f'w340_compare_process_source_{source_scope}': 'w340_compare_process_source_project',
                      previous_selection: 'w340_compare_comparison_files_project'}
    for key in tuple(settings):
        if not key.startswith('w340_compare_'):
            continue
        migrated = picker_aliases.get(key) or '_'.join(aliases.get(part, part) for part in key.split('_'))
        if migrated != key:
            settings.setdefault(migrated, settings.pop(key))


def _replace_settings(value, replacements):
    if isinstance(value, dict):
        return {key: _replace_settings(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_settings(item, replacements) for item in value]
    return _replace_path(value, replacements)


def _instrument(data):
    return str(next((data.metadata[key] for key in ('instrument', 'instrument_name', 'vendor') if data.metadata.get(key)), '')
               or INSTRUMENT_NAMES.get(data.source.suffix.lower(), ''))


def _record_instrument(record):
    for key in ('source_path', 'relative_source_path', 'virtual_source'):
        if vendor := INSTRUMENT_NAMES.get(Path(str(record.get(key) or '')).suffix.lower()):
            return str(record.get('instrument') or record.get('vendor') or vendor)
    return str(record.get('instrument') or record.get('vendor') or '')


def _original_table(data, path):
    if data.source.is_dir() and path.is_relative_to(data.source):
        return str(path)
    if data.source.suffix.lower() == path.suffix.lower() and data.source.name == path.name:
        return str(data.source)
    return f'{data.source}::{path.relative_to(data.analysis_root).as_posix() if path.is_relative_to(data.analysis_root) else path.name}'


class DuplicateSourceError(ValueError):
    def __init__(self, handle):
        self.handle_id = handle['id']
        super().__init__(f'Identical source content is already in this project as {handle["label"]!r}.')


class MultiInstrumentProject:
    """Keep each source's independent measurements together in one browser dataset."""

    def __init__(self, root: Path, name='Comparison session'):
        self.root = Path(root).expanduser().resolve()
        self.name = str(name).strip() or 'Comparison session'
        self.project_id = uuid4().hex
        self.handles = []
        self.state = {}
        self.history = []
        self._undo = []
        self._redo = []
        self._lock = threading.RLock()

    def _content(self):
        return _json_copy(dict(name=self.name, handles=self.handles, state=self.state))

    def snapshot(self):
        with self._lock:
            return dict(schema=PROJECT_SCHEMA, project_id=self.project_id, root=str(self.root), **self._content(),
                        undo=deepcopy(self._undo), redo=deepcopy(self._redo), history=deepcopy(self.history))

    def _persist(self, content, undo, redo, history):
        payload = dict(schema=PROJECT_SCHEMA, project_id=self.project_id, root=str(self.root), root_relative='.',
                       **content, undo=undo, redo=redo, history=history)
        try:
            self._publish_view(content)
            _write_json(self.root / CHECKPOINT_FILE, payload)
        except (OSError, ValueError):
            # Keep the earlier active view when a catalog change cannot be committed.
            try:
                self._publish_view(self._content())
            except (OSError, ValueError):
                pass
            raise
        self.name, self.handles, self.state = content['name'], content['handles'], content['state']
        self._undo, self._redo, self.history = undo, redo, history

    def _event(self, action, **details):
        return dict(time_utc=datetime.now(timezone.utc).isoformat(), action=action, **details)

    def _commit(self, content, action, **details):
        self._persist(content, [*self._undo, self._content()][-_UNDO_LIMIT:], [],
                      [*self.history, self._event(action, **details)])

    def _ensure_separate(self, source):
        if source.is_dir() and self.root.is_relative_to(source):
            raise ValueError('Choose a workbench outside the raw source directory.')
        if source.is_relative_to(self.root):
            raise ValueError('Select raw data outside this project’s generated files.')

    def _duplicate(self, digest):
        return next((handle for handle in self.handles if digest and handle.get('sha256') == digest), None)

    def _prepare_data(self, data, *, label='', instrument='', tags=None, profile=None, digest=''):
        if not data.tables:
            raise ValueError(f'{data.source.name} contains no measurements.')
        handle_id = uuid4().hex
        label = str(label).strip() or data.source.stem or data.source.name
        instrument = str(instrument).strip()
        default_instrument = _instrument(data) or 'Unspecified'
        base = safe_component(label, max_chars=32, default='Source')
        with self._lock:
            sources = self.root / 'Sources'
            if sources.is_symlink() or (self.root / 'Measurements').is_symlink():
                raise ValueError('The project measurement directories must be local folders.')
            used = {item.name.casefold() for item in sources.iterdir()} if sources.is_dir() else set()
            index = 1
            while f'{base}_D{index:03d}'.casefold() in used:
                index += 1
            folder = f'{base}_D{index:03d}'
            target = sources / folder
            target.mkdir(parents=True, exist_ok=False)
        handle = dict(id=handle_id, folder=folder, label=label, instrument=instrument, source_path=str(data.source),
                      sha256=digest, tags=_tags(tags), annotations='', metadata=_json_copy(data.metadata),
                      profile=profile.snapshot() if profile else None, measurements=[])
        try:
            for index, path in enumerate(data.tables, 1):
                title = path_component(target, path.stem, max_chars=64, reserve=12)
                output = target / f'{index:04d}_{title}.csv'
                table = _readable_frame(data.read_table(path), path)
                original_columns = list(map(str, table.columns))
                record = _json_copy(data.records.get(path, {}))
                measurement_instrument = instrument or _record_instrument(record) or default_instrument
                if profile:
                    original_units = record.get('units')
                    table = profile.apply(table)
                    record['original_units'] = original_units or dict(profile.units)
                    mapped_columns = set(profile.columns.values())
                    units = {key: value for key, value in (original_units or {}).items() if key not in mapped_columns} if isinstance(original_units, dict) else {}
                    units.update({CANONICAL_COLUMNS[role]: CANONICAL_COLUMNS[role].split('_')[-1] for role in profile.columns})
                    record['units'] = units
                    if profile.technique:
                        record['technique'] = profile.technique
                original_source = record.pop('source_path', '')
                if original_source:
                    record['original_source_path'] = original_source
                record.pop('hdf5_dataset', None)
                relative = f'Measurements/{folder}/{output.name}'
                export_relative = f'{folder}/{output.name}'
                recorded_tags = _tags(record.get('tags'))
                record.update(handle_id=handle_id, instrument=measurement_instrument, source_path=str(data.source),
                              original_table_path=_original_table(data, path), original_columns=original_columns,
                              display_name=str(record.get('display_name') or record.get('name') or path.stem),
                              tags=_tags([*handle['tags'], *recorded_tags]), relative_csv_path=export_relative,
                              relative_path=export_relative, columns=list(map(str, table.columns)), n_points=len(table))
                table.to_csv(output, index=False)
                handle['measurements'].append(dict(path=relative, record=record))
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        vendors = {measurement['record']['instrument'] for measurement in handle['measurements']}
        handle['instrument'] = next(iter(vendors)) if len(vendors) == 1 else 'Mixed instruments'
        return handle

    def prepare_source(self, source, *, workspace, label='', instrument='', profile=None, tags=None,
                       expected_signature=None, allow_duplicate=False):
        """Build a source's canonical tables without mutating the project catalog."""
        source = Path(source).expanduser().resolve(strict=True)
        self._ensure_separate(source)
        workspace = Path(workspace).expanduser().resolve()
        if source.is_dir() and workspace.is_relative_to(source):
            raise ValueError('Choose an import workspace outside the raw source directory.')
        if profile is not None and not isinstance(profile, ImportProfile):
            profile = ImportProfile.load(profile)
        before = source_signature(source)
        if expected_signature is not None and before != expected_signature:
            raise IncompleteSourceError(f'{source.name} changed after the watch poll. Wait for another stable observation.')
        checksum = sha256_source(source)
        with self._lock:
            duplicate = self._duplicate(checksum)
            if duplicate and not allow_duplicate:
                raise DuplicateSourceError(duplicate)
        with temporary_import_dir(workspace, source) as directory:
            data, diagnosis = open_verified(source, directory)
            handle = self._prepare_data(data, label=label, instrument=instrument or diagnosis.instrument,
                                        tags=tags, profile=profile, digest=checksum)
            handle['allow_duplicate'] = bool(allow_duplicate)
        try:
            if before != source_signature(source):
                raise IncompleteSourceError(f'{source.name} changed during import. Wait for it to finish writing.')
        except BaseException:
            self.discard_prepared(handle)
            raise
        return handle

    def discard_prepared(self, handle):
        handle_id = handle['id']
        if not _ID.fullmatch(handle_id):
            raise ValueError('Invalid measurement source ID.')
        with self._lock:
            if any(existing['id'] == handle_id for existing in self.handles):
                return
            folder = _safe_table(self.root, f'Measurements/{handle["folder"]}/table.csv', handle['folder'], storage='Sources').parent
            shutil.rmtree(folder, ignore_errors=True)

    def apply_prepared(self, handles):
        """Commit prepared sources together after checking for duplicate content again."""
        outcomes = {}
        with self._lock:
            content = self._content()
            seen = {handle['sha256']: handle for handle in self.handles if handle.get('sha256')}
            added = []
            for handle in handles:
                handle_id = handle['id']
                if any(existing['id'] == handle_id for existing in content['handles']):
                    outcomes[handle_id] = ('Imported', handle_id)
                    continue
                digest = handle.get('sha256')
                duplicate = seen.get(digest)
                if duplicate and not handle.get('allow_duplicate'):
                    outcomes[handle_id] = ('Duplicate', str(DuplicateSourceError(duplicate)))
                    self.discard_prepared(handle)
                    continue
                committed = _json_copy(handle)
                committed.pop('allow_duplicate', None)
                if duplicate:
                    committed['duplicate_of'] = duplicate['id']
                    for measurement in committed['measurements']:
                        measurement['record']['duplicate_source_handle_id'] = duplicate['id']
                if digest:
                    seen.setdefault(digest, committed)
                content['handles'].append(committed)
                outcomes[handle_id] = ('Imported', handle_id)
                added.append(handle_id)
            if added:
                self._commit(content, 'Import sources', handle_ids=added,
                             sources=[handle['source_path'] for handle in content['handles'] if handle['id'] in added])
        return outcomes

    def _add_handle(self, handle):
        try:
            status, detail = self.apply_prepared([handle])[handle['id']]
            if status == 'Duplicate':
                with self._lock:
                    duplicate = self._duplicate(handle.get('sha256'))
                if duplicate:
                    raise DuplicateSourceError(duplicate)
                raise ValueError(detail)
            return detail
        except BaseException:
            self.discard_prepared(handle)
            raise

    def add_source(self, source, *, workspace: Path, label='', instrument='', profile=None, tags=None, allow_duplicate=False):
        return self._add_handle(self.prepare_source(source, workspace=workspace, label=label, instrument=instrument,
                                                     profile=profile, tags=tags, allow_duplicate=allow_duplicate))

    def add_data(self, data: BrowserData, label='', instrument='', tags=None, *, profile=None, allow_duplicate=False):
        source = data.source.expanduser().resolve()
        self._ensure_separate(source)
        if profile is not None and not isinstance(profile, ImportProfile):
            profile = ImportProfile.load(profile)
        signature = source_signature(source) if source.exists() else None
        digest = sha256_source(source) if signature else ''
        with self._lock:
            duplicate = self._duplicate(digest)
            if duplicate and not allow_duplicate:
                raise DuplicateSourceError(duplicate)
        handle = self._prepare_data(data, label=label, instrument=instrument, tags=tags, profile=profile, digest=digest)
        handle['allow_duplicate'] = bool(allow_duplicate)
        try:
            if signature and signature != source_signature(source):
                raise IncompleteSourceError(f'{source.name} changed while current data were added.')
        except BaseException:
            self.discard_prepared(handle)
            raise
        return self._add_handle(handle)

    def _publish_view(self, content):
        """Project catalog and active-only table directory for all established export routes."""
        root = self.root / 'Measurements'
        manifest = root / MANIFEST_FILE
        if root.is_symlink() or manifest.is_symlink():
            raise ValueError('The canonical project measurement directory and manifest must be local.')
        records = {}
        for handle, measurement in _iter_measurements(content):
            folder = handle['folder']
            path = _safe_table(self.root, measurement['path'], folder)
            original = _safe_table(self.root, measurement['path'], folder, storage='Sources')
            if not original.is_file():
                raise FileNotFoundError(f'Canonical measurement is missing: {original}. Restore the project folder or its portable ZIP.')
            before = original.stat()
            current = path.stat() if path.is_file() else None
            if not current or (current.st_size, current.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                path.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    pending = Path(stream.name)
                try:
                    shutil.copy2(original, pending)
                    pending.replace(path)
                finally:
                    pending.unlink(missing_ok=True)
            records[path] = measurement['record']
        # Vault entries remain available for undo even when their active copies are removed.
        archive = self.root / 'Sources'
        if archive.is_dir() and not archive.is_symlink():
            for original in archive.glob('*/*.csv'):
                if original.is_symlink() or original.parent.is_symlink():
                    continue
                folder = original.parent.name
                path = _safe_table(self.root, f'Measurements/{folder}/{original.name}', folder)
                if path not in records and path.is_file():
                    path.unlink()
                    if not any(path.parent.iterdir()):
                        path.parent.rmdir()
        handles = content['handles']
        source_metadata = [dict(id=handle['id'], instrument=handle['instrument'], source_path=handle['source_path'],
                                tags=deepcopy(handle.get('tags', [])), metadata=deepcopy(handle.get('metadata', {})))
                           for handle in handles]
        metadata = dict(project_id=self.project_id, project_name=content['name'],
                        instrument_count=len({record['instrument'] for record in records.values()}), source_count=len(handles),
                        project_handles=source_metadata)
        payload = dict(project_id=self.project_id, csv_entries=list(records.values()), experiment_metadata=metadata)
        try:
            existing = _read_json(manifest)
        except (FileNotFoundError, json.JSONDecodeError):
            existing = None
        if existing != payload:
            _write_json(manifest, payload)
        return BrowserData(self.root, root, tuple(records), records, metadata, manifest_path=manifest)

    def combined_data(self) -> BrowserData:
        with self._lock:
            return self._publish_view(self._content())

    def update_measurement(self, path, display_name=None, tags=None, annotations=None):
        path = Path(path)
        path = path.resolve().relative_to(self.root) if path.is_absolute() and path.resolve().is_relative_to(self.root) else path
        with self._lock:
            content = self._content()
            for handle, measurement in _iter_measurements(content):
                if measurement['path'] != path.as_posix():
                    continue
                record = measurement['record']
                updated = deepcopy(record)
                if display_name is not None:
                    if not str(display_name).strip():
                        raise ValueError('Enter a measurement display name.')
                    updated['display_name'] = str(display_name).strip()
                if tags is not None:
                    updated['tags'] = _tags(tags)
                if annotations is not None:
                    updated['annotations'] = str(annotations)
                if updated != record:
                    measurement['record'] = updated
                    self._commit(content, 'Edit measurement', handle_id=handle['id'], measurement=measurement['path'])
                return
            raise ValueError('Choose a measurement from this project.')

    def update_handle(self, handle_id, *, label=None, instrument=None, tags=None, annotations=None):
        with self._lock:
            content = self._content()
            handle = next((item for item in content['handles'] if item['id'] == handle_id), None)
            if handle is None:
                raise ValueError('Choose a data source from this project.')
            before = deepcopy(handle)
            for field, value in (('label', label), ('instrument', instrument), ('annotations', annotations)):
                if value is not None:
                    value = str(value).strip()
                    if field != 'annotations' and not value:
                        raise ValueError(f'Enter a source {field}.')
                    handle[field] = value
            if tags is not None:
                handle['tags'] = _tags(tags)
            for measurement in handle['measurements']:
                record = measurement['record']
                if before['instrument'] != handle['instrument']:
                    record['instrument'] = handle['instrument']
                if tags is not None:
                    previous = {tag.casefold() for tag in before['tags']}
                    record['tags'] = _tags([*handle['tags'], *(tag for tag in record['tags'] if tag.casefold() not in previous)])
                record['dataset_annotations'] = handle['annotations']
            if before != handle:
                self._commit(content, 'Edit data source', handle_id=handle_id)

    def remove_handle(self, handle_id):
        with self._lock:
            content = self._content()
            content['handles'] = [handle for handle in content['handles'] if handle['id'] != handle_id]
            if len(content['handles']) == len(self.handles):
                raise ValueError('Choose a data source from this project.')
            self._commit(content, 'Remove source', handle_id=handle_id)

    def update_state(self, state, *, name=None):
        if not isinstance(state, dict):
            raise ValueError('Comparison settings must be a JSON object.')
        with self._lock:
            content = self._content()
            content['state'] = _json_copy(state)
            if name is not None:
                content['name'] = str(name).strip() or self.name
            if content != self._content():
                self._commit(content, 'Save comparison settings')

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        with self._lock:
            if not self._undo:
                return False
            self._persist(self._undo[-1], self._undo[:-1], [*self._redo, self._content()],
                          [*self.history, self._event('Undo project edit')])
            return True

    def redo(self):
        with self._lock:
            if not self._redo:
                return False
            self._persist(self._redo[-1], [*self._undo, self._content()][-_UNDO_LIMIT:], self._redo[:-1],
                          [*self.history, self._event('Redo project edit')])
            return True

    @property
    def checkpoint_path(self):
        return self.root / CHECKPOINT_FILE

    def save(self, path=None):
        with self._lock:
            path = Path(path).expanduser().resolve() if path else self.root / PROJECT_FILE
            for handle in self.handles:
                raw = Path(handle['source_path'])
                if raw.is_dir() and path.is_relative_to(raw):
                    raise ValueError('Save the comparison workspace outside the raw source directories.')
            if path.is_file():
                existing = _read_json(path)
                if not isinstance(existing, dict) or existing.get('project_id') != self.project_id:
                    raise ValueError('Choose a new project filename. This path already belongs to another file.')
            payload = self.snapshot()
            if self.root.is_relative_to(path.parent):
                payload['root_relative'] = self.root.relative_to(path.parent).as_posix()
            return _write_json(path, payload)

    @classmethod
    def load(cls, path_or_payload, root=None, replacements=None):
        path = None
        if isinstance(path_or_payload, (str, Path)):
            path = Path(path_or_payload).expanduser().resolve(strict=True)
            if path.suffix.lower() == '.zip':
                return cls._load_portable(path, root=root, replacements=replacements)
            payload = _read_json(path)
        else:
            payload = deepcopy(path_or_payload)
        _validate_snapshot(payload)
        if root is None and path and isinstance(payload.get('root_relative'), str):
            relative = Path(payload['root_relative'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('The project has an unsafe relative location.')
            root = path.parent / relative
        if root is None:
            root = payload.get('root')
        if not root:
            raise ValueError('Choose the folder containing the project’s canonical Measurements directory.')
        project = cls(root, payload.get('name', 'Comparison session'))
        project.project_id = payload['project_id']
        project.handles = payload['handles']
        project.state = payload.get('state', {})
        project.history = payload.get('history', [])
        project._undo = payload.get('undo', [])[-_UNDO_LIMIT:]
        project._redo = payload.get('redo', [])[-_UNDO_LIMIT:]
        replacements = dict(replacements or {})
        if payload.get('root') and Path(payload['root']).expanduser().resolve() != project.root:
            replacements.setdefault(payload['root'], project.root)
        mapping = _mapping(replacements)
        for content in (payload, *project._undo, *project._redo):
            _migrate_curve_settings(content, payload.get('root'))
            _replace_content(content, mapping)
            for handle, measurement in _iter_measurements(content):
                canonical = _safe_table(project.root, measurement['path'], handle['folder'], storage='Sources')
                if not canonical.is_file():
                    raise FileNotFoundError(f'Canonical measurement is missing: {canonical}. Choose the relocated project folder or its portable ZIP.')
        project.state = payload.get('state', {})
        return project

    def _all_measurements(self):
        for content in (self._content(), *self._undo, *self._redo):
            yield from _iter_measurements(content)

    def relocate(self, mapping, *, state=None):
        """Repoint raw provenance, and copy canonical contents if the project root changes."""
        if state is not None and not isinstance(state, dict):
            raise ValueError('Comparison settings must be a JSON object.')
        replacements = _mapping(mapping)
        if not replacements:
            return self.root
        with self._lock:
            previous_root = self.root
            next_root = Path(_replace_path(str(previous_root), replacements)).resolve()
            content, undo, redo = self._content(), deepcopy(self._undo), deepcopy(self._redo)
            if state is not None:
                content['state'] = _json_copy(state)
            for snapshot in (content, *undo, *redo):
                _migrate_curve_settings(snapshot, previous_root)
                _replace_content(snapshot, replacements)
            if next_root != previous_root:
                if next_root.is_relative_to(previous_root) or previous_root.is_relative_to(next_root):
                    raise ValueError('Choose a project destination outside the current project folder.')
                if next_root.exists() and (not next_root.is_dir() or any(next_root.iterdir())):
                    raise ValueError('Choose a new or empty destination folder for the relocated project.')
                for handle, measurement in self._all_measurements():
                    raw = Path(handle['source_path'])
                    relocated_raw = Path(_replace_path(handle['source_path'], replacements))
                    if any(path.is_dir() and next_root.is_relative_to(path) for path in (raw, relocated_raw)):
                        raise ValueError('Choose a project destination outside the raw source directories.')
                next_root.parent.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(prefix='relocate-', dir=next_root.parent) as temporary:
                    stage = Path(temporary) / 'project'
                    stage.mkdir()
                    for handle, measurement in self._all_measurements():
                        source = _safe_table(previous_root, measurement['path'], handle['folder'], storage='Sources')
                        target = _safe_table(stage, measurement['path'], handle['folder'], storage='Sources')
                        if not target.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source, target)
                    history = [*self.history, self._event('Relocate project', destination=str(next_root))]
                    payload = dict(schema=PROJECT_SCHEMA, project_id=self.project_id, root=str(next_root), root_relative='.',
                                   **content, undo=undo, redo=redo, history=history)
                    _write_json(stage / PROJECT_FILE, payload)
                    _write_json(stage / CHECKPOINT_FILE, payload)
                    if next_root.exists():
                        next_root.rmdir()
                    stage.replace(next_root)
                self.root = next_root
                self.name, self.handles, self.state = content['name'], content['handles'], content['state']
                self._undo, self._redo, self.history = undo, redo, history
                self._publish_view(content)
            elif content != self._content() or undo != self._undo or redo != self._redo:
                self._persist(content, undo, redo, [*self.history, self._event('Relocate source paths')])
        return self.root

    def export_portable(self, destination, selected=None, *, state=None):
        """Package only the selected canonical sources and their catalog in a relocatable ZIP."""
        if state is not None and not isinstance(state, dict):
            raise ValueError('Comparison settings must be a JSON object.')
        with self._lock:
            destination = Path(destination).expanduser().resolve()
            if destination.exists():
                raise ValueError('Choose a new filename for the portable project ZIP.')
            if destination.suffix.lower() != '.zip':
                raise ValueError('Choose a .zip filename for the portable project.')
            choices = set(selected) if selected is not None else {handle['id'] for handle in self.handles}
            if not choices or choices - {handle['id'] for handle in self.handles}:
                raise ValueError('Select one or more sources from this project.')
            for handle in self.handles:
                raw = Path(handle['source_path'])
                if raw.is_dir() and destination.is_relative_to(raw):
                    raise ValueError('Save the portable package outside the raw source directories.')
            handles = [handle for handle in self._content()['handles'] if handle['id'] in choices]
            settings = self.state if state is None else state
            payload = dict(schema=PROJECT_SCHEMA, project_id=uuid4().hex, name=self.name, root=str(self.root), root_relative='.',
                           handles=handles, state=_json_copy(settings) if len(handles) == len(self.handles) else {},
                           history=[self._event('Create portable project', source_count=len(handles))])
            destination.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix='portable-', dir=destination.parent) as temporary:
                staged = Path(temporary) / destination.name
                with ZipFile(staged, 'w', compression=ZIP_DEFLATED) as archive:
                    archive.writestr(PROJECT_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
                    for handle, measurement in _iter_measurements(payload):
                        source = _safe_table(self.root, measurement['path'], handle['folder'], storage='Sources')
                        archive.write(source, measurement['path'])
                # O_EXCL protects existing files even if another writer arrives after validation.
                with destination.open('xb') as output, staged.open('rb') as stream:
                    try:
                        shutil.copyfileobj(stream, output)
                    except BaseException:
                        output.close()
                        destination.unlink(missing_ok=True)
                        raise
            return destination

    @classmethod
    def _load_portable(cls, source, *, root=None, replacements=None):
        destination = Path(root).expanduser().resolve() if root else source.with_name(source.stem + ' project')
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise ValueError('Choose a new or empty folder to open the portable project.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(source) as archive, TemporaryDirectory(prefix='open-project-', dir=destination.parent) as temporary:
            stage = Path(temporary) / 'project'
            stage.mkdir()
            members = {}
            for info in archive.infolist():
                canonical = '/'.join(_portable_parts(info.filename))
                if (canonical != info.filename or canonical in members or info.is_dir()
                        or stat.S_ISLNK(info.external_attr >> 16)):
                    raise ValueError(f'Unsafe portable project member: {info.filename}')
                members[canonical] = info
            if PROJECT_FILE not in members:
                raise ValueError('The portable ZIP does not contain a project manifest.')
            payload = json.loads(archive.read(PROJECT_FILE))
            _validate_snapshot(payload)
            if payload.get('undo') or payload.get('redo'):
                raise ValueError('Portable project manifests must contain only the current selection.')
            expected = {PROJECT_FILE}
            files = {}
            for handle, measurement in _iter_measurements(payload):
                path = _safe_table(stage, measurement['path'], handle['folder'])
                expected.add(path.relative_to(stage).as_posix())
                files[measurement['path']] = _safe_table(stage, measurement['path'], handle['folder'], storage='Sources')
            if expected != members.keys():
                raise ValueError('Portable ZIP members do not match the declared measurements.')
            for name in sorted(expected - {PROJECT_FILE}):
                path = files[name]
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(members[name]) as stream, path.open('xb') as output:
                    shutil.copyfileobj(stream, output)
            mapping = dict(replacements or {})
            if payload.get('root'):
                mapping.setdefault(payload['root'], destination)
                _migrate_curve_settings(payload, payload['root'])
                _replace_content(payload, _mapping(mapping))
            payload['root'] = str(stage)
            # Validate at the temporary location, then save the final location in the manifest.
            project = cls.load(payload, root=stage)
            _write_json(stage / PROJECT_FILE, dict(project.snapshot(), root=str(destination), root_relative='.'))
            if destination.exists():
                destination.rmdir()
            stage.replace(destination)
            project.root = destination
            project._publish_view(project._content())
        return project

    def search(self, query=''):
        tokens = str(query).casefold().split()
        with self._lock:
            rows = []
            for handle, measurement in _iter_measurements(self._content()):
                record = measurement['record']
                row = dict(project=self.name, project_root=str(self.root), source=handle['label'],
                           instrument=record['instrument'], original_source=handle['source_path'],
                           measurement=record.get('display_name', ''), technique=record.get('technique', ''),
                           tags=', '.join(_tags([*handle.get('tags', []), *_tags(record.get('tags'))])),
                           annotations=' '.join(filter(None, (handle.get('annotations'), record.get('annotations')))),
                           table=str(self.root / measurement['path']))
                searchable = ' '.join(map(str, row.values())).casefold()
                if all(token in searchable for token in tokens):
                    rows.append(row)
            return rows


def search_projects(folder, query='', *, current=None):
    folder = Path(folder).expanduser().resolve()
    paths = sorted(folder.rglob(PROJECT_FILE)) + sorted(folder.rglob(CHECKPOINT_FILE))
    # A checkpoint reflects the latest completed edit for its own project.
    active = {}
    for path in paths:
        if not path.resolve().is_relative_to(folder):
            continue
        try:
            project = MultiInstrumentProject.load(path)
        except (OSError, ValueError):
            continue
        active[(project.project_id, project.root)] = project
    if current is not None:
        active[(current.project_id, current.root)] = current
    return [row for project in active.values() for row in project.search(query)]


@dataclass(frozen=True)
class ImportRequest:
    source: str | Path
    label: str = ''
    instrument: str = ''
    profile: ImportProfile | None = None
    tags: str = ''
    signature: tuple | None = None
    allow_duplicate: bool = False


class ImportQueue:
    """A job's per-source progress and prepared results, independent of Streamlit."""

    def __init__(self, project, requests, workspace):
        self.project = project
        self.requests = tuple(requests)
        self.workspace = workspace
        self._lock = threading.Lock()
        self._statuses = [dict(source=str(row.source), status='Waiting', detail='', handle_id='') for row in self.requests]

    def status(self):
        with self._lock:
            return deepcopy(self._statuses)

    def _update(self, index, status, detail='', handle_id=''):
        with self._lock:
            self._statuses[index].update(status=status, detail=detail, handle_id=handle_id)

    def run(self, cancel, report):
        handles = []
        for index, row in enumerate(self.requests):
            if cancel.is_set():
                self._update(index, 'Canceled', 'Stopped before this source started.')
                continue
            self._update(index, 'Importing')
            report(f'{index + 1} of {len(self.requests)} · {Path(row.source).name}')
            try:
                handle = self.project.prepare_source(row.source, workspace=self.workspace, label=row.label,
                                                     instrument=row.instrument, profile=row.profile, tags=row.tags,
                                                     expected_signature=row.signature, allow_duplicate=row.allow_duplicate)
            except DuplicateSourceError as error:
                self._update(index, 'Duplicate', str(error))
            except IncompleteSourceError as error:
                self._update(index, 'Incomplete', str(error))
            except Exception as error:
                self._update(index, 'Failed', str(error))
            else:
                handles.append(handle)
                self._update(index, 'Prepared', handle_id=handle['id'])
        return handles

    def apply(self, handles):
        outcomes = self.project.apply_prepared(handles)
        committed = {handle['id']: handle for handle in self.project.handles}
        for index, row in enumerate(self.status()):
            if row['handle_id'] in outcomes:
                status, detail = outcomes[row['handle_id']]
                if status == 'Imported':
                    existing = committed.get(committed[detail].get('duplicate_of'))
                    detail = f'Identical content to {existing["label"]!r}. Imported as an independent source.' if existing else ''
                self._update(index, status, detail, row['handle_id'])
        return sum(status == 'Imported' for status, _ in outcomes.values())
