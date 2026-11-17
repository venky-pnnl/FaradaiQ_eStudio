"""Project credits and the bundled user guides."""
import json
import tkinter as tk
from tkinter import ttk
import webbrowser


def show_about(gui):
    from .estudio_ui import ROOT, TAGLINE, TITLE, apply_theme, set_icon

    existing = getattr(gui, '_estudio_about_window', None)
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        existing.focus_set()
        return existing

    docs = ROOT / 'docs'
    rows = json.loads((docs / 'credits.json').read_text(encoding='utf-8'))
    window = tk.Toplevel(gui)
    gui._estudio_about_window = window
    window.title(f'{TITLE} — Project credits')
    window.geometry('1010x600')
    window.minsize(760, 500)
    window.bind('<Escape>', lambda _event: window.destroy())
    set_icon(window)

    box = ttk.Frame(window, padding=(22, 18, 22, 20))
    box.pack(fill='both', expand=True)
    header = ttk.Frame(box)
    header.pack(fill='x')
    icon = getattr(gui, '_estudio_header_icon', None)
    if icon is not None:
        ttk.Label(header, image=icon).pack(side='left', padx=(0, 12))
    branding = ttk.Frame(header)
    branding.pack(side='left', fill='x', expand=True)
    ttk.Label(branding, text=TITLE, style='HeroTitle.TLabel').pack(anchor='w')
    ttk.Label(branding, text=TAGLINE, style='HeroSub.TLabel').pack(anchor='w', pady=(3, 0))
    ttk.Label(branding, text='FaradaIQ eStudio was designed by Venkateshkumar Prabhakaran.').pack(anchor='w', pady=(3, 0))
    ttk.Separator(box).pack(fill='x', pady=(15, 13))

    footer = ttk.Frame(box)
    footer.pack(side='bottom', fill='x', pady=(14, 0))
    homepage = (docs / 'homepage.html').as_uri()
    guides = (
        ('Workflow guide', homepage + '#from-source-files-to-results'),
        ('Integration setup', homepage + '#configure-integrations'),
        ('Notices and citations', (docs / 'THIRD_PARTY.md').as_uri()),
    )
    for name, url in guides:
        ttk.Button(footer, text=name, command=lambda target=url: webbrowser.open(target)).pack(side='left', padx=(0, 8))
    ttk.Button(footer, text='Close', command=window.destroy).pack(side='right')

    body = ttk.Panedwindow(box, orient='horizontal')
    body.pack(fill='both', expand=True)
    project_list = ttk.Frame(body, padding=(0, 0, 10, 0))
    detail = ttk.Labelframe(body, text='Project credits', padding=(18, 14))
    body.add(project_list, weight=1)
    body.add(detail, weight=2)
    projects = ttk.Treeview(project_list, show='tree headings', selectmode='browse')
    projects.heading('#0', text='Project', anchor='w')
    projects.column('#0', width=256, minwidth=150)
    scroll = ttk.Scrollbar(project_list, orient='vertical', command=projects.yview)
    projects.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    projects.pack(side='left', fill='both', expand=True)
    entries = {projects.insert('', 'end', text=row['project']): row for row in rows}

    project = tk.StringVar(master=window)
    people = tk.StringVar(master=window)
    contribution = tk.StringVar(master=window)
    project_label = ttk.Label(detail, textvariable=project, style='HeroTitle.TLabel', justify='left')
    project_label.pack(fill='x', pady=(0, 17))
    ttk.Label(detail, text='Contributors', style='Section.TLabel').pack(anchor='w')
    people_label = ttk.Label(detail, textvariable=people, justify='left')
    people_label.pack(fill='x', pady=(3, 14))
    ttk.Label(detail, text='Use in eStudio', style='Section.TLabel').pack(anchor='w')
    contribution_label = ttk.Label(detail, textvariable=contribution, justify='left')
    contribution_label.pack(fill='x', pady=(3, 16))

    def reflow(event):
        for label in (project_label, people_label, contribution_label):
            label.configure(wraplength=max(120, event.width - 40))

    detail.bind('<Configure>', reflow)
    actions = ttk.Frame(detail)
    actions.pack(fill='x')

    def selected_credit():
        selection = projects.selection()
        return entries.get(selection[0], {}) if selection else {}

    def open_link(key):
        url = selected_credit().get(key)
        if url:
            location = url if url.startswith('https://') else (docs / url).resolve().as_uri()
            webbrowser.open(location)

    open_project = ttk.Button(actions, text='Open project', command=lambda: open_link('url'))
    open_project.pack(side='left', padx=(0, 8))
    open_documentation = ttk.Button(actions, text='Documentation', command=lambda: open_link('documentation'))
    open_documentation.pack(side='left')

    def update_details(_event=None):
        credit = selected_credit()
        project.set(credit.get('project', 'Select a project'))
        people.set(credit.get('people', ''))
        contribution.set(credit.get('role', ''))
        open_project.state(['!disabled'] if credit.get('url') else ['disabled'])
        open_documentation.state(['!disabled'] if credit.get('documentation') else ['disabled'])

    projects.bind('<<TreeviewSelect>>', update_details)
    projects.bind('<Return>', lambda _event: open_link('url'))

    def open_at_pointer(event):
        item = projects.identify_row(event.y)
        if item:
            projects.selection_set(item)
            open_link('url')

    projects.bind('<Double-1>', open_at_pointer)
    first = next(iter(entries), None)
    if first:
        projects.selection_set(first)
        projects.focus(first)
    update_details()
    projects.focus_set()
    theme = getattr(gui, 'estudio_theme_var', None)
    apply_theme(window, theme.get() if theme is not None else 'Dark')
    return window
