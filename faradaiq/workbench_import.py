"""Guided local import, explicit physical column mapping and stable-file polling."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
from tempfile import TemporaryDirectory, gettempdir
import time
from zipfile import BadZipFile, ZipFile

import numpy as np
import pandas as pd

from .browser_analysis import _NORMALIZE_UNITS, _ROLE_LABELS, _UNITS, measurement_roles
from .browser_data import (BUNDLE_EXTENSIONS, INSTRUMENT_EXTENSIONS, TABLE_EXTENSIONS,
                           _table_paths, classify_source, open_data)

PROFILE_SCHEMA = 'faradaiq-import-profile-1'
CANONICAL_COLUMNS = {
    'time': 'time_s', 'potential': 'potential_V', 'current': 'current_A',
    'frequency': 'frequency_Hz', 'zreal': 'Zreal_ohm', 'zimag': 'Zimag_ohm',
    'x': 'x_m', 'y': 'y_m', 'z': 'z_m',
}
ROLE_LABELS = {role: _ROLE_LABELS[role] for role in CANONICAL_COLUMNS}
ROLE_UNITS = {role: tuple(_UNITS[role]) for role in CANONICAL_COLUMNS}
SOURCE_EXTENSIONS = TABLE_EXTENSIONS | INSTRUMENT_EXTENSIONS | BUNDLE_EXTENSIONS | {'.zip'}
HEKA = {'.dat', '.pul', '.pgf', '.amp', '.mrk', '.mth', '.onl'}
BIOLOGIC = {'.mpr', '.mps', '.mgr'}
INSTRUMENT_NAMES = dict.fromkeys(HEKA, 'HEKA') | dict.fromkeys(BIOLOGIC | {'.mpt'}, 'BioLogic') | {
    '.par': 'VersaStudio', '.psbd': 'VersaStudio', '.pssession': 'PalmSens',
    '.z': 'Solartron', '.cor': 'Solartron',
}


class IncompleteSourceError(ValueError):
    """The source may be usable after acquisition or transfer finishes."""


def _unit_name(unit):
    return str(unit).strip().translate(_NORMALIZE_UNITS).replace('Ohm', 'ohm')


@dataclass(frozen=True)
class ImportProfile:
    """Map physical roles to source columns and their declared input units."""

    name: str
    columns: dict[str, str]
    units: dict[str, str]
    technique: str = ''
    negate_imaginary: bool = False

    def __post_init__(self):
        columns = {str(key): str(value).strip() for key, value in self.columns.items()}
        units = {str(key): _unit_name(value) for key, value in self.units.items()}
        if not columns or columns.keys() - CANONICAL_COLUMNS.keys():
            raise ValueError('Select at least one supported measurement quantity.')
        if not all(columns.values()) or len(set(columns.values())) != len(columns):
            raise ValueError('Select a different source column for each measurement quantity.')
        if units.keys() != columns.keys():
            raise ValueError('Declare an input unit for every mapped quantity.')
        for role, unit in units.items():
            if unit not in _UNITS[role]:
                options = ', '.join(ROLE_UNITS[role])
                raise ValueError(f'{ROLE_LABELS[role]} unit {unit!r} is incompatible. Choose one of {options}.')
        if self.negate_imaginary and 'zimag' not in columns:
            raise ValueError('Map the imaginary impedance column to apply its sign convention.')
        object.__setattr__(self, 'columns', columns)
        object.__setattr__(self, 'units', units)

    def snapshot(self):
        return dict(schema=PROFILE_SCHEMA, name=self.name, columns=dict(self.columns), units=dict(self.units),
                    technique=self.technique, negate_imaginary=self.negate_imaginary)

    def save(self, path):
        path = Path(path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(self.snapshot(), stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        return path

    @classmethod
    def load(cls, source):
        if isinstance(source, (str, Path)):
            source = json.loads(Path(source).expanduser().read_text(encoding='utf-8-sig'))
        if (not isinstance(source, dict) or source.get('schema', PROFILE_SCHEMA) != PROFILE_SCHEMA
                or not isinstance(source.get('columns'), dict) or not isinstance(source.get('units'), dict)
                or not isinstance(source.get('negate_imaginary', False), bool)):
            raise ValueError('Choose a FaradaIQ import profile JSON with columns and units.')
        return cls(str(source.get('name') or 'Custom import'), source['columns'], source['units'],
                   str(source.get('technique') or ''), source.get('negate_imaginary', False))

    def apply(self, table):
        if not table.columns.is_unique:
            raise ValueError('The input table must have unique column headers.')
        absent = [column for column in self.columns.values() if column not in table]
        if absent:
            raise ValueError(f'Profile {self.name!r} requires missing columns: {", ".join(absent)}.')
        output = table.drop(columns=list(self.columns.values())).copy()
        for role, column in self.columns.items():
            canonical = CANONICAL_COLUMNS[role]
            if canonical in output:
                raise ValueError(f'The mapped quantity {canonical} conflicts with an unmapped source column.')
            source = table[column]
            numeric = pd.to_numeric(source, errors='coerce')
            if (source.notna() & ~np.isfinite(numeric)).any() or not np.isfinite(numeric).any():
                raise ValueError(f'{column!r} must contain finite numeric values for {ROLE_LABELS[role].lower()}.')
            if role == 'frequency' and numeric.le(0).any():
                raise ValueError('Frequency values must be positive.')
            sign = -1 if role == 'zimag' and self.negate_imaginary else 1
            scaled = numeric.astype(float) * (_UNITS[role][self.units[role]] * sign)
            if np.isinf(scaled).any():
                raise ValueError(f'Converting {column!r} to canonical units exceeded the numeric range.')
            output[canonical] = scaled
        return output


def _bio_key(path):
    stem = path.stem
    if path.suffix.lower() == '.mps':
        stem = re.sub(r'^VMP\d+\s*-\s*[\d.]+_', '', stem, flags=re.I)
    return stem.casefold()


def _peek(path, size=256):
    with path.open('rb') as stream:
        return stream.read(size)


def _companions(source):
    """Return local physical companions and problems based on exact file groups."""
    suffix = source.suffix.lower()
    if suffix not in HEKA | BIOLOGIC | {'.par', '.psbd'}:
        return (), ()
    siblings = tuple(path for path in source.parent.iterdir() if path != source and path.is_file())
    if suffix in {'.par', '.psbd'}:
        stem = source.stem.casefold().removesuffix('.par') if suffix == '.psbd' else source.stem.casefold()
        group = (source, *siblings)
        methods = [path for path in group if path.name.casefold() == stem + '.par']
        if len(methods) > 1:
            return (), ('More than one file matches the VersaStudio method name.',)
        if not methods:
            return (), ('Keep the matching VersaStudio .par and .par.psbd (or .psbd) files in the same folder.',)
        method = methods[0]
        preferred = (method.with_suffix('.psbd'), Path(str(method) + '.psbd'))
        binary = next((path for path in preferred if path in group), None)
        if binary is None:
            for candidate in preferred:
                matches = [path for path in group if path.name.casefold() == candidate.name.casefold()]
                if len(matches) > 1:
                    return (), ('More than one file matches the VersaStudio binary name.',)
                if matches:
                    binary = matches[0]
                    break
        if binary is None:
            return (), ('Keep the matching VersaStudio .par and .par.psbd (or .psbd) files in the same folder.',)
        if suffix == '.psbd' and source != binary:
            return (method, binary), (f'The VersaStudio method uses {binary.name}. Select the matching method or binary file.',)
        return tuple(path for path in (method, binary) if path != source), ()
    if suffix in BIOLOGIC:
        matches = tuple(path for path in siblings if path.suffix.lower() in BIOLOGIC
                        and _bio_key(path) == _bio_key(source))
        return matches, ()
    matches = tuple(path for path in siblings if path.suffix.lower() in HEKA
                    and path.stem.casefold() == source.stem.casefold())
    if suffix == '.dat' and _peek(source, 4) == b'DAT2':
        return matches, ()
    group = {path.suffix.lower() for path in matches} | {suffix}
    raw = next((path for path in matches if path.suffix.lower() == '.dat'), None)
    if raw and _peek(raw, 4) == b'DAT2':
        return matches, ()
    missing = {'.dat', '.pul'} - group
    messages = (f'Loose HEKA data require same-stem .dat and .pul files. Missing {", ".join(sorted(missing))}.',) if missing else ()
    return matches, messages


def _file_inventory(source):
    if source.is_dir():
        paths = [path for path in source.rglob('*') if path.is_file() and path.resolve().is_relative_to(source)]
        # An analysis-ready sidecar lives beside some curated folders.
        stem = source.name.removesuffix('_analysis_ready_csv')
        sidecar = source.parent / f'{stem}_analysis_ready_manifest.json'
        if sidecar.is_file():
            paths.append(sidecar)
        return tuple(sorted(paths))
    related, _ = _companions(source)
    base = (source, *related)
    # Canonical HDF5 may use an external JSON manifest for metadata.
    sidecar = source.with_suffix('.json')
    if source.suffix.lower() in {'.h5', '.hdf5'} and sidecar.is_file():
        return (*base, sidecar)
    return base


def _stat_key(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


def source_signature(source):
    """Cheap change detection used before and after reading or hashing a source."""
    source = Path(source).expanduser().resolve(strict=True)
    return tuple((str(path), _stat_key(path)) for path in sorted(_file_inventory(source)))


def sha256_source(source):
    """Hash data bytes, including physical companions, independent of the base filename."""
    source = Path(source).expanduser().resolve(strict=True)
    before = source_signature(source)
    if not before:
        raise IncompleteSourceError(f'{source.name} contains no files.')
    if len(before) == 1 and not source.is_dir():
        with source.open('rb') as stream:
            result = hashlib.file_digest(stream, 'sha256').hexdigest()
    else:
        members = []
        for name, _ in before:
            path = Path(name)
            with path.open('rb') as stream:
                checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            if source.is_dir():
                key = path.relative_to(source).as_posix() if path.is_relative_to(source) else 'manifest'
            else:
                key = path.suffix.lower()
            members.append((key, checksum))
        result = hashlib.sha256(json.dumps(sorted(members), ensure_ascii=False).encode('utf-8')).hexdigest()
    if before != source_signature(source):
        raise IncompleteSourceError(f'{source.name} changed during verification. Wait for it to finish writing.')
    return result


def _check_delimited(path):
    with path.open('rb') as stream:
        sample = stream.read(16384)
    try:
        sample = sample.decode('utf-8-sig')
    except UnicodeDecodeError:
        sample = sample.decode('latin-1')
        encoding = 'latin-1'
    else:
        encoding = 'utf-8-sig'
    if not sample.strip():
        raise IncompleteSourceError(f'{path.name} has no table data.')
    useful = '\n'.join(line for line in sample.splitlines() if line.strip() and not line.lstrip().startswith('#'))
    if not useful:
        raise IncompleteSourceError(f'{path.name} has no table data.')
    delim = '\t' if path.suffix.lower() == '.tsv' else None
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(useful, delimiters=',;\t').delimiter
        except csv.Error:
            delim = next((char for char in ',;\t' if char in useful.splitlines()[0]), None)
    if delim is None:
        raise IncompleteSourceError(f'{path.name} has no delimited table columns.')
    with path.open(encoding=encoding, newline='') as stream:
        rows = csv.reader(stream, delimiter=delim, strict=True)
        width = None
        count = 0
        try:
            for row in rows:
                if not row or (len(row) == 1 and not row[0].strip()) or row[0].lstrip().startswith('#'):
                    continue
                if width is None:
                    width = len(row)
                if len(row) != width:
                    raise IncompleteSourceError(f'{path.name} row {rows.line_num} has {len(row)} fields. Expected {width}.')
                count += 1
        except csv.Error as error:
            raise IncompleteSourceError(f'{path.name} contains malformed or unfinished quoted fields.') from error
    if count < 2 or width is None or width < 2:
        raise IncompleteSourceError(f'{path.name} needs at least two columns and one data row.')


def _check_file(path, *, full):
    size = path.stat().st_size
    if not size:
        raise IncompleteSourceError(f'{path.name} is empty.')
    suffix = path.suffix.lower()
    if suffix == '.dat':
        head = _peek(path)
        if head.startswith(b'DAT2'):
            if len(head) < 256:
                raise IncompleteSourceError(f'{path.name} has an incomplete HEKA DAT2 header.')
            for start in range(64, 224, 16):
                offset, length, label = struct.unpack_from('<II8s', head, start)
                if label.strip(b'\x00') and length and (offset < 256 or offset + length > size):
                    section = label.rstrip(b'\x00').decode('ascii', errors='replace')
                    raise IncompleteSourceError(f'{path.name} is missing bytes from the HEKA {section} section.')
    if suffix in {'.zip', '.npz', '.xlsx'}:
        try:
            with ZipFile(path) as archive:
                if not any(not member.is_dir() for member in archive.infolist()):
                    raise IncompleteSourceError(f'{path.name} contains no archived data.')
        except BadZipFile as error:
            raise IncompleteSourceError(f'{path.name} has a missing or invalid ZIP directory.') from error
    if full and suffix in {'.csv', '.tsv'}:
        _check_delimited(path)


@dataclass(frozen=True)
class ImportDiagnosis:
    source: Path
    kind: str
    instrument: str
    companions: tuple[Path, ...]
    problems: tuple[str, ...]
    warnings: tuple[str, ...]


def diagnose_source(source, *, full=False):
    """Describe required companions and detectable incomplete-transfer conditions."""
    source = Path(source).expanduser().resolve()
    kind = classify_source(source)
    companions = ()
    problems = []
    warnings = []
    if source.is_file():
        companions, missing = _companions(source)
        problems.extend(missing)
        for path in (source, *companions):
            try:
                _check_file(path, full=full)
            except IncompleteSourceError as error:
                problems.append(str(error))
        if source.suffix.lower() in {'.mps', '.mgr'} and not any(p.suffix.lower() == '.mpr' for p in companions):
            warnings.append('No matching BioLogic .mpr trace was found. The selected file may contain only settings.')
    elif kind == 'tables':
        for path in _table_paths(source):
            try:
                _check_file(path, full=full)
            except IncompleteSourceError as error:
                problems.append(f'{path.relative_to(source).parent} · {error}')
    elif kind == 'instrument':
        # Native directory imports can continue past incomplete experiments.
        for path in _file_inventory(source):
            if path.suffix.lower() not in {'.par', '.dat', '.psbd'}:
                continue
            _, missing = _companions(path)
            warnings.extend(f'{path.name} · {message}' for message in missing)
    return ImportDiagnosis(source, kind, INSTRUMENT_NAMES.get(source.suffix.lower(), ''),
                           companions, tuple(problems), tuple(dict.fromkeys(warnings)))


def _readable_frame(frame, path):
    if frame.empty or len(frame.columns) < 2 or not len(frame.select_dtypes(include='number').columns):
        raise ValueError(f'{path.name} contains no readable numeric measurement rows.')
    return frame


def open_verified(source, workspace):
    diagnosis = diagnose_source(source, full=True)
    if diagnosis.problems:
        raise IncompleteSourceError(' '.join(diagnosis.problems))
    return open_data(diagnosis.source, workspace), diagnosis


def temporary_import_dir(workspace, source):
    """Keep disposable native parser paths short enough for their output naming budget."""
    workspace = Path(workspace).expanduser().resolve()
    temporary_root = Path(gettempdir()).resolve()
    if source.is_dir() and temporary_root.is_relative_to(source):
        if workspace.is_relative_to(source):
            raise ValueError('Choose a workbench outside the imported data folder.')
        workspace.mkdir(parents=True, exist_ok=True)
        temporary_root = workspace
    return TemporaryDirectory(prefix='fq-', dir=temporary_root)


@dataclass(frozen=True)
class ImportPreview:
    diagnosis: ImportDiagnosis
    samples: dict[str, pd.DataFrame]
    suggested_roles: dict[str, str]
    table_count: int


def preview_source(source, workspace, *, rows=20, tables=8):
    """Exercise the native reader on a disposable copy and retain bounded samples."""
    diagnosis = diagnose_source(source, full=True)
    if diagnosis.problems:
        return ImportPreview(diagnosis, {}, {}, 0)
    workspace = Path(workspace).expanduser().resolve()
    if diagnosis.source.is_dir() and workspace.is_relative_to(diagnosis.source):
        raise ValueError('Choose a workbench outside the imported data folder.')
    with temporary_import_dir(workspace, diagnosis.source) as directory:
        data = open_data(diagnosis.source, directory)
        samples = {str(path.relative_to(data.analysis_root)): _readable_frame(data.read_table(path, max_rows=rows), path)
                   for path in data.tables[:tables]}
        first = next(iter(samples.values()))
        roles = {role: column for role, column in measurement_roles(first).items()
                 if column and role in CANONICAL_COLUMNS}
        return ImportPreview(diagnosis, samples, roles, len(data.tables))


@dataclass(frozen=True)
class WatchFile:
    source: Path
    status: str
    signature: tuple
    detail: str = ''


class StableFolderWatch:
    """Require identical file/companion signatures over a measured quiet interval."""

    def __init__(self, folder, *, stable_for=3.0):
        self.folder = Path(folder).expanduser().resolve(strict=True)
        if not self.folder.is_dir():
            raise ValueError('Choose an existing watch folder.')
        self.stable_for = float(stable_for)
        if not np.isfinite(self.stable_for) or self.stable_for < 0:
            raise ValueError('The quiet interval must be a finite duration of zero or more seconds.')
        self._observed = {}
        self._handled = {}

    def poll(self, *, now=None):
        now = time.monotonic() if now is None else float(now)
        candidates = sorted(path for path in self.folder.iterdir() if path.is_file() and not path.is_symlink()
                            and not path.name.startswith('.') and path.suffix.lower() in SOURCE_EXTENSIONS)
        observed = {}
        result = []
        for path in candidates:
            try:
                companions, _ = _companions(path)
                suffix = path.suffix.lower()
                # Report each vendor group once and prefer its primary acquisition file.
                if ((suffix == '.psbd' and companions) or (suffix in HEKA - {'.dat'}
                        and any(item.suffix.lower() == '.dat' for item in companions))
                        or (suffix in BIOLOGIC - {'.mpr'} and any(item.suffix.lower() == '.mpr' for item in companions))):
                    continue
                signature = source_signature(path)
                previous = self._observed.get(path)
                since = previous[1] if previous and previous[0] == signature else now
                observed[path] = signature, since
                if self._handled.get(path) == signature:
                    status, detail = 'Handled', ''
                elif not previous or previous[0] != signature or now - since < self.stable_for:
                    status, detail = 'Waiting', 'File or companion is new or still changing.'
                else:
                    diagnosis = diagnose_source(path)
                    status = 'Incomplete' if diagnosis.problems else 'Ready'
                    detail = ' '.join(diagnosis.problems or diagnosis.warnings)
                result.append(WatchFile(path, status, signature, detail))
            except (OSError, ValueError) as error:
                result.append(WatchFile(path, 'Incomplete', (), str(error)))
        self._observed = observed
        self._handled = {path: signature for path, signature in self._handled.items() if path in observed}
        return result

    def mark_handled(self, path, signature):
        self._handled[Path(path)] = signature
