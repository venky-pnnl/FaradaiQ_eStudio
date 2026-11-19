"""Projects and imports tools for the local eStudio browser."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from .browser_data import stage_browser_uploads
from .readable_names import safe_component
from .workbench_audit import read_history, record_operation
from .workbench_import import ImportProfile, ROLE_LABELS, ROLE_UNITS, StableFolderWatch, preview_source
from .workbench_project import (QUEUE_LABEL, ImportQueue, ImportRequest, MultiInstrumentProject,
                               search_projects)
from .workbench_state import capture_settings, ephemeral_widget_keys

PREFIX = 'w340_project_'
PERSIST = dict(persist_state='session')
QUEUE_KEY = 'workbench_project_queue'
QUEUE_CONTEXT_KEY = 'workbench_project_queue_context'
WATCH_KEY = 'workbench_project_watch'


def _fresh(workspace, name='Comparison session'):
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S_%f')
    directory = f'{safe_component(name, max_chars=32)}_{stamp}'
    return MultiInstrumentProject(workspace / 'Workbench' / 'Projects' / directory, name)


def _audit(workspace, action, *, inputs=(), parameters=None, outputs=()):
    try:
        record_operation(workspace, action, inputs=inputs, parameters=parameters, outputs=outputs)
    except OSError as error:
        st.warning(f'The operation completed, but its analysis history could not be written. {error}')


def _changed(workspace, action, project, *, inputs=(), parameters=None, restore=False):
    _audit(workspace, action, inputs=inputs, parameters=parameters, outputs=(project.checkpoint_path,))
    st.session_state['project_changed'] = True
    if restore:
        st.session_state['project_restore_state'] = True
    st.rerun()


def _attach(workspace, project, source):
    st.session_state['multi_project'] = project
    st.session_state.pop(WATCH_KEY, None)
    st.session_state[PREFIX + 'watch_enabled'] = False
    _changed(workspace, 'Open comparison project', project, inputs=(source,), restore=True)


def _queue_busy():
    from .browser_app import _job_busy
    return _job_busy()


def _submit(workspace, requests):
    from .browser_app import _submit as submit_job

    if not requests or _queue_busy():
        return
    project = st.session_state.get('multi_project')
    queue = ImportQueue(project or _fresh(workspace), requests, workspace / 'Workbench' / 'Import staging')
    st.session_state[QUEUE_KEY] = queue
    st.session_state[QUEUE_CONTEXT_KEY] = project, st.session_state.get('dataset')
    submit_job(QUEUE_LABEL, queue.run, cancelable=True)


def _queue_matches_context(queue):
    active = st.session_state.get('multi_project')
    if active is queue.project:
        return True
    base_project, base_data = st.session_state.get(QUEUE_CONTEXT_KEY, (None, None))
    return active is None and base_project is None and base_data is st.session_state.get('dataset')


def _consume_queue(workspace):
    if QUEUE_LABEL not in st.session_state:
        return
    handles = st.session_state[QUEUE_LABEL]
    queue = st.session_state.get(QUEUE_KEY)
    if queue is None:
        st.session_state.pop(QUEUE_LABEL, None)
        return
    if not _queue_matches_context(queue):
        for handle in handles:
            queue.project.discard_prepared(handle)
        st.session_state.pop(QUEUE_LABEL, None)
        st.warning('The import finished after the active project changed. Start the import again in the active project.')
        return
    try:
        imported = queue.apply(handles)
    except (OSError, ValueError) as error:
        st.error(f'The imported files are ready, but the project catalog could not be written. {error}')
        return
    st.session_state.pop(QUEUE_LABEL, None)
    project = queue.project
    watch = st.session_state.get(WATCH_KEY)
    statuses = queue.status()
    for request, outcome in zip(queue.requests, statuses):
        if watch and request.signature is not None and outcome['status'] != 'Canceled':
            watch.mark_handled(request.source, request.signature)
    if any(outcome['status'] == 'Canceled' for outcome in statuses):
        st.session_state[PREFIX + 'watch_enabled'] = False
    _audit(workspace, 'Project import queue', inputs=[request.source for request in queue.requests],
           parameters={'statuses': statuses}, outputs=(project.checkpoint_path,) if imported else ())
    if imported:
        st.session_state['multi_project'] = project
        st.session_state['project_changed'] = True
        st.rerun()


@st.fragment(run_every=1)
def _queue_status():
    queue = st.session_state.get(QUEUE_KEY)
    if queue is not None and _queue_matches_context(queue):
        st.subheader('Import queue')
        st.dataframe(queue.status(), hide_index=True, column_order=('source', 'status', 'detail'))
        if _queue_busy():
            st.caption('Stop finishes the current source and cancels the sources still waiting.')


def _source_paths(text):
    return list(dict.fromkeys(line.strip().strip('"') for line in (text or '').splitlines() if line.strip().strip('"')))


def _import_profile(preview, table):
    mode = st.radio('Column mapping', ('Use source headers', 'Map columns and units', 'Load JSON profile'),
                    key=PREFIX + 'mapping_mode', horizontal=True, **PERSIST)
    if mode == 'Use source headers':
        return None, False
    if mode == 'Load JSON profile':
        path = st.text_input('Profile JSON path', key=PREFIX + 'profile_path', **PERSIST).strip()
        if not path:
            return None, True
        try:
            return ImportProfile.load(path), False
        except (OSError, ValueError) as error:
            st.error(str(error))
            return None, True
    if preview is None or table is None:
        st.info('Preview a source to select its column names.')
        return None, True
    options = [None, *table.columns]
    name = st.text_input('Profile name', 'Custom import', key=PREFIX + 'profile_name', **PERSIST)
    technique = st.text_input('Technique', placeholder='CV, LSV, EIS, CA, CP, or OCP',
                              key=PREFIX + 'profile_technique', **PERSIST)
    columns = {}
    units = {}
    for role, label in ROLE_LABELS.items():
        choices, unit_box = st.columns(2)
        default = preview.suggested_roles.get(role)
        column = choices.selectbox(label, options, index=options.index(default) if default in options else 0,
                                   format_func=lambda value: 'Unmapped' if value is None else str(value),
                                   key=PREFIX + f'map_{role}', **PERSIST)
        if column is not None:
            columns[role] = str(column)
            unit = unit_box.selectbox(f'{label} input unit', [None, *ROLE_UNITS[role]],
                                      format_func=lambda value: 'Select a unit' if value is None else value,
                                      key=PREFIX + f'unit_{role}', **PERSIST)
            if unit is not None:
                units[role] = unit
    negate = 'zimag' in columns and st.checkbox('Input column contains −Im(Z)', key=PREFIX + 'negate', **PERSIST)
    if not columns or columns.keys() != units.keys():
        st.caption('Select at least one quantity and declare each mapped input unit.')
        return None, True
    try:
        return ImportProfile(name, columns, units, technique, negate), False
    except ValueError as error:
        st.error(str(error))
        return None, True


def _import_sources(data, workspace):
    st.subheader('Add sources')
    if pending := st.session_state.pop('workbench_project_upload_path', None):
        old = st.session_state.get(PREFIX + 'source_paths', '')
        st.session_state[PREFIX + 'source_paths'] = '\n'.join([*(_source_paths(old)), pending])
    paths = _source_paths(st.text_area('Local files or folders, one path per line', key=PREFIX + 'source_paths', **PERSIST))
    with st.expander('Upload files with companions'):
        uploads = st.file_uploader('Files from one source', accept_multiple_files=True, key=PREFIX + 'uploads')
        if st.button('Stage uploads', key=PREFIX + 'stage_uploads', disabled=not uploads):
            try:
                staged = stage_browser_uploads(uploads, workspace / 'Workbench' / 'Uploads')
            except (OSError, ValueError) as error:
                st.error(str(error))
            else:
                _audit(workspace, 'Stage project uploads', inputs=[item.name for item in uploads], outputs=(staged,))
                st.session_state['workbench_project_upload_path'] = str(staged)
                st.rerun()
    selected = st.selectbox('Source to preview', paths, key=PREFIX + 'preview_path') if paths else None
    if st.button('Preview and diagnose', key=PREFIX + 'preview', disabled=not selected or _queue_busy()):
        if selected is not None:
            try:
                with st.spinner('Reading a local preview…'):
                    preview = preview_source(selected, workspace / 'Workbench' / 'Import staging')
            except Exception as error:
                st.session_state['workbench_project_preview'] = selected, None, str(error)
                _audit(workspace, 'Diagnose project import', inputs=(selected,), parameters={'error': str(error)})
            else:
                st.session_state['workbench_project_preview'] = selected, preview, ''
                _audit(workspace, 'Diagnose project import', inputs=(selected,),
                       parameters={'tables': preview.table_count, 'problems': preview.diagnosis.problems})
    prior = st.session_state.get('workbench_project_preview')
    preview = None
    sample = None
    blocked = False
    if prior and prior[0] == selected:
        _, preview, error = prior
        if error:
            st.error(error)
            st.caption('Check the file format, access permissions, and whether the instrument has finished exporting.')
            blocked = True
        if preview is not None:
            diagnosis = preview.diagnosis
            st.caption(' · '.join(filter(None, [diagnosis.instrument, diagnosis.kind.title(),
                                                 f'{preview.table_count} available tables'])))
            if diagnosis.companions:
                st.write('Companions found', ', '.join(path.name for path in diagnosis.companions))
            for problem in diagnosis.problems:
                st.error(problem)
            for warning in diagnosis.warnings:
                st.warning(warning)
            blocked = bool(diagnosis.problems)
            if preview.samples:
                name = st.selectbox('Preview table', list(preview.samples), key=PREFIX + 'preview_table')
                sample = preview.samples[name]
                st.dataframe(sample, hide_index=True)
    profile, invalid = _import_profile(preview, sample)
    if profile is not None:
        if sample is not None:
            try:
                mapped = profile.apply(sample)
            except ValueError as error:
                st.error(str(error))
                invalid = True
            else:
                st.dataframe(mapped, hide_index=True)
        st.download_button('Download import profile', json.dumps(profile.snapshot(), ensure_ascii=False, indent=2),
                           f'{safe_component(profile.name, max_chars=48)}.json', 'application/json',
                           key=PREFIX + 'download_profile')
    left, middle, right = st.columns(3)
    label = left.text_input('Source label', key=PREFIX + 'source_label', disabled=len(paths) > 1, **PERSIST)
    instrument = middle.text_input('Instrument override', key=PREFIX + 'instrument', **PERSIST)
    tags = right.text_input('Source tags, comma separated', key=PREFIX + 'source_tags', **PERSIST)
    allow_duplicate = st.checkbox('Import identical contents as an independent source', key=PREFIX + 'duplicate_override',
                                  help='Use this for an independently acquired replicate or a deliberately different instrument label.')
    if st.button('Import sources', type='primary', key=PREFIX + 'import_sources',
                 disabled=not paths or invalid or _queue_busy() or (blocked and len(paths) == 1)):
        _submit(workspace, [
            ImportRequest(path, label if len(paths) == 1 else '', instrument, profile, tags,
                          allow_duplicate=allow_duplicate) for path in paths
        ])
    project = st.session_state.get('multi_project')
    current = data is not None and bool(data.tables) and (project is None or data.source != project.root)
    if current and st.button('Add currently open data', key=PREFIX + 'add_current', disabled=invalid or _queue_busy()):
        project = project or _fresh(workspace)
        try:
            project.add_data(data, label=label, instrument=instrument, tags=tags, profile=profile,
                             allow_duplicate=allow_duplicate)
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            st.session_state['multi_project'] = project
            _changed(workspace, 'Add currently open data', project, inputs=(data.source,))


def _source_label(handle):
    return f'{handle["label"]} · {handle["instrument"]} — {handle["folder"]}'


def _organizer(project, workspace):
    st.subheader('Experiment organizer')
    if project is None or not project.handles:
        st.info('Import a source to organize its measurements.')
        return
    rows = project.search()
    st.dataframe(rows, hide_index=True, column_order=('source', 'instrument', 'measurement', 'technique', 'tags', 'annotations'))
    handles = {handle['id']: handle for handle in project.handles}
    handle_id = st.selectbox('Source', list(handles), format_func=lambda key: _source_label(handles[key]),
                             key=PREFIX + 'organize_source', **PERSIST)
    handle = handles[handle_id]
    controls = f'{PREFIX}{project.project_id}_{handle_id}_{len(project.history)}_'
    with st.form(key=controls + 'source_edit'):
        label = st.text_input('Source label', handle['label'], key=controls + 'source_label')
        instrument = st.text_input('Instrument', handle['instrument'], key=controls + 'instrument')
        tags = st.text_input('Source tags, comma separated', ', '.join(handle['tags']), key=controls + 'source_tags')
        annotations = st.text_area('Dataset annotations', handle['annotations'], key=controls + 'dataset_annotations')
        submitted = st.form_submit_button('Update source', disabled=_queue_busy())
    if submitted:
        try:
            project.update_handle(handle_id, label=label, instrument=instrument, tags=tags, annotations=annotations)
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            _changed(workspace, 'Edit project source', project, inputs=(handle['source_path'],), parameters={'handle': handle_id})
    st.caption(handle['source_path'])
    items = {row['path']: row['record'] for row in handle['measurements']}
    if items:
        selected = st.selectbox('Measurement', list(items), format_func=lambda path: f'{items[path]["display_name"]} — {path}',
                                key=PREFIX + f'organize_measurement_{handle_id}', **PERSIST)
        row = items[selected]
        controls += f'{list(items).index(selected)}_'
        with st.form(key=controls + 'measurement_edit'):
            name = st.text_input('Measurement name', row['display_name'], key=controls + 'measurement_name')
            tags = st.text_input('Measurement tags, comma separated', ', '.join(row['tags']), key=controls + 'measurement_tags')
            annotations = st.text_area('Measurement annotations', row.get('annotations', ''), key=controls + 'annotations')
            submitted = st.form_submit_button('Update measurement', disabled=_queue_busy())
        if submitted:
            try:
                project.update_measurement(selected, display_name=name, tags=tags, annotations=annotations)
            except (OSError, ValueError) as error:
                st.error(str(error))
            else:
                _changed(workspace, 'Edit project measurement', project, inputs=(project.root / selected,))
    if st.button('Remove source from project', key=PREFIX + 'remove_source', disabled=_queue_busy()):
        try:
            project.remove_handle(handle_id)
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            _changed(workspace, 'Remove project source', project, inputs=(handle['source_path'],))


@st.fragment(run_every=2)
def _watch_tick(workspace, *, show=True):
    project = st.session_state.get('multi_project')
    folder = st.session_state.get(PREFIX + 'watch_folder', '').strip()
    seconds = st.session_state.get(PREFIX + 'watch_seconds', 3.0)
    enabled = st.session_state.get(PREFIX + 'watch_enabled', False)
    if not folder or (not enabled and not show):
        return
    try:
        normalized = Path(folder).expanduser().resolve(strict=True)
        if project is not None and (normalized.is_relative_to(project.root) or project.root.is_relative_to(normalized)):
            raise ValueError('Choose a watch folder outside the generated project directory.')
        watch = st.session_state.get(WATCH_KEY)
        if watch is None or watch.folder != normalized or watch.stable_for != float(seconds):
            watch = StableFolderWatch(normalized, stable_for=seconds)
            st.session_state[WATCH_KEY] = watch
        rows = watch.poll()
    except (OSError, ValueError) as error:
        if show:
            st.error(str(error))
        return
    ready = [row for row in rows if row.status == 'Ready']
    if show and rows:
        st.dataframe([dict(source=str(row.source), status=row.status, detail=row.detail) for row in rows], hide_index=True)
    elif show:
        st.caption('No supported sources found in this folder.')
    manual = show and st.button('Import ready files', key=PREFIX + 'watch_import', disabled=not ready or _queue_busy())
    if ready and (manual or enabled) and not _queue_busy() and QUEUE_LABEL not in st.session_state:
        _submit(workspace, [ImportRequest(row.source, signature=row.signature) for row in ready])


def _watch_settings():
    st.subheader('Watch folder')
    st.text_input('Local watch folder', key=PREFIX + 'watch_folder', **PERSIST)
    st.number_input('Quiet interval in seconds', min_value=1.0, max_value=600.0, step=1.0,
                    value=3.0, key=PREFIX + 'watch_seconds', **PERSIST)
    enabled = st.checkbox('Automatically import stable files', key=PREFIX + 'watch_enabled', **PERSIST)
    if enabled:
        st.caption('Polling runs while Projects and imports is open. Files enter the import queue after the quiet interval.')


def _saved_comparison_settings():
    from .browser_app import _experiment_metadata

    return dict(active_table=st.session_state.get('selected_table'), plot_recipe=st.session_state.get('plot_recipe'),
                experiment_metadata=_experiment_metadata(),
                workbench_settings=capture_settings(st.session_state, excluded=ephemeral_widget_keys() | {
                    PREFIX + 'watch_enabled', PREFIX + 'source_paths', PREFIX + 'profile_path'}))


def _project_sessions(project, workspace):
    st.subheader('Comparison workspaces')
    busy = _queue_busy()
    name_input = st.text_input('New project name', key=PREFIX + 'new_name', placeholder='Comparison session', **PERSIST)
    if st.button('Create empty project', key=PREFIX + 'create_project', disabled=busy):
        created = _fresh(workspace, name_input.strip() or 'Comparison session')
        try:
            created.save()
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            _attach(workspace, created, created.root)
    open_path = st.text_input('Project JSON, checkpoint, or portable ZIP path', key=PREFIX + 'open_path', **PERSIST).strip()
    override = st.text_input('Project folder override', key=PREFIX + 'open_root',
                             help='For a moved project, choose the folder containing Measurements. For ZIP, choose a new extraction folder.',
                             **PERSIST).strip()
    if st.button('Open comparison project', key=PREFIX + 'open_project', disabled=not open_path or busy):
        try:
            path = Path(open_path).expanduser().resolve(strict=True)
            if path.suffix.lower() == '.zip' and not override:
                override = str(_fresh(workspace, path.stem).root)
            restored = MultiInstrumentProject.load(path, root=override or None)
        except Exception as error:
            st.error(str(error))
        else:
            _attach(workspace, restored, path)
    if project is None:
        return
    st.caption(str(project.root))
    path = st.text_input('Save project at a different JSON path', key=PREFIX + 'save_path', **PERSIST).strip()
    name = st.text_input('Project name', project.name, key=PREFIX + 'name_' + project.project_id, **PERSIST)
    if st.button('Save comparison workspace', key=PREFIX + 'save_project', disabled=busy):
        try:
            project.update_state(_saved_comparison_settings(), name=name)
            saved = project.save(path or None)
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            _audit(workspace, 'Save comparison workspace', outputs=(saved,))
            st.success(f'Saved {saved}')
    if project.checkpoint_path.is_file() and st.button('Recover latest checkpoint', key=PREFIX + 'recover', disabled=busy):
        try:
            restored = MultiInstrumentProject.load(project.checkpoint_path)
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            _attach(workspace, restored, project.checkpoint_path)
    with st.expander('Relocate project or original source paths'):
        destination = st.text_input('New project folder', key=PREFIX + 'relocate_project', **PERSIST).strip()
        previous = st.text_input('Previous raw path or folder', key=PREFIX + 'relocate_from', **PERSIST).strip()
        replacement = st.text_input('Replacement raw path or folder', key=PREFIX + 'relocate_to', **PERSIST).strip()
        if st.button('Apply relocation', key=PREFIX + 'relocate_button', disabled=busy or not (destination or (previous and replacement))):
            mapping = {project.root: destination} if destination else {}
            if previous and replacement:
                mapping[previous] = replacement
            try:
                previous_root = project.root
                project.relocate(mapping, state=_saved_comparison_settings())
            except (OSError, ValueError) as error:
                st.error(str(error))
            else:
                _changed(workspace, 'Relocate comparison project', project, inputs=(previous_root,), restore=True)
    if project.handles:
        with st.expander('Package selected sources'):
            available = {handle['id']: _source_label(handle) for handle in project.handles}
            selected = st.multiselect('Sources to package', list(available), default=list(available),
                                      format_func=available.__getitem__, key=PREFIX + 'portable_sources_' + project.project_id,
                                      **PERSIST)
            output = st.text_input('Portable ZIP destination', key=PREFIX + 'portable_path',
                                   placeholder=str(workspace / 'Workbench' / 'Comparison.zip'), **PERSIST).strip()
            if st.button('Export portable project', key=PREFIX + 'portable_export', disabled=busy or not output or not selected):
                try:
                    path = project.export_portable(output, selected=selected, state=_saved_comparison_settings())
                except (OSError, ValueError) as error:
                    st.error(str(error))
                else:
                    _audit(workspace, 'Export portable project', inputs=(project.root,), parameters={'sources': selected}, outputs=(path,))
                    st.success(f'Created {path}')


def _history_search(project, workspace):
    st.subheader('Cross-experiment search')
    folder = st.text_input('Search project folder', str(workspace / 'Workbench' / 'Projects'),
                           key=PREFIX + 'search_folder', **PERSIST).strip()
    query = st.text_input('Search names, tags, techniques, instruments, and annotations',
                          key=PREFIX + 'query', **PERSIST)
    if st.button('Search projects', key=PREFIX + 'search'):
        try:
            st.session_state['workbench_project_search'] = search_projects(folder, query, current=project)
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            _audit(workspace, 'Search experiments', inputs=(folder,), parameters={'query': query})
    if 'workbench_project_search' in st.session_state:
        result = st.session_state['workbench_project_search']
        if result:
            st.dataframe(result, hide_index=True)
        else:
            st.info('No measurements matched the search.')
    st.subheader('Analysis history')
    history = read_history(workspace)
    if history:
        st.dataframe(list(reversed(history)), hide_index=True)
    else:
        st.info('Analysis actions appear here after they run.')
    if project and project.history:
        st.subheader('Project edits')
        st.dataframe(list(reversed(project.history)), hide_index=True)


def render(data, *, workspace: Path):
    workspace = Path(workspace).expanduser().resolve()
    _consume_queue(workspace)
    project = st.session_state.get('multi_project')
    if project is not None:
        st.write(f'**{project.name}** · {len(project.handles)} sources')
        undo, redo = st.columns(2)
        if undo.button('Undo project edit', key=PREFIX + 'undo', disabled=not project.can_undo or _queue_busy()):
            try:
                project.undo()
            except (OSError, ValueError) as error:
                st.error(str(error))
            else:
                _changed(workspace, 'Undo project edit', project, restore=True)
        if redo.button('Redo project edit', key=PREFIX + 'redo', disabled=not project.can_redo or _queue_busy()):
            try:
                project.redo()
            except (OSError, ValueError) as error:
                st.error(str(error))
            else:
                _changed(workspace, 'Redo project edit', project, restore=True)
    section = st.radio('Project tools', ('Import sources', 'Organize', 'Watch folder', 'Projects and packages', 'Search and history'),
                       key=PREFIX + 'section', horizontal=True, **PERSIST)
    if section == 'Import sources':
        _import_sources(data, workspace)
    elif section == 'Organize':
        _organizer(project, workspace)
    elif section == 'Watch folder':
        _watch_settings()
    elif section == 'Projects and packages':
        _project_sessions(project, workspace)
    else:
        _history_search(project, workspace)
    _watch_tick(workspace, show=section == 'Watch folder')
    _queue_status()
