"""Open eStudio inputs and path-based sessions in the local browser workspace."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
from tempfile import NamedTemporaryFile, TemporaryDirectory, gettempdir
from typing import Any, BinaryIO, Iterable, Mapping
from zipfile import ZipFile

from .readable_names import new_folder, safe_component

TABLE_EXTENSIONS = frozenset({'.csv', '.tsv', '.txt', '.asc', '.mpt', '.xls', '.xlsx'})
INSTRUMENT_EXTENSIONS = frozenset({'.pssession', '.dat', '.pul', '.pgf', '.amp', '.mrk', '.mth', '.onl',
                                   '.par', '.psbd', '.cor', '.z', '.mpr', '.mps', '.mgr'})
BUNDLE_EXTENSIONS = frozenset({'.h5', '.hdf5', '.npy', '.npz'})
_HDF5_EXTENSIONS = frozenset({'.h5', '.hdf5'})
_DOCUMENT_EXTENSIONS = TABLE_EXTENSIONS | {'.json', '.jsonl', '.md', '.log'}
_COPY_BLOCK_SIZE = 8 * 1024 * 1024
_DESKTOP_SCHEMA = 'faradaiq_main_session_v199'
_SESSION_SCHEMAS = {_DESKTOP_SCHEMA, 'faradaiq_main_session_v106'}
_PORTABLE_PATH_LIMIT = 235
_IMPORT_MANIFEST = 'analysis_ready_manifest.json'
_ARCHIVE_CONTENTS = 'raw_export_siblings'
_DESKTOP_SCRIPT_COPY = 'pyseccm_tools_scripts'


def _mapping(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_mapping(value: Any) -> dict:
    try:
        return _mapping(json.loads(value))
    except (UnicodeError, ValueError, TypeError):
        return {}


def _record_path(root: Path, value: Any) -> Path | None:
    if not value:
        return None
    path = (root / str(value).replace('\\', '/')).resolve()
    return path if path.is_relative_to(root) else None


@dataclass(frozen=True)
class BrowserAsset:
    path: Path
    kind: str
    title: str
    hdf5_dataset: str = ''


@dataclass(frozen=True)
class BrowserData:
    source: Path
    analysis_root: Path
    tables: tuple[Path, ...]
    records: dict[Path, dict[str, Any]]
    metadata: dict[str, Any]
    manifest_path: Path | None = None
    bundle_path: Path | None = None
    bundle_manifest: dict[str, Any] = field(default_factory=dict)
    assets: tuple[BrowserAsset, ...] = ()

    def read_table(self, path: str | Path, *, max_rows: int | None = None):
        """Use the desktop reader and preserve explicitly declared headerless axes."""
        import pandas as pd
        from faradaiq_canonical_bundle import _desktop_axis_columns, _read_biologic_ascii
        from .multidim_v216 import _read_table
        from .v290_portable_browser import _technique_key

        path = Path(path).expanduser().resolve()
        if path not in self.records:
            raise ValueError('Select a table from the current data source.')
        record = self.records[path]
        if self.bundle_path and record.get('hdf5_dataset') and self.bundle_path.is_file():
            return _read_bundle_table(self.bundle_path, record, max_rows=max_rows)
        context = dict(record, technique_key=_technique_key(record.get('technique'), path=path))
        if path.suffix.lower() == '.mpt':
            frame = _read_biologic_ascii(path, max_rows=max_rows)
            if frame is not None:
                return frame

        frame = _read_table(path, context, max_rows=max_rows)
        labels = _desktop_axis_columns(record)
        if labels and path.suffix.lower() in {'.csv', '.tsv', '.txt', '.asc'} and len(frame.columns) == 2:
            options = dict(sep=None, engine='python', comment='#', header=None)
            first = pd.read_csv(path, nrows=1, **options)
            numeric = pd.to_numeric(first.iloc[0], errors='coerce')
            if (numeric.notna() | first.iloc[0].isna()).all():
                frame = pd.read_csv(path, names=labels, nrows=max_rows, **options)
        return frame


def _portable_parts(name: str) -> tuple[str, ...]:
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or PureWindowsPath(name).drive or '..' in path.parts
            or any(PureWindowsPath(part).is_reserved() or part.rstrip(' .') != part
                   or re.search(r'[<>:"|?*\x00-\x1f]', part) for part in path.parts)):
        raise ValueError(f'Unsafe file path: {name}')
    return path.parts


def _path_units(value: str | Path) -> int:
    return len(str(value).encode('utf-16-le')) // 2


def _compact_component(label: str, budget: int, *, byte_limit: int = 255) -> str:
    for length in range(min(len(label), budget), 0, -1):
        name = safe_component(label, max_chars=length)
        if _path_units(name) <= budget and len(name.encode('utf-8')) <= byte_limit:
            return name
    raise ValueError(f'The file name {label!r} has no readable portable form.')


def _workspace_folder(parent: Path, label: str, *, descendants: int,
                      preferred: int | None = None) -> Path:
    """Create an import directory with enough room for its actual descendants."""
    label = safe_component(label, max_chars=70)
    available = _PORTABLE_PATH_LIMIT - _path_units(parent) - 1
    minimum = min(12, _path_units(label))
    for index in range(1, 10000):
        serial = '' if index == 1 else f'_{index:02d}'
        budget = available - len(serial) - descendants
        if budget < minimum:
            raise ValueError(f'The selected output folder is too deep for this data import.\n{parent}\n'
                             'In Settings → Local output folder, choose a shorter path such as '
                             'C:\\Data\\FaradaIQ on Windows or ~/FaradaIQ on macOS or Linux, then open the data again.')
        if preferred is not None:
            budget = min(budget, max(minimum, available - len(serial) - preferred))
        folder = parent / f'{_compact_component(label, budget, byte_limit=255 - len(serial))}{serial}'
        try:
            folder.mkdir(parents=True)
        except FileExistsError:
            continue
        return folder
    raise FileExistsError(f'Too many imports named {label!r} in {parent}.')


def stage_browser_uploads(files: Iterable[BinaryIO], workspace: str | Path) -> Path:
    """Copy browser uploads locally, keeping instrument companions and subfolders together."""
    members = []
    names: dict[tuple[str, ...], tuple[str, ...]] = {}
    leaves: set[tuple[str, ...]] = set()
    for upload in files:
        parts = _portable_parts(str(upload.name))
        if parts[0] == '__MACOSX' or parts[-1] in {'.DS_Store', 'Thumbs.db'}:
            continue
        for depth in range(1, len(parts) + 1):
            prefix = parts[:depth]
            folded = tuple(segment.casefold() for segment in prefix)
            if folded in leaves or names.setdefault(folded, prefix) != prefix:
                raise ValueError(f'Uploads contain conflicting paths: {upload.name}')
        leaves.add(folded)
        members.append((parts, upload))
    if not members:
        raise ValueError('Choose one or more local data files.')
    if any(any(key[:depth] in leaves for depth in range(1, len(key))) for key in leaves):
        raise ValueError('An uploaded file has the same path as an uploaded folder.')
    common_top = {parts[0] for parts, _ in members}
    descendants = max(_path_units(Path(*parts)) + 1 for parts, _ in members)
    folder = _workspace_folder(Path(workspace).expanduser().resolve(), 'Browser uploads', descendants=descendants)
    try:
        for parts, upload in members:
            target = folder.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            upload.seek(0)
            with target.open('xb') as output:
                shutil.copyfileobj(upload, output, length=_COPY_BLOCK_SIZE)
        if len(members) == 1:
            return folder.joinpath(*members[0][0])
        if len(common_top) == 1:
            return folder / next(iter(common_top))
        return folder
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _table_paths(folder: Path) -> tuple[Path, ...]:
    from faradaiq_canonical_bundle import EXCLUDED_DIR_TOKENS

    return tuple(sorted({item.resolve() for item in folder.rglob('*')
                         if item.suffix.lower() in TABLE_EXTENSIONS and item.is_file()
                         and not item.name.lower().startswith('readme')
                         and item.resolve().is_relative_to(folder)
                         and not any(part.casefold() in EXCLUDED_DIR_TOKENS or part.startswith('.')
                                     for part in item.relative_to(folder).parts[:-1])}))


def classify_source(path: str | Path) -> str:
    """Identify an eStudio input supported by the local import step."""
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f'Data source does not exist: {source}')
    if source.is_dir():
        # A known export tree is already curated. Prefer its explicit title over
        # auxiliary binary files copied into the export by desktop tools.
        if source.name.lower().endswith(('_analysis_ready_csv', '_for_plotting_analysis')):
            return 'tables'
        browser_manifest = source / _IMPORT_MANIFEST
        if browser_manifest.is_file() and _json_mapping(browser_manifest.read_bytes()).get('browser_import'):
            return 'tables'
        if any(p.is_file() and p.suffix.lower() in INSTRUMENT_EXTENSIONS
               for p in source.rglob('*')):
            return 'instrument'
        if _table_paths(source):
            return 'tables'
        if any(p.is_file() and p.suffix.lower() in _HDF5_EXTENSIONS for p in source.iterdir()):
            return 'bundle'
        raise ValueError(f'No supported eStudio data files found in {source}.')
    extension = source.suffix.lower()
    if extension in TABLE_EXTENSIONS:
        return 'tables'
    if extension in INSTRUMENT_EXTENSIONS:
        return 'instrument'
    if extension in BUNDLE_EXTENSIONS:
        return 'bundle'
    if extension == '.zip':
        return 'archive'
    raise ValueError(f'Unsupported data file type: {source.name}')


def _build_dataset(source: Path, root: Path, manifest_path: Path | None = None,
                   metadata: Mapping[str, Any] | None = None) -> BrowserData:
    if manifest_path is None:
        base = root.name.removesuffix('_analysis_ready_csv')
        candidates = [root.parent / f'{base}_analysis_ready_manifest.json',
                      root / f'{base}_analysis_ready_manifest.json',
                      root / 'analysis_ready_manifest.json']
        manifest_path = next((path for path in candidates if path.is_file()), None)
    manifest = _json_mapping(manifest_path.read_text(encoding='utf-8-sig')) if manifest_path else {}
    indexed = {}
    for record in manifest.get('csv_entries') or manifest.get('records') or []:
        if not isinstance(record, dict):
            continue
        path = _record_path(root, record.get('relative_csv_path') or record.get('relative_path'))
        if path:
            indexed[path] = record
    tables = _table_paths(root)
    if not tables:
        raise ValueError(f'No readable numeric tables were exported for {source.name}.')
    # Preserve manifest acquisition order. Unindexed extras keep a stable path order.
    table_set = set(tables)
    ordered = tuple(path for path in indexed if path in table_set) + tuple(p for p in tables if p not in indexed)
    bundle = _mapping(manifest.get('browser_bundle'))
    bundle_path = Path(bundle['source']).resolve() if isinstance(bundle.get('source'), str) else None
    if bundle_path and bundle_path.suffix.lower() not in _HDF5_EXTENSIONS:
        bundle_path = None
    asset_records = []
    for item in bundle.get('assets', []):
        if not isinstance(item, dict):
            continue
        path = _record_path(root, item.get('path'))
        if path and path.is_file():
            asset_records.append(BrowserAsset(path, str(item.get('kind', 'Other')),
                                             str(item.get('title') or path.name),
                                             str(item.get('hdf5_dataset') or '')))
    return BrowserData(source, root, ordered, {p: indexed.get(p, {}) for p in ordered},
                       _mapping(manifest.get('experiment_metadata')) | _mapping(metadata), manifest_path,
                       bundle_path, _mapping(bundle.get('manifest')), tuple(asset_records))


def _unpack_archive(source: Path, destination: Path) -> Path:
    with ZipFile(source, 'r') as archive:
        seen = set()
        for member in archive.infolist():
            name = member.filename.replace('\\', '/')
            try:
                parts = _portable_parts(name)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError('Symbolic links are unsupported.')
            except ValueError as error:
                raise ValueError(f'Found an unsafe ZIP member: {member.filename}') from error
            if parts[0] == '__MACOSX' or parts[-1] in {'.DS_Store', 'Thumbs.db'}:
                continue
            out = destination.joinpath(*parts)
            if member.is_dir() or name.endswith('/'):
                out.mkdir(parents=True, exist_ok=True)
                continue
            key = tuple(part.casefold() for part in parts)
            if key in seen:
                raise ValueError(f'ZIP contains duplicate file names: {member.filename}')
            seen.add(key)
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as input_stream, out.open('xb') as output_stream:
                shutil.copyfileobj(input_stream, output_stream)
    contents = list(destination.iterdir()) if destination.is_dir() else []
    if len(contents) == 1:
        return contents[0]
    return destination


def _local_group(container, key):
    import h5py

    if isinstance(container.get(key, getlink=True), h5py.HardLink):
        item = container[key]
        if isinstance(item, h5py.Group):
            return item
    return None


def _local_dataset(container, key):
    import h5py

    if isinstance(container.get(key, getlink=True), h5py.HardLink):
        item = container[key]
        if isinstance(item, h5py.Dataset) and not item.is_virtual and not item.external:
            return item
    return None


def _read_bundle_table(source: Path, record: Mapping[str, Any], *, max_rows):
    import h5py
    import pandas as pd

    path = str(record['hdf5_dataset']).split('/')
    if len(path) != 3 or path[:2] != ['', 'tables']:
        raise ValueError('Select an internal canonical HDF5 table.')
    with h5py.File(source, 'r') as h5:
        tables = _local_group(h5, 'tables')
        item = _local_dataset(tables, path[2]) if tables is not None else None
        if item is None or item.ndim != 2 or len(record.get('columns', [])) != item.shape[1]:
            raise ValueError('The original HDF5 table changed. Reopen the bundle to refresh the catalog.')
        return pd.DataFrame(item[:max_rows], columns=record['columns'])


def _bundle_manifest(source: Path, bundle) -> dict:
    item = _local_dataset(bundle, 'manifest_json')
    manifest = _json_mapping(item[()]) if item is not None and item.ndim == 0 else {}
    if not manifest:
        sidecar = source.with_suffix('.json')
        if sidecar.is_file():
            manifest = _json_mapping(sidecar.read_text(encoding='utf-8-sig', errors='replace'))
    return manifest


def _bundle_context(manifest: Mapping[str, Any]) -> dict:
    excluded = {'gui_metadata', 'supporting_json_metadata', 'scilink_ready', 'data_structure',
                'experiment_structure_from_dat', 'validation', 'source_policy', 'browser_cache'}
    context = {key: value for key, value in manifest.items() if key not in excluded and not isinstance(value, (dict, list))}
    return context | _mapping(manifest.get('gui_metadata')) | _mapping(
        _mapping(manifest.get('scilink_ready')).get('experimental_metadata'))


def _bundle_asset_records(h5, root: Path) -> list[dict]:
    from faradaiq_canonical_bundle import MEDIA_EXTS

    assets = []
    groups = (('media', 'Media', MEDIA_EXTS), ('metadata', 'Metadata', _DOCUMENT_EXTENSIONS),
              ('source_documents', 'Original source', _DOCUMENT_EXTENSIONS))
    for group, kind, extensions in groups:
        entries = _local_group(h5, group)
        if entries is None:
            continue
        for key in entries:
            item = _local_dataset(entries, key)
            if item is None:
                continue
            rec = _json_mapping(item.attrs.get('record_json', '{}'))
            title = str(rec.get('name') or item.attrs.get('filename') or key)
            name = safe_component(PurePosixPath(title.replace('\\', '/')).name, max_chars=75, default='document')
            if Path(name).suffix.lower() not in extensions:
                if kind == 'Media':
                    suffix = str(rec.get('suffix', '')).lower()
                    if suffix not in MEDIA_EXTS:
                        continue
                    name = f'{name}{suffix}'
                else:
                    name = f'{name}.json' if item.ndim == 0 and kind == 'Metadata' else f'{name}.bin'
            output = root / 'metadata' / group / f'{len(assets) + 1:05d}_{name}'
            if item.ndim == 1 and item.dtype.kind == 'u' and item.dtype.itemsize == 1:
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open('xb') as handle:
                    for start in range(0, item.size, _COPY_BLOCK_SIZE):
                        handle.write(item[start:start + _COPY_BLOCK_SIZE].tobytes())
            elif item.ndim == 0 and item.dtype.kind in {'S', 'O', 'U'} and kind != 'Media':
                raw = item[()]
                if not isinstance(raw, (str, bytes)):
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(raw.encode('utf-8') if isinstance(raw, str) else raw)
            else:
                continue
            assets.append({'path': output.relative_to(root).as_posix(), 'kind': kind,
                           'title': title, 'hdf5_dataset': item.name})
    return assets


def _canonical_hdf5(source: Path, root: Path) -> tuple[Path, dict]:
    import h5py
    import pandas as pd
    from .scilink_bundle import column_unit

    manifest_path = root / 'analysis_ready_manifest.json'
    records = []
    with h5py.File(source, 'r') as h5:
        tables = _local_group(h5, 'tables')
        if tables is None:
            raise ValueError('Choose an eStudio canonical HDF5 bundle containing a /tables group.')
        manifest = _bundle_manifest(source, h5)
        for key in tables:
            item = _local_dataset(tables, key)
            if item is None or item.ndim != 2 or item.shape[1] < 2:
                continue
            rec = _json_mapping(item.attrs.get('record_json', '{}'))
            for attr in ('source_path', 'relative_path', 'technique', 'role'):
                if attr not in rec and attr in item.attrs:
                    rec[attr] = str(item.attrs[attr])
            try:
                columns = json.loads(item.attrs.get('columns_json', '[]'))
            except (TypeError, ValueError):
                columns = []
            if not isinstance(columns, list) or len(columns) != item.shape[1]:
                columns = [f'col_{index + 1}' for index in range(item.shape[1])]
            columns = [str(column) for column in columns]
            units = _json_mapping(item.attrs.get('units_json', '{}'))
            rec['units'] = {col: units.get(col, column_unit(col)) for col in columns}
            technique = safe_component(rec.get('technique') or 'Other', max_chars=38)
            name = safe_component(Path(str(rec.get('name') or key)).stem, max_chars=72)
            folder = root / technique
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / f'{len(records) + 1:04d}_{name}.csv'
            with output.open('w', encoding='utf-8', newline='') as handle:
                pd.DataFrame(columns=columns).to_csv(handle, index=False)
                for start in range(0, item.shape[0], 100_000):
                    pd.DataFrame(item[start:start + 100_000], columns=columns).to_csv(handle, index=False, header=False)
            records.append(dict(rec, relative_csv_path=output.relative_to(root).as_posix(), columns=columns,
                                hdf5_dataset=item.name, n_points=item.shape[0]))
        assets = _bundle_asset_records(h5, root)
    metadata = _bundle_context(manifest)
    record = {'csv_entries': records, 'experiment_metadata': metadata,
              'browser_bundle': {'source': str(source), 'manifest': manifest, 'assets': assets}}
    manifest_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest_path, metadata


def _numeric_numpy(source: Path, root: Path) -> None:
    import numpy as np
    import pandas as pd

    def write_table(name, array):
        if array.ndim == 2 and array.shape[1] >= 2:
            pd.DataFrame(array).to_csv(root / f'{safe_component(name)}.csv', index=False,
                                       header=[f'col_{index + 1}' for index in range(array.shape[1])])
        elif array.dtype.names:
            pd.DataFrame.from_records(array).to_csv(root / f'{safe_component(name)}.csv', index=False)

    root.mkdir(parents=True, exist_ok=True)
    try:
        if source.suffix.lower() == '.npz':
            with np.load(source, allow_pickle=False) as archive:
                for index, name in enumerate(archive.files, 1):
                    write_table(f'{index:04d}_{name}', archive[name])
        else:
            write_table(source.stem, np.load(source, allow_pickle=False))
    except ValueError as exc:
        if 'Object arrays' in str(exc):
            raise ValueError('This NumPy file contains Python objects. Export its tables as CSV or canonical HDF5 in eStudio before opening in the local browser.') from exc
        raise


def _native_vendor(source: Path, destination: Path, description: str) -> tuple[Path, Path, dict, dict]:
    from .parsers.pssession import PSSessionCollection, PSSessionParser

    if source.suffix.lower() == '.psbd':
        stem = source.name[:-5]
        if stem.lower().endswith('.par'):
            stem = stem[:-4]
        source = next((item for item in source.parent.iterdir() if item.name.casefold() == (stem + '.par').casefold()),
                      source.with_name(stem + '.par'))
        if not source.is_file():
            raise ValueError('Select the VersaStudio .par file alongside its .psbd companion.')
    collection = PSSessionCollection().load(str(source))
    metadata = collection.summarize()
    if description:
        metadata['experimental_description'] = description
    # The desktop writer uses basename for generated paths and relative_path for
    # source identity. Keep the latter and give each generated stem room for its
    # technique, curve and vendor suffixes before the legacy writer appends them.
    identities = {}
    stems_by_folder: dict[tuple[str, ...], set[str]] = {}
    for index, entry in enumerate(collection.entries, 1):
        context = entry['path_metadata']
        basename = Path(context['basename'])
        original = PSSessionParser._sanitize_filename(basename.stem)
        used = stems_by_folder.setdefault(tuple(p.casefold() for p in context['subfolder_names']), set())
        stem = _compact_component(original, 24)
        serial = 1
        while stem.casefold() in used:
            serial += 1
            ending = f'_{serial}'
            stem = f'{_compact_component(original, 24 - len(ending))}{ending}'
        used.add(stem.casefold())
        identities[f'e{index:04d}'] = {'source_path': context['virtual_source'],
                                     'source_filename': basename.name}
        entry['path_metadata'] = dict(context, basename=stem + basename.suffix)
    output = collection.export_analysis_ready_csv(destination, 'Analysis')
    return Path(output['analysis_ready_root']), Path(output['manifest_path']), metadata, identities


def _import_tempdir(workspace: Path, source: Path) -> TemporaryDirectory:
    temporary_root = Path(gettempdir()).resolve()
    if source.is_dir() and temporary_root.is_relative_to(source):
        workspace.mkdir(parents=True, exist_ok=True)
        temporary_root = workspace
    # Windows legacy readers append vendor paths before we can budget the final
    # names. Extended paths are limited to this disposable staging directory.
    if os.name == 'nt':
        path = str(temporary_root)
        path = '\\\\?\\UNC\\' + path[2:] if path.startswith('\\\\') else '\\\\?\\' + path
        temporary_root = Path(path)
    return TemporaryDirectory(prefix='fq-', dir=temporary_root)


def _generated_tail(path: Path) -> str:
    if path.name.endswith('_freq_zreal_zimag.csv'):
        return '_freq_zreal_zimag.csv'
    return path.suffix


def _generated_paths(root: Path, files: list[Path], directories: list[Path]) -> dict[Path, Path]:
    """Keep hierarchy names and shorten individual generated files only as needed."""
    occupied = {str(path).casefold() for path in directories}
    occupied.update({_IMPORT_MANIFEST.casefold(), _ARCHIVE_CONTENTS.casefold()})
    paths = {}
    for relative in files:
        if (_path_units(root / relative) <= _PORTABLE_PATH_LIMIT
                and len(relative.name.encode('utf-8')) <= 255
                and str(relative).casefold() not in occupied):
            paths[relative] = relative
            occupied.add(str(relative).casefold())
    for relative in files:
        if relative in paths:
            continue
        tail = _generated_tail(relative)
        stem = relative.name[:-len(tail)] if tail else relative.name
        budget = _PORTABLE_PATH_LIMIT - _path_units(root / relative.parent) - 1 - _path_units(tail)
        for index in range(1, 10000):
            serial = '' if index == 1 else f'_{index:02d}'
            name = _compact_component(stem, budget - len(serial), byte_limit=255 - len((serial + tail).encode('utf-8')))
            target = relative.with_name(f'{name}{serial}{tail}')
            if str(target).casefold() not in occupied:
                occupied.add(str(target).casefold())
                paths[relative] = target
                break
        else:
            raise FileExistsError(f'Too many generated files named {relative.name!r}.')
    return paths


def _rebase_import_json(value, old_root: Path, root: Path, paths: Mapping[Path, Path],
                        archive: Path | None, *, key: str = '', relative_base: Path = Path()):
    if isinstance(value, dict):
        return {k: _rebase_import_json(v, old_root, root, paths, archive, key=k, relative_base=relative_base)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_rebase_import_json(v, old_root, root, paths, archive, key=key, relative_base=relative_base)
                for v in value if not (key == 'traditional_folders' and v == _DESKTOP_SCRIPT_COPY)]
    if not isinstance(value, str) or not value:
        return value
    path = Path(value.replace('\\', '/'))
    if path.is_absolute():
        if path.is_relative_to(old_root):
            relative = path.relative_to(old_root)
            if key == 'script_bundle_copy' and relative.name == _DESKTOP_SCRIPT_COPY:
                return ''
            return str(root / paths.get(relative, relative))
        if archive is not None and path.is_relative_to(archive):
            return str(root / _ARCHIVE_CONTENTS / path.relative_to(archive))
    elif key in {'relative_csv_path', 'relative_drt_path', 'relative_path'}:
        if path in paths:
            return paths[path].as_posix()
        if relative_base / path in paths:
            return paths[relative_base / path].relative_to(relative_base).as_posix()
    return value


def _open_native(source: Path, active: Path, workspace: Path, staging: Path,
                 archive: Path | None, description: str) -> BrowserData:
    from faradaiq_canonical_bundle import EXCLUDED_DIR_TOKENS
    from .v273_short_names import alias_entry

    stage_root, stage_manifest, metadata, identities = _native_vendor(active, staging, description)
    manifest = _json_mapping(stage_manifest.read_text(encoding='utf-8'))
    for record in manifest.get('csv_entries', []):
        record.update(identities.get(record.get('entry_id'), {}))
    files, directories = [], []
    for path in sorted(stage_root.rglob('*')):
        relative = path.relative_to(stage_root)
        # Desktop exports may embed another copy of the installed pySECCM tools.
        # The browser runs its installed tools and only needs their scientific inputs.
        if _DESKTOP_SCRIPT_COPY in relative.parts or relative.name == 'Run_all_traditional_pySECCM_parsers.bat':
            continue
        if path.is_file():
            files.append(relative)
        elif path.is_dir():
            directories.append(relative)
    fixed = [_path_units(_IMPORT_MANIFEST) + 1, _path_units('name_mapping.json') + 1]
    fixed.extend(_path_units(p) + 1 for p in directories)
    if archive is not None:
        fixed.extend(_path_units(Path(_ARCHIVE_CONTENTS) / p.relative_to(archive)) + 1 for p in archive.rglob('*'))
    minimum = max([*fixed, *(_path_units(p.parent / 'x')
                            + min(_path_units(p.name), 12 + _path_units(_generated_tail(p))) for p in files)])
    preferred = max([*fixed, *(_path_units(p) + 1 for p in files)])
    label = source.name if source.is_dir() else source.stem
    folder_label = safe_component(label)
    if folder_label.casefold() in EXCLUDED_DIR_TOKENS or folder_label.startswith('.'):
        folder_label += ' analysis'
    root = _workspace_folder(workspace, folder_label, descendants=minimum, preferred=preferred)
    try:
        paths = _generated_paths(root, files, directories)
        path_aliases = {old.as_posix(): new.as_posix() for old, new in paths.items() if old != new}
        aliases = [alias_entry(label, root.name, kind='physical_output_base', context='browser_import')]
        aliases.extend(alias_entry(old, new, kind='generated_path', context='browser_import')
                       for old, new in path_aliases.items())
        aliases = [alias for alias in aliases if alias]
        if archive is not None:
            shutil.copytree(archive, root / _ARCHIVE_CONTENTS)
        for relative in directories:
            (root / relative).mkdir(parents=True, exist_ok=True)
        for relative in files:
            path = stage_root / relative
            target = root / paths[relative]
            if path.suffix.lower() == '.json':
                payload = json.loads(path.read_text(encoding='utf-8-sig'))
                payload = _rebase_import_json(payload, stage_root, root, paths, archive, relative_base=relative.parent)
                if relative == Path('short_name_map.json'):
                    payload['name_aliases'] = aliases
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            else:
                shutil.copy2(path, target)
                if archive is not None and path.name.endswith('_heka_raw_traces.h5'):
                    import h5py

                    with h5py.File(target, 'r+') as raw:
                        source_file = raw.attrs.get('source_file')
                        if source_file:
                            raw.attrs['source_file'] = _rebase_import_json(source_file, stage_root, root, paths, archive)
        metadata = _rebase_import_json(metadata, stage_root, root, paths, archive)
        manifest = _rebase_import_json(manifest, stage_root, root, paths, archive)
        provenance = {'source_path': str(source), 'source_filename': source.name,
                      'original_label': label, 'folder_name': root.name,
                      'generated_path_aliases': path_aliases}
        if manifest.get('short_name_policy'):
            manifest['short_name_policy']['name_aliases'] = aliases
        manifest.update(browser_import=provenance, experiment_metadata=metadata,
                        original_requested_base_name=label, physical_generated_base_name=root.name,
                        canonical_identity_base_name=label)
        (root / 'name_mapping.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
        manifest_path = root / _IMPORT_MANIFEST
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return _build_dataset(source, root, manifest_path, metadata)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def open_data(source: str | Path, workspace: str | Path, *, description: str = '') -> BrowserData:
    """Open a curated folder or stage desktop parser outputs in a new workspace."""
    source = Path(source).expanduser().resolve()
    kind = classify_source(source)
    if kind == 'tables' and source.is_dir():
        return _build_dataset(source, source)

    workspace = Path(workspace).expanduser().resolve()
    if source.is_dir() and workspace.is_relative_to(source):
        raise ValueError('Choose a browser workspace outside the source data directory.')
    if kind in {'instrument', 'archive'}:
        with _import_tempdir(workspace, source) as directory:
            staging = Path(directory)
            if kind == 'instrument':
                return _open_native(source, source, workspace, staging, None, description)
            archive = staging / 'raw'
            active = _unpack_archive(source, archive)
            kind = classify_source(active)
            if kind == 'instrument':
                return _open_native(source, active, workspace, staging, archive, description)
            return _open_prepared(source, active, kind, workspace, archive)
    return _open_prepared(source, source, kind, workspace)


def _open_prepared(source: Path, active: Path, kind: str, workspace: Path,
                   archive: Path | None = None) -> BrowserData:
    folder = new_folder(workspace, source.stem, reserve=66)
    try:
        if archive is not None:
            contents = folder / 'Archive contents'
            shutil.copytree(archive, contents)
            active = contents / active.relative_to(archive)
        if kind == 'bundle':
            if active.is_dir():
                candidates = [p for p in active.iterdir() if p.is_file() and p.suffix.lower() in _HDF5_EXTENSIONS]
                canonical = [p for p in candidates if p.stem.lower().endswith('_faradaiq_bundle')]
                candidates = canonical or candidates
                active = max(candidates, key=lambda path: path.stat().st_mtime_ns)
            root = folder / f'{safe_component(active.stem, max_chars=60)}_analysis_ready_csv'
            if active.suffix.lower() in {'.npy', '.npz'}:
                _numeric_numpy(active, root)
                return _build_dataset(source, root)
            root.mkdir()
            manifest, metadata = _canonical_hdf5(active, root)
            return _build_dataset(source, root, manifest, metadata)
        if kind == 'tables':
            if active.is_dir():
                return _build_dataset(source, active)
            root = folder / f'{safe_component(active.stem, max_chars=60)}_analysis_ready_csv'
            root.mkdir()
            shutil.copy2(active, root / active.name)
            return _build_dataset(source, root)
        raise ValueError('Choose a ZIP containing data files and extract nested archives first.')
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _check_cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError('Canonical bundle export canceled.')


def _copy_canonical_bundle(data: BrowserData, output: Path, label: str, metadata: dict,
                           progress_callback, cancel_event) -> dict:
    import h5py
    from faradaiq_canonical_bundle import SCHEMA, safe_name
    from .release_identity import APP_NAME, APP_VERSION

    source = data.bundle_path
    name = safe_name(label)[:68]
    h5_path = output / f'{name}_faradaiq_bundle.h5'
    json_path = h5_path.with_suffix('.json')
    before = source.stat()
    if progress_callback:
        progress_callback('Copying the canonical HDF5 and its embedded data…')
    with source.open('rb') as original, h5_path.open('xb') as target:
        while block := original.read(_COPY_BLOCK_SIZE):
            _check_cancel(cancel_event)
            target.write(block)
    after = source.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise RuntimeError('The original HDF5 changed during export. Reopen the completed source file and try again.')

    with h5py.File(h5_path, 'r+') as h5:
        if _local_group(h5, 'tables') is None:
            raise ValueError('The original file no longer contains an eStudio canonical HDF5 table group.')
        manifest = _bundle_manifest(h5_path, h5) or dict(data.bundle_manifest)
        schema = manifest.get('schema') or str(h5.attrs.get('schema') or SCHEMA)
        manifest.update(schema=schema, app=APP_NAME, version=APP_VERSION,
                        base_name=label, sample_name=label, analysis_root=str(data.analysis_root),
                        bundle_dir=str(output), container_root=str(output),
                        h5_path=str(h5_path), json_path=str(json_path),
                        gui_metadata=_mapping(manifest.get('gui_metadata')) | metadata)
        manifest['browser_export'] = {'source_hdf5': str(source), 'source_size': before.st_size,
                                      'source_modified_ns': before.st_mtime_ns}
        for key, value in {'schema': schema, 'app': APP_NAME, 'version': APP_VERSION, 'base_name': label,
                           'analysis_root': str(data.analysis_root), 'container_root': str(output),
                           'json_manifest_name': json_path.name}.items():
            h5.attrs[key] = value
        encoded = json.dumps(manifest, ensure_ascii=False)
        if h5.get('manifest_json', getlink=True) is not None:
            del h5['manifest_json']
        h5.create_dataset('manifest_json', data=encoded, dtype=h5py.string_dtype('utf-8'))
    _check_cancel(cancel_event)
    json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'bundle_dir': str(output), 'container_root': str(output), 'analysis_root': str(data.analysis_root),
            'h5_path': str(h5_path), 'json_path': str(json_path), 'manifest_path': str(json_path),
            'technique_counts': _mapping(manifest.get('technique_counts'))}


def export_browser_bundle(data: BrowserData, output_dir: str | Path, *, sample_name: str | None = None,
                          metadata: Mapping[str, Any] | None = None, progress_callback=None,
                          cancel_event=None) -> dict:
    """Export a separate canonical HDF5 and JSON manifest without touching its inputs."""
    from faradaiq_canonical_bundle import build_canonical_bundle, safe_json

    _check_cancel(cancel_event)
    target = Path(output_dir).expanduser().resolve()
    if target.is_relative_to(data.analysis_root):
        raise ValueError('Choose a bundle output folder outside the selected analysis data folder.')
    label = str(sample_name or data.metadata.get('sample_name') or data.source.stem).strip() or data.source.stem
    selected_metadata = safe_json(_mapping(metadata))
    output = new_folder(target, label, reserve=54)
    try:
        if data.bundle_path and data.bundle_path.is_file():
            result = _copy_canonical_bundle(data, output, label, selected_metadata, progress_callback, cancel_event)
        else:
            result = build_canonical_bundle(data.analysis_root, base_name=label,
                                            gui_metadata=safe_json(data.metadata | selected_metadata),
                                            progress_callback=progress_callback, label='canonical', output_dir=output,
                                            preserve_sources=True, preserve_input_units=True, cancel_event=cancel_event,
                                            bundle_file_stem=safe_component(label, max_chars=48))
        import h5py
        with h5py.File(result['h5_path'], 'r') as h5:
            tables = _local_group(h5, 'tables')
            if tables is None or not len(tables):
                raise ValueError('No numeric tables were available for this canonical bundle.')
        _check_cancel(cancel_event)
        return result
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


def load_session(path: str | Path) -> dict:
    """Read desktop and browser session JSON with desktop-specific fields intact."""
    path = Path(path).expanduser().resolve()
    session = _json_mapping(path.read_text(encoding='utf-8-sig'))
    if session.get('schema') not in _SESSION_SCHEMAS:
        raise ValueError('Choose an eStudio full session JSON file.')
    workspace = _mapping(session.get('browser_workspace'))
    main_vars = _mapping(session.get('main_vars'))
    main_texts = _mapping(session.get('main_texts'))
    source = (main_vars.get('file_var') or _mapping(session.get('collection_state')).get('source_path') or '')
    browser_sessions = session.get('open_browser_sessions') or []
    native_browser = _mapping(browser_sessions[0]) if isinstance(browser_sessions, list) and browser_sessions else {}
    analysis_root = (session.get('current_analysis_root') or native_browser.get('analysis_root')
                     or native_browser.get('analyzed_root') or native_browser.get('cache_root') or '')
    workspace.setdefault('source', source)
    workspace.setdefault('analysis_root', analysis_root)
    workspace.setdefault('metadata', {'experimental_description': main_texts.get('experimental_description') or ''})
    workspace.setdefault('state', {})
    session['browser_workspace'] = workspace
    return session


def save_session(path: str | Path, *, source: str | Path, analysis_root: str | Path,
                 metadata: Mapping[str, Any] | None = None, browser_state: Mapping[str, Any] | None = None,
                 previous: Mapping[str, Any] | None = None) -> Path:
    """Atomically save paths and web state without discarding desktop view settings."""
    import os
    from faradaiq_canonical_bundle import safe_json

    path = Path(path).expanduser().resolve()
    source = Path(source).expanduser().resolve()
    analysis_root = Path(analysis_root).expanduser().resolve()
    if path == source or path.suffix.lower() != '.json':
        raise ValueError('Choose a separate .json file for this eStudio session.')
    payload = _mapping(previous)
    from .release_identity import APP_NAME, APP_VERSION

    version = APP_VERSION
    payload.setdefault('created', datetime.now(timezone.utc).isoformat())
    payload.setdefault('open_browser_sessions', [{
        'schema': 'faradaiq_open_browser_session_v177',
        'class_name': 'FaradaIQDataBrowser',
        'base_name': source.stem,
        'analysis_root': str(analysis_root),
    }])
    main_vars = _mapping(payload.get('main_vars'))
    main_vars.update(file_var=str(source))
    main_vars.setdefault('output_dir_var', str(analysis_root.parent))
    main_texts = _mapping(payload.get('main_texts'))
    combined = _mapping(_mapping(payload.get('browser_workspace')).get('metadata'))
    combined.update(_mapping(metadata))
    if 'experimental_description' in combined:
        main_texts['experimental_description'] = str(combined['experimental_description'] or '')
    browser = _mapping(payload.get('browser_workspace'))
    state = _mapping(browser.get('state'))
    state.update(_mapping(browser_state))
    browser.update(source=str(source), analysis_root=str(analysis_root), metadata=combined, state=state)
    payload.update(app=APP_NAME, version=version, main_vars=main_vars,
                   main_texts=main_texts, current_analysis_root=str(analysis_root), browser_workspace=browser)
    payload.setdefault('schema', _DESKTOP_SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix='.tmp', prefix='.estudio-', delete=False,
                                mode='w', encoding='utf-8') as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=safe_json)
        if path.exists():
            try:
                if not path.is_file():
                    raise ValueError('Session destination must be a regular file.')
                load_session(path)
            except (OSError, ValueError) as error:
                raise FileExistsError(f'Choose a new session filename. This path already contains unrelated data: {path}') from error
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return path
