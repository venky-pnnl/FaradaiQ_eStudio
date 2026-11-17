"""Session actions and a lazy results navigator for the eStudio sidebar."""
from __future__ import annotations

import json
import os
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from tkinter import filedialog, messagebox, ttk

HDF5_SUFFIXES = {'.h5', '.hdf5'}


class ResultsNavigator:
    def __init__(self, parent, gui, core, current_browser):
        self.gui = gui
        self.core = core
        self.current_browser = current_browser
        self.paths = {}
        self.pending = set()
        self.completed = Queue()
        self.chosen_root = None
        self.tree = ttk.Treeview(parent, show='tree', selectmode='browse',
                                 style='E.Results.Treeview', height=3)
        self.tree.column('#0', width=216, minwidth=60)
        self.tree.bind('<<TreeviewOpen>>', self.expand)
        self.tree.bind('<Return>', self.open_selected)
        self.tree.bind('<Double-1>', self.open_bundle_at_pointer)
        self.reset()
        if self.root_item in self.paths:
            self.tree.item(self.root_item, open=True)
            self.load_children(self.root_item)
        gui.output_dir_var.trace_add('write', self.follow_output)

    def follow_output(self, *_):
        self.chosen_root = None
        self.reset()

    def reset(self):
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.paths.clear()
        selected = self.chosen_root or self.gui.output_dir_var.get().strip()
        if selected:
            path = Path(selected).expanduser()
            caption = path.name or str(path)
            self.root_item = self.add_path('', path, True, caption)
        else:
            self.root_item = self.tree.insert('', 'end', text='Choose a results folder')

    def choose_folder(self):
        selected = filedialog.askdirectory(parent=self.gui, title='Choose a results folder')
        if selected:
            self.chosen_root = selected
            self.reset()
            self.tree.selection_set(self.root_item)
            self.tree.focus(self.root_item)
            self.tree.item(self.root_item, open=True)
            self.load_children(self.root_item)

    def add_path(self, parent, path, is_directory, caption):
        item = self.tree.insert(parent, 'end', text=caption)
        self.paths[item] = (path, is_directory)
        if is_directory:
            self.tree.insert(item, 'end', text='Expand to browse')
        return item

    def expand(self, _event=None):
        self.load_children(self.tree.focus())

    def clear_children(self, item):
        for child in self.tree.get_children(item):
            self.clear_children(child)
            self.paths.pop(child, None)
            self.tree.delete(child)

    def load_children(self, item):
        entry = self.paths.get(item)
        if not entry or not entry[1] or item in self.pending:
            return
        self.clear_children(item)
        self.tree.insert(item, 'end', text='Reading folder…')
        if not self.pending:
            self.tree.after(50, self.finish_reads)
        self.pending.add(item)
        Thread(target=self.read_folder, args=(item, entry[0]), daemon=True).start()

    def read_folder(self, item, path):
        rows = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name.startswith('.'):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        rows.append((Path(entry.path), True))
                    elif Path(entry.name).suffix.lower() in HDF5_SUFFIXES and entry.is_file():
                        rows.append((Path(entry.path), False))
            rows.sort(key=lambda row: (not row[1], row[0].name.casefold()))
        except OSError as exc:
            self.completed.put((item, [], exc.strerror or 'Could not read folder'))
        else:
            self.completed.put((item, rows, None))

    def finish_reads(self):
        while True:
            try:
                item, rows, error = self.completed.get_nowait()
            except Empty:
                break
            self.pending.discard(item)
            if not self.tree.exists(item):
                continue
            self.clear_children(item)
            for path, is_directory in rows:
                self.add_path(item, path, is_directory, path.name)
            if error or not rows:
                self.tree.insert(item, 'end', text=error or 'No folders or HDF5 bundles')
        if self.pending:
            self.tree.after(50, self.finish_reads)

    def open_bundle_at_pointer(self, event):
        item = self.tree.identify_row(event.y)
        if item in self.paths and not self.paths[item][1]:
            self.open_path(self.paths[item][0])
            return 'break'

    def open_selected(self, event=None):
        selected = self.tree.selection()
        item = selected[0] if selected else ''
        if item in self.paths and (item != self.root_item or self.chosen_root):
            self.open_path(self.paths[item][0])
        else:
            self.current_browser()
        if event is not None:
            return 'break'

    def open_path(self, path, base_name=None, on_open=None):
        label = path.stem if path.suffix.lower() in HDF5_SUFFIXES else path.name
        label = label.removesuffix('_faradaiq_bundle').removesuffix('_analysis_ready_csv')
        return self.core['_v138_prepare_and_open_browser_async'](
            self.gui, root=path, base_name=base_name or label, on_open=on_open
        )

    def load_browser_session(self):
        selected = filedialog.askopenfilename(
            parent=self.gui, title='Load Browser Session',
            filetypes=[('Browser session JSON', '*.json'), ('All files', '*')],
        )
        if not selected:
            return
        session_path = Path(selected)
        try:
            obj = json.loads(session_path.read_text(encoding='utf-8-sig'))
            if not isinstance(obj, dict) or not isinstance(obj.get('plot_views'), list):
                raise ValueError('Choose a saved Data Browser session JSON.')
            schema = str(obj.get('schema', ''))
            legacy = schema == 'faradaiq_browser_session_v106'
            if not legacy and not schema.startswith('faradaiq-h5-browser-session-'):
                raise ValueError('Choose a Data Browser session saved from the Data Browser window.')

            def restore(browser):
                try:
                    if legacy:
                        self.core['_v106_apply_browser_session'](browser, obj)
                    else:
                        self.core['_v138_apply_browser_session_payload'](browser, obj)
                    self.gui.set_status(f'Loaded browser session: {session_path.name}')
                except (TypeError, ValueError) as exc:
                    messagebox.showerror('Load Browser Session', str(exc), parent=self.gui)

            for key in ('h5_path', 'analysis_root'):
                value = obj.get(key)
                if value:
                    path = Path(value)
                    candidate = path if path.is_absolute() else session_path.parent / path
                    if candidate.exists():
                        self.open_path(candidate, obj.get('base_name'), restore)
                        return
                    sibling = session_path.parent / path.name
                    if sibling.exists():
                        self.open_path(sibling, obj.get('base_name'), restore)
                        return
            cache_root = obj.get('cache_root')
            if cache_root and Path(cache_root).is_dir():
                browser = self.core['_v138_open_browser_from_cache'](
                    self.gui, {'cache_root': cache_root}, obj.get('base_name'))
                if browser is not None:
                    restore(browser)
                return
            bundle = filedialog.askopenfilename(
                parent=self.gui, title='Locate the HDF5 bundle for this browser session',
                filetypes=[('HDF5 bundle', '*.h5 *.hdf5'), ('All files', '*')],
            )
            if bundle:
                self.open_path(Path(bundle), obj.get('base_name'), restore)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror('Load Browser Session', str(exc), parent=self.gui)
