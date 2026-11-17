"""FaradaIQ Studio v291 release identity, DRT eligibility, Help Center, and visible plot controls.

This module is deliberately installed last.  It centralizes every public
release string, prevents DRT execution without verified EIS spectra, and
replaces the accumulated historical Help tabs with a task-oriented guide, and guarantees readable Matplotlib controls in every theme.
"""
from __future__ import annotations

import csv
import html
import json
import math
import os
import re
import tempfile
import webbrowser
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

VERSION = "v291"
VERSION_NUMBER = 291
SEMVER = "0.291.0"
APP_NAME = "FaradaIQ Studio"
APP_TITLE = f"{APP_NAME} {VERSION}"
INSTALL_TARGET = rf"C:\FaradaIQ\Studio\V{VERSION_NUMBER}"

_VERSION_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.])[vV](?:1\d{2}|2\d{2})(?![A-Za-z0-9_.])")
_FARADAIQ_VERSION_RE = re.compile(
    r"(?i)FaradaIQ(?:\s+Studio)?\s+[vV](?:1\d{2}|2\d{2})(?:\.\d+)*"
)


def normalize_release_text(value: Any) -> Any:
    """Normalize release-facing text without changing paths or arbitrary data.

    The function is intentionally conservative for generic strings.  Every
    explicit ``FaradaIQ ... vNNN`` reference is normalized.  Standalone vNNN
    tokens are normalized only in clearly release-facing labels.
    """
    if not isinstance(value, str) or not value:
        return value
    text = _FARADAIQ_VERSION_RE.sub(APP_TITLE, value)
    low = text.lower()
    release_facing = (
        "faradaiq" in low
        or "about" in low
        or "credit" in low
        or "help center" in low
        or low.startswith("version")
        or low.startswith("v")
        or "release" in low
    )
    if release_facing:
        text = _VERSION_TOKEN_RE.sub(VERSION, text)
    return text


def _split_columns(line: str) -> list[str]:
    if "\t" in line:
        return [part.strip().strip('"') for part in line.split("\t")]
    return [part.strip().strip('"') for part in line.split(",")]


def _column_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _find_eis_columns(headers: Sequence[str]) -> tuple[int, int, int] | None:
    keys = [_column_key(x) for x in headers]
    f_idx = zr_idx = zi_idx = None
    for idx, key in enumerate(keys):
        if f_idx is None and (
            key in {"frequency", "frequencyhz", "freq", "freqhz", "f2hz", "fhz"}
            or ("frequency" in key and "log" not in key)
        ):
            f_idx = idx
        if zr_idx is None and ("zreal" in key or "realz" in key):
            zr_idx = idx
        if zi_idx is None and ("zimag" in key or "imagz" in key):
            zi_idx = idx
    if None in (f_idx, zr_idx, zi_idx):
        return None
    return int(f_idx), int(zr_idx), int(zi_idx)


def _float(value: Any) -> float | None:
    try:
        out = float(str(value).strip().strip('"'))
        return out if math.isfinite(out) else None
    except Exception:
        return None


def valid_eis_file(path: str | Path, minimum_rows: int = 3) -> bool:
    """Return True only for a table containing real EIS frequency/Z'/Z'' data."""
    p = Path(path)
    if not p.is_file() or p.suffix.lower() not in {".csv", ".txt", ".tsv", ".asc", ".dat"}:
        return False
    try:
        lines = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except Exception:
        try:
            lines = p.read_text(encoding="windows-1252", errors="replace").splitlines()
        except Exception:
            return False
    if not lines:
        return False

    header_columns: tuple[int, int, int] | None = None
    header_index = -1
    for idx, line in enumerate(lines[:240]):
        if not line.strip():
            continue
        columns = _split_columns(line)
        found = _find_eis_columns(columns)
        if found is not None:
            header_columns = found
            header_index = idx
            break
    if header_columns is None:
        return False

    f_idx, zr_idx, zi_idx = header_columns
    needed = max(header_columns)
    rows: list[tuple[float, float, float]] = []
    for line in lines[header_index + 1 : header_index + 5000]:
        if not line.strip():
            continue
        cols = _split_columns(line)
        if len(cols) <= needed:
            continue
        # Repeated legacy headers are skipped because their values are nonnumeric.
        f = _float(cols[f_idx])
        zr = _float(cols[zr_idx])
        zi = _float(cols[zi_idx])
        if f is None or zr is None or zi is None or f <= 0:
            continue
        rows.append((f, zr, zi))
        if len(rows) >= max(12, minimum_rows):
            break
    if len(rows) < minimum_rows:
        return False
    return len({round(row[0], 12) for row in rows}) >= minimum_rows


def valid_eis_files(paths: Iterable[str | Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for raw in paths or []:
        try:
            p = Path(raw).resolve()
            key = os.path.normcase(str(p))
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        if valid_eis_file(p):
            out.append(p)
    return out


def valid_drt_input_dir(path: str | Path) -> bool:
    p = Path(path)
    if not p.is_dir():
        return False
    try:
        candidates = sorted(
            q for q in p.iterdir() if q.is_file() and q.suffix.lower() in {".csv", ".txt", ".tsv"}
        )
    except Exception:
        return False
    return bool(valid_eis_files(candidates))


def _is_drt_command(args: Sequence[Any]) -> bool:
    parts = [str(x).lower() for x in (args or [])]
    joined = " ".join(parts)
    return (
        "headless_pydrttools" in joined
        or "pydrttools.py" in joined
        or ("--run_types" in parts and "drt" in joined)
    )


def _filter_drt_commands(
    commands: Sequence[tuple[Sequence[Any], Any]],
    notes: Sequence[str],
    detected: Mapping[str, Any] | None,
) -> tuple[list[tuple[Sequence[Any], Any]], list[str], Mapping[str, Any] | None]:
    detected = detected or {}
    eis_paths = valid_eis_files(detected.get("eis", []) or [])
    drt_dirs = [Path(x) for x in (detected.get("drt_dir", []) or []) if Path(x).is_dir()]
    valid_dirs = [d for d in drt_dirs if valid_drt_input_dir(d)]
    eligible = bool(eis_paths and valid_dirs)

    kept: list[tuple[Sequence[Any], Any]] = []
    removed = 0
    for command in commands or []:
        args = command[0] if command else []
        if _is_drt_command(args):
            command_dir = next((Path(str(x)) for x in args if Path(str(x)).is_dir()), None)
            if not eligible or (command_dir is not None and command_dir not in valid_dirs):
                removed += 1
                continue
        kept.append(command)

    out_notes = list(notes or [])
    if removed or (drt_dirs and not eligible):
        if not eis_paths:
            reason = (
                "DRT skipped: no valid EIS spectrum with frequency, Z-real, and Z-imaginary columns "
                "was detected. Enabling DRT never routes CA, CP, CV, OCP, or other non-EIS data "
                "through the DRT parser."
            )
        else:
            reason = (
                "DRT skipped: EIS was detected, but no valid DRT input directory containing "
                "frequency/Z-real/Z-imaginary spectra passed validation."
            )
        if reason not in out_notes:
            out_notes.append(reason)
    return kept, out_notes, detected


_HELP_PAGES: list[tuple[str, str]] = [
    (
        "Start here",
        """# Start here

FaradaIQ Studio {version} is organized around one repeatable workflow.

1. Install and start Studio.
2. Load and inspect the raw experiment or an existing canonical H5/JSON bundle.
3. Review the experiment description and detected techniques.
4. Export the canonical H5/JSON package when the source is raw.
5. Open Data Browser and select the exact point, method, cycle, or analysis result.
6. Run a technique-aware local analysis or prepare a reviewed SciLink/Edison handoff.
7. Inspect plots, tables, warnings, assumptions, and generated reports.
8. Save the Studio session and Browser Plot/views.
9. Export final figures, tables, reports, and the audit trail.

The status line and background-progress window report what is running. A feature being enabled does not mean it is valid for every dataset. FaradaIQ first verifies the detected technique and required columns.
""",
    ),
    (
        "Main Studio",
        """# Main Studio

## Load and inspect
Choose a raw experiment, output folder, completed job, session, or canonical H5/JSON bundle. Load and inspect builds the experiment summary and technique map without changing the raw source.

## Experiment description
Enter the scientific context that is not encoded in the files, including sample, electrolyte, electrode, concentration, geometry, applied conditions, spatial sequence, and known regions. This description is reused in reviewed AI handoffs.

## Export
Export archive + CSV creates the canonical numeric package and analysis-ready tables. Auto-run schedules only parsers compatible with the detected data. DRT requires verified EIS spectra and is skipped for CA, CP, CV, OCP, and other non-EIS datasets.

Use shortened names for generated folders and files is enabled by default. It limits generated path components while leaving raw source names unchanged. Every rename is recorded in short_name_map.json and in the analysis manifest.

## Jobs
Experiment export jobs run independently and keep their own log, progress, output folder, and restorable session. Loading a completed job must not replace or hide an active job.
""",
    ),
    (
        "Browser and selection",
        """# Data Browser and selection

## Experiment map
The tree is grouped by technique, method, point, cycle, scan rate, analysis run, and engine. Expanding a branch should not load every descendant table. Selecting a leaf updates the preview asynchronously.

## What a selection means
A branch selection represents the records under that branch. A file or curve selection represents the exact numeric table. Review the selected-data line before analysis or handoff.

## Plot/views
Use Plot/views to save a comparison definition without duplicating raw data. Keep separate Plot/views for publication overlays, spatial comparisons, cycles, scan rates, and fitted results.

## SciLink handoff
Browser handoff prepares a technique-specific data bundle and compact JSON structure. Both generated items are checked by default in Files to Share. The compact JSON includes the Main Studio experiment description, columns, units, series metadata, and relative filenames.
""",
    ),
    (
        "Technique analysis",
        """# Technique-aware analysis

## Voltammetry
CV, LSV, pulse, stripping, and Tafel handoffs use potential_V and current_A. Cycle, branch, point, method, and scan rate remain metadata rather than fitted signal columns.

## Chrono techniques
CA uses time_s and current_A. CP, OCP, and galvanostatic charge-discharge use time_s and potential_V. CA analysis can integrate charge, summarize transient metrics, compare spatial trends, and fit Scharifker-Hills or other reviewed nucleation/transient models.

## EIS and DRT
EIS requires frequency_Hz, z_real_ohm, and z_imag_ohm. DRT requires actual EIS input and produces tau_s/gamma_ohm results. DRT is never inferred merely because its checkbox is enabled or a folder name contains DRT.

## Data-quality rule
If required columns are missing, constant, nonnumeric, or scientifically incompatible, FaradaIQ blocks or skips that analysis and explains why. It does not silently substitute another column.
""",
    ),
    (
        "SciLink, Edison, LabRIC",
        """# SciLink, Edison Scientific, and LabRIC MCP

## Ask SciLink
The general Ask SciLink button opens a blank reviewed prompt for the current Browser selection. The Ask SciLink: CA/CP analysis button opens the same review window with the editable CA charge/transient template prefilled. It does not run a separate hidden analyzer or start SciLink without approval.

The review window opens in front of the active Browser immediately and shows the generated technique-specific data bundle and compact JSON checked by default. Optional supporting files can be added explicitly from Files to Share. Technique templates may be edited and appended only when the user explicitly chooses them.

When shortened names are enabled, the compact JSON and bundle include original_name to short_name mappings. short_name_map.json states that the short filename is only a filesystem alias, and requests short names for newly generated reports, figures, workbooks, and subfolders.

SciLink runs as an interactive session. Questions must wait for user input. Generated HTML reports open automatically after they are completely written.

## Literature analysis
The literature-analysis checkbox controls literature use for the SciLink session. Claims should be cited and separated from measurements and assumptions.

## LabRIC MCP cloud context
Use LabRIC MCP cloud context for cross-instrument comparison, prior-data analysis, and experiment planning. This is separate from literature analysis and requires a successful MCP connection.

## Edison Scientific
Ask Edison Scientific uses the reviewed files and exact prompt. Upload receipts, task ID, response, failure state, and output files remain in the job folder.

## Direct LLM tools
LLM-guided prompt, Smart Suggestions, AI Chat, and Ask LLM are text/context tools and remain available in every launch mode. Experimental files are transferred only through the reviewed SciLink or Edison workflow.
""",
    ),
    (
        "Plots and maps",
        """# Plots, contours, maps, and cubes

## Technique defaults
CV opens as current versus potential. CA opens as current versus time. CP and OCP open as potential versus time. EIS offers Nyquist and Bode views. DRT uses gamma versus tau and spatial/slider views when a series exists.

## Contour viewer
Contour frames are pre-rendered before the final viewer opens and reused from cache when source and settings are unchanged. The slider supports manual dragging, Previous/Next, Play/Pause, and keyboard movement.

Auto rescale each frame recomputes limits from the active finite frame. Fixed-global mode uses one range for all frames. User-defined mode keeps the entered range. Color bars show intermediate tick labels.

## Publication output
Review axis units, signs, normalization, masks, interpolation, contour range, legends, and aspect ratio before export. Preserve raw data and save processed derivatives separately.
""",
    ),
    (
        "Sessions, jobs, export",
        """# Sessions, jobs, and export

## Main session
Save Session records source, output folder, experiment description, safe-to-persist settings, and workflow references. Auto Save maintains a recoverable copy after meaningful changes.

## Browser session
Save Browser session stores tree context, Plot/views, axes, legends, selections, and visualization state. It does not duplicate the canonical raw numeric package.

## Job windows
Live transactions and Experiment export jobs always display the same public release number as Main Studio. Each job has a unique folder and restorable FaradaIQ session.

The Main Studio and Experiment export jobs windows each provide a default-enabled shortened-name checkbox. The job setting is saved with the job. FaradaIQ applies it to generated base names, nested export folders, files, figures, reports, and job-local AI handoff copies. Raw source files are never renamed.

## Portable HTML data browser
Create portable HTML data browser is enabled by default in Main Studio and Experiment export jobs. FaradaIQ writes FaradaIQ_Data_Browser.html in the experiment root above the analysis-ready/processed tree. The first page compares all point curves and provides overlay, waterfall, small-multiple, and metric-versus-point modes with axis selection, point selection, normalization, zoom, pan, hover readout, and PNG export.

Plot previews are sampled across the complete authoritative processed tables. Photos, animated GIFs, and generated figures are shown in a dedicated gallery and are never parsed as CSV data. The Dataset browser provides individual plot/image/table previews, scientific metadata, direct relative links, and pre-built technique/method/point ZIPs under browser_downloads.

Copy or share the complete experiment folder. The browser requires no FaradaIQ installation, Python, web server, internet connection, or external JavaScript. Do not move the HTML file by itself. The portable JSON and every batch ZIP manifest preserve original scientific names and explain shortened physical aliases.

## Name mapping
Each affected export writes short_name_map.json. Analysis manifests, the portable browser manifest, batch ZIP manifests, and SciLink/Edison metadata contain both original scientific identity and physical aliases, the deterministic readable-prefix plus hash algorithm, and instructions for interpreting the aliases.

## Final archive
Archive the raw source, canonical H5/JSON, compact manifests, sessions, selected analysis tables, plots, AI transaction folders, reports, and final conclusions together.
""",
    ),
    (
        "Safety and troubleshooting",
        """# Safety and troubleshooting

## Scientific safeguards
Do not interpret a successful numerical fit as proof of mechanism. Review units, electrode area, concentration, scan rate, sign convention, fitting window, residuals, uncertainty, spatial metadata, and model assumptions.

## DRT ran on non-EIS data
This is blocked in {version}. DRT is eligible only when FaradaIQ validates both an EIS source and DRT input tables with frequency, Z-real, and Z-imaginary columns. Rebuild an old cache or export if stale DRT folders remain from a different experiment.

## Wrong signal columns
Open the compact SciLink JSON and confirm the canonical contract. CA must be time/current, CP and OCP time/potential, voltammetry potential/current, EIS frequency/Z-real/Z-imaginary, and DRT tau/gamma.

## Browser is slow
Use the canonical H5 cache, avoid unrelated files in the experiment root, and let the selection preview finish asynchronously. Repeated selections use cached summaries.


## Technique-aware PowerPoint summary
Create PowerPoint data summary is enabled by default in Main Studio and Experiment export jobs. FaradaIQ writes FaradaIQ_Data_Summary.pptx in the same experiment root as FaradaIQ_Data_Browser.html. Comprehensive mode includes every scientifically valid user-facing comparison view supported by the available columns, plus coverage, trends, analyses, media, review flags, conclusions, verification steps, and provenance.

Use Direct LLM for PowerPoint discussion is also enabled by default. The Direct LLM receives only a compact JSON summary of coverage, comparison labels, and derived metrics. Raw experimental files are not uploaded. Observations, interpretations, limitations, recommended checks, prompt, response, and status are retained under ppt_summary_assets. If the LLM is unavailable, FaradaIQ still produces a deterministic deck. Unfamiliar analysis acronyms such as EAL, LSB, and AFL are preserved without invented expansions.

## Windows destination path too long
Keep Use shortened names for generated folders and files enabled in Main Studio and Experiment export jobs. New outputs use bounded deterministic names and preserve the full names in short_name_map.json. Existing folders created with older releases are not renamed automatically.

## Report an issue
Include FaradaIQ Studio {version}, exact steps, screenshot, source family, minimal allowed sample, session ID, transaction log, and manifest. Remove credentials.
""",
    ),
    (
        "Release and credits",
        """# Release and credits

FaradaIQ Studio {version}
Electrochemical Intelligence for Analyze, Control and Discover

Designed by Venky Prabhakaran and powered by LLM.

Key integrated or supported projects include pySECCM parser/plot tools, pyDRTtools, DearEIS, PySimpleEChem, SciLink, Edison Scientific, and LabRIC MCP. Their original licenses and project credits remain applicable.

Controlled Windows installation target:
{install_target}

All Main Studio, Browser, About/Credits, Help, export-job, transaction, and analysis-window titles are normalized from the same release identity.
""",
    ),
]


def _help_pages() -> list[tuple[str, str]]:
    return [
        (name, content.format(version=VERSION, install_target=INSTALL_TARGET).strip())
        for name, content in _HELP_PAGES
    ]


def _render_help_text(widget: Any, content: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    for raw in content.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            widget.insert("end", line[3:] + "\n", "h2")
        elif line.startswith("# "):
            widget.insert("end", line[2:] + "\n", "h1")
        elif re.match(r"^\d+\.\s", line):
            widget.insert("end", line + "\n", "step")
        else:
            widget.insert("end", line + "\n")
    widget.configure(state="disabled")


def _install_help(core: MutableMapping[str, Any]) -> None:
    Session = core.get("SessionReaderGUI")
    tk = core.get("tk")
    ttk = core.get("ttk")
    ScrolledText = core.get("ScrolledText")
    if Session is None or tk is None or ttk is None or ScrolledText is None:
        return

    def open_help(self: Any) -> None:
        win = tk.Toplevel(self)
        win.title(f"{APP_TITLE} — Help")
        win.geometry("1120x760")
        win.minsize(900, 620)
        try:
            apply_theme = core.get("_v36_apply_theme")
            if callable(apply_theme):
                apply_theme(win, getattr(self, "_faradaiq_theme_mode", "dark"))
        except Exception:
            pass

        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(2, weight=1)

        ttk.Label(outer, text=f"{APP_TITLE} Help", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            outer,
            text="Workflow-first guidance. Select one topic instead of searching through accumulated release notes.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))

        nav_frame = ttk.Frame(outer, padding=(0, 0, 10, 0))
        nav_frame.grid(row=2, column=0, sticky="nsew")
        nav_frame.rowconfigure(2, weight=1)
        ttk.Label(nav_frame, text="Topics", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        search_var = tk.StringVar()
        search = ttk.Entry(nav_frame, textvariable=search_var, width=29)
        search.grid(row=1, column=0, sticky="ew", pady=(5, 8))
        topics = tk.Listbox(nav_frame, width=31, exportselection=False)
        topics.grid(row=2, column=0, sticky="nsew")

        content_frame = ttk.Frame(outer)
        content_frame.grid(row=2, column=1, sticky="nsew")
        content_frame.columnconfigure(0, weight=1)
        content_frame.rowconfigure(0, weight=1)
        text = ScrolledText(content_frame, wrap="word", padx=20, pady=16, undo=False)
        text.grid(row=0, column=0, sticky="nsew")
        text.tag_configure("h1", font=("Segoe UI", 18, "bold"), spacing1=4, spacing3=10)
        text.tag_configure("h2", font=("Segoe UI", 12, "bold"), spacing1=12, spacing3=4)
        text.tag_configure("step", lmargin1=14, lmargin2=30, spacing1=2, spacing3=2)
        text.tag_configure("found", background="#facc15", foreground="#111827")

        pages = _help_pages()
        visible_indices = list(range(len(pages)))
        status = tk.StringVar(value="Select a topic. Search filters topic names and content.")

        def show_page(page_index: int) -> None:
            name, content = pages[page_index]
            _render_help_text(text, content)
            status.set(name)

        def rebuild_topics(*_args: Any) -> None:
            query = search_var.get().strip().lower()
            visible_indices.clear()
            topics.delete(0, "end")
            for idx, (name, content) in enumerate(pages):
                if not query or query in name.lower() or query in content.lower():
                    visible_indices.append(idx)
                    topics.insert("end", name)
            if visible_indices:
                topics.selection_set(0)
                topics.activate(0)
                show_page(visible_indices[0])
                if query:
                    text.configure(state="normal")
                    start = "1.0"
                    matches = 0
                    while True:
                        pos = text.search(query, start, stopindex="end", nocase=True)
                        if not pos:
                            break
                        end = f"{pos}+{len(query)}c"
                        text.tag_add("found", pos, end)
                        start = end
                        matches += 1
                    text.configure(state="disabled")
                    status.set(f"{len(visible_indices)} topic(s) matched; {matches} match(es) highlighted in this topic.")
            else:
                _render_help_text(text, "# No matching help topic\n\nTry a broader term such as SciLink, DRT, Browser, export, CA, or session.")
                status.set("No matching topic.")

        def on_select(_event: Any = None) -> None:
            selection = topics.curselection()
            if selection:
                show_page(visible_indices[int(selection[0])])

        def copy_topic() -> None:
            try:
                win.clipboard_clear()
                win.clipboard_append(text.get("1.0", "end-1c"))
                status.set("Copied the current help topic.")
            except Exception as exc:
                status.set(f"Copy failed: {exc}")

        def printable() -> None:
            try:
                sections = []
                for name, body in pages:
                    sections.append(f"<section><h1>{html.escape(name)}</h1><pre>{html.escape(body)}</pre></section>")
                page = (
                    "<!doctype html><html><head><meta charset='utf-8'>"
                    f"<title>{html.escape(APP_TITLE)} Help</title>"
                    "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:1080px;margin:30px auto;padding:0 24px;line-height:1.5;color:#172033}"
                    "section{border-bottom:1px solid #ccd4df;padding:12px 0 28px}pre{font:inherit;white-space:pre-wrap}h1{color:#164e86}</style>"
                    "</head><body>" + "".join(sections) + "</body></html>"
                )
                folder = Path(tempfile.gettempdir()) / "faradaiq_v291_help"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / "FaradaIQ_Studio_v291_Help.html"
                path.write_text(page, encoding="utf-8")
                webbrowser.open(path.resolve().as_uri())
                status.set(f"Opened printable help: {path}")
            except Exception as exc:
                status.set(f"Printable help failed: {exc}")

        footer = ttk.Frame(outer)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        ttk.Label(footer, textvariable=status, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="Copy topic", command=copy_topic).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="Printable HTML", command=printable).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="Close", command=win.destroy).pack(side="right")

        topics.bind("<<ListboxSelect>>", on_select)
        search_var.trace_add("write", rebuild_topics)
        rebuild_topics()
        try:
            self.set_status(f"Opened {APP_TITLE} workflow Help.")
        except Exception:
            pass

    Session.open_help_guide = open_help


def _install_about(core: MutableMapping[str, Any]) -> None:
    Session = core.get("SessionReaderGUI")
    tk = core.get("tk")
    ttk = core.get("ttk")
    if Session is None or tk is None or ttk is None:
        return

    def show_about(self: Any) -> None:
        win = tk.Toplevel(self)
        win.title(f"About / credits — {APP_TITLE}")
        win.geometry("720x620")
        win.minsize(620, 520)
        try:
            apply_theme = core.get("_v36_apply_theme")
            if callable(apply_theme):
                apply_theme(win, getattr(self, "_faradaiq_theme_mode", "dark"))
        except Exception:
            pass
        outer = ttk.Frame(win, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Electrochemical Intelligence for Analyze, Control and Discover",
            style="Subtitle.TLabel",
            wraplength=650,
        ).pack(anchor="w", pady=(4, 14))
        ttk.Label(
            outer,
            text=(
                "Designed by Venky Prabhakaran and powered by LLM.\n\n"
                f"Version: {VERSION}\n"
                f"Controlled Windows installation: {INSTALL_TARGET}\n\n"
                "FaradaIQ Studio provides electrochemical data inspection, canonical H5/JSON packaging, "
                "technique-aware analysis, plotting, sessions, jobs, SciLink/Edison handoff, and LabRIC MCP context.\n\n"
                "Project credits\n"
                "• pySECCM parser and plotting tools: Benjamin A Helfrecht\n"
                "• pyDRTtools: Ciucci Lab\n"
                "• DearEIS and PySimpleEChem integrations retain their original project licenses and credits\n"
                "• SciLink, Edison Scientific, and LabRIC MCP retain their respective service/project terms\n\n"
                "All public GUI windows use the same centralized release identity."
            ),
            justify="left",
            wraplength=650,
        ).pack(anchor="w", fill="x")
        ttk.Button(outer, text="Close", command=win.destroy).pack(anchor="e", pady=(18, 0))

    Session.show_about_credits = show_about


def _normalize_widget_tree(widget: Any) -> None:
    try:
        current = widget.title()
        normalized = normalize_release_text(current)
        if normalized != current:
            widget.title(normalized)
    except Exception:
        pass
    try:
        if "text" in widget.keys():
            current = widget.cget("text")
            normalized = normalize_release_text(current)
            if normalized != current:
                widget.configure(text=normalized)
    except Exception:
        pass
    try:
        # Notebook tabs and menu entries have text outside the normal widget option.
        if widget.winfo_class() == "TNotebook":
            for tab in widget.tabs():
                current = widget.tab(tab, "text")
                normalized = normalize_release_text(current)
                if normalized != current:
                    widget.tab(tab, text=normalized)
    except Exception:
        pass
    try:
        if widget.winfo_class() == "Menu":
            end = widget.index("end")
            if end is not None:
                for idx in range(int(end) + 1):
                    try:
                        current = widget.entrycget(idx, "label")
                        normalized = normalize_release_text(current)
                        if normalized != current:
                            widget.entryconfigure(idx, label=normalized)
                    except Exception:
                        pass
    except Exception:
        pass
    try:
        for child in widget.winfo_children():
            _normalize_widget_tree(child)
    except Exception:
        pass


def _schedule_normalization(widget: Any) -> None:
    for delay in (0, 30, 150, 500, 1200, 3000, 7000):
        try:
            widget.after(delay, lambda w=widget: _normalize_widget_tree(w))
        except Exception:
            break


def _install_runtime_version_normalization(core: MutableMapping[str, Any]) -> None:
    tk = core.get("tk")
    Session = core.get("SessionReaderGUI")
    Browser = core.get("FaradaIQDataBrowser")
    messagebox = core.get("messagebox")
    if tk is None:
        return

    # Window-manager titles are normalized at the API boundary, including titles
    # supplied by older feature modules and delayed callbacks.
    if not getattr(tk.Wm, "_faradaiq_v291_title_patch", False):
        original_wm_title = tk.Wm.wm_title

        def wm_title(self: Any, string: Any = None):
            if string is None:
                return original_wm_title(self)
            return original_wm_title(self, normalize_release_text(string))

        tk.Wm.wm_title = wm_title
        tk.Wm.title = wm_title
        tk.Wm._faradaiq_v291_title_patch = True

    if not getattr(tk.Toplevel, "_faradaiq_v291_init_patch", False):
        original_top_init = tk.Toplevel.__init__

        def top_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_top_init(self, *args, **kwargs)
            _schedule_normalization(self)

        tk.Toplevel.__init__ = top_init
        tk.Toplevel._faradaiq_v291_init_patch = True

    def wrap_init(cls: Any, marker: str) -> None:
        if cls is None or getattr(cls, marker, False):
            return
        previous = cls.__init__

        def init(self: Any, *args: Any, **kwargs: Any) -> None:
            previous(self, *args, **kwargs)
            try:
                self.title(APP_TITLE if cls is Session else normalize_release_text(self.title()))
            except Exception:
                pass
            _schedule_normalization(self)

        cls.__init__ = init
        setattr(cls, marker, True)

    wrap_init(Session, "_faradaiq_v291_version_init")
    wrap_init(Browser, "_faradaiq_v291_version_init")

    if messagebox is not None and not getattr(messagebox, "_faradaiq_v291_patch", False):
        for name in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel", "askretrycancel"):
            fn = getattr(messagebox, name, None)
            if not callable(fn):
                continue

            def make_wrapper(callable_fn: Any):
                def wrapper(title: Any = None, message: Any = None, *args: Any, **kwargs: Any):
                    return callable_fn(
                        normalize_release_text(title),
                        normalize_release_text(message),
                        *args,
                        **kwargs,
                    )
                return wrapper

            setattr(messagebox, name, make_wrapper(fn))
        messagebox._faradaiq_v291_patch = True



def _normalize_release_payload(value: Any) -> Any:
    """Recursively normalize release-facing strings in logs and public metadata."""
    if isinstance(value, str):
        return normalize_release_text(value)
    if isinstance(value, Mapping):
        return {key: _normalize_release_payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_normalize_release_payload(item) for item in value)
    if isinstance(value, list):
        return [_normalize_release_payload(item) for item in value]
    return value


def _install_tracking_version_normalization(core: MutableMapping[str, Any]) -> None:
    tracker = core.get("_v753_track_event")
    if not callable(tracker) or getattr(tracker, "_faradaiq_v291_patch", False):
        return

    def track_event(event: Any, payload: Any = None, *args: Any, **kwargs: Any):
        return tracker(
            normalize_release_text(event),
            _normalize_release_payload(payload),
            *args,
            **kwargs,
        )

    track_event._faradaiq_v291_patch = True
    core["_v753_track_event"] = track_event

def _install_drt_gates(core: MutableMapping[str, Any]) -> None:
    previous_builder = core.get("_v27_build_auto_pyseccm_commands_for_data_dir")
    if callable(previous_builder) and not getattr(previous_builder, "_faradaiq_v291_gate", False):
        def gated_builder(data_dir: Any, script_root: Any = None, python_exe: Any = None, include_drt: bool = True):
            commands, notes, detected = previous_builder(
                data_dir,
                script_root=script_root,
                python_exe=python_exe,
                include_drt=include_drt,
            )
            if not include_drt:
                return list(commands or []), list(notes or []), detected
            return _filter_drt_commands(commands, notes, detected)

        gated_builder._faradaiq_v291_gate = True
        core["_v27_build_auto_pyseccm_commands_for_data_dir"] = gated_builder

    # Patch the manifest router too, because it can be called directly by jobs.
    try:
        from faradaiq import legacy_pyseccm_v210 as legacy
        previous_manifest = legacy.commands_from_manifest
        if not getattr(previous_manifest, "_faradaiq_v291_gate", False):
            def gated_manifest(data_dir: Any, script_root: Any, python_exe: str, include_drt: bool = True):
                result = previous_manifest(data_dir, script_root, python_exe, include_drt=include_drt)
                if result is None or not include_drt:
                    return result
                return _filter_drt_commands(*result)
            gated_manifest._faradaiq_v291_gate = True
            legacy.commands_from_manifest = gated_manifest
            core["_v210_commands_from_manifest"] = gated_manifest
    except Exception:
        pass

    Runner = core.get("SECCMPlotRunnerGUI")
    if Runner is not None and hasattr(Runner, "run_drt") and not getattr(Runner, "_faradaiq_v291_drt_gate", False):
        previous_run_drt = Runner.run_drt
        messagebox = core.get("messagebox")

        def run_drt(self: Any):
            try:
                self.detect_files()
                eis = self.files.get("eis")
                drt_dir = self.files.get("drt_dir")
                if not eis or not valid_eis_file(eis) or not drt_dir or not valid_drt_input_dir(drt_dir):
                    message = (
                        "DRT was not started. Select a dataset with valid EIS frequency, Z-real, and Z-imaginary data. "
                        "CA, CP, CV, OCP, and other non-EIS data cannot be sent to the DRT parser."
                    )
                    try:
                        self.log(message)
                    except Exception:
                        pass
                    if messagebox is not None:
                        messagebox.showwarning("DRT requires EIS", message, parent=self)
                    return None
            except Exception as exc:
                try:
                    self.log(f"DRT eligibility check failed: {exc}")
                except Exception:
                    pass
                return None
            return previous_run_drt(self)

        Runner.run_drt = run_drt
        Runner._faradaiq_v291_drt_gate = True

    RunWindow = core.get("PySECCMRunWindow")
    if RunWindow is not None and hasattr(RunWindow, "build_commands") and not getattr(RunWindow, "_faradaiq_v291_drt_gate", False):
        previous_build = RunWindow.build_commands

        def build_commands(self: Any):
            if str(getattr(self, "tool", "")).lower() == "drt":
                try:
                    self.auto_detect()
                except Exception:
                    pass
                eis_files = valid_eis_files((getattr(self, "detected", {}) or {}).get("eis", []) or [])
                inp = Path(self.drt_input_dir_var.get().strip())
                if not eis_files:
                    raise ValueError(
                        "DRT requires verified EIS input. No valid frequency/Z-real/Z-imaginary spectrum was detected; non-EIS data are not eligible."
                    )
                if not valid_drt_input_dir(inp):
                    raise ValueError(
                        "The selected DRT input folder does not contain valid frequency/Z-real/Z-imaginary spectra."
                    )
            return previous_build(self)

        RunWindow.build_commands = build_commands
        RunWindow._faradaiq_v291_drt_gate = True



def _style_matplotlib_toolbar(toolbar: Any) -> None:
    """Replace low-contrast native icons with always-visible text controls.

    Matplotlib's Tk toolbar icons are dark glyphs.  Several FaradaIQ dark
    themes gave the toolbar a black background, making Home, Pan, Zoom, Save,
    and related controls effectively invisible.  This last-layer renderer
    keeps the toolbar below the plot but gives it a light, bordered surface
    and explicit text buttons that remain visible in every FaradaIQ theme.
    """
    try:
        import tkinter as tk
    except Exception:
        return

    bg = "#f8fafc"
    fg = "#0f172a"
    active_bg = "#dbeafe"
    border = "#94a3b8"

    try:
        toolbar.configure(
            background=bg,
            borderwidth=1,
            relief="solid",
            highlightbackground=border,
            highlightcolor=border,
            highlightthickness=1,
            padx=4,
            pady=3,
        )
    except Exception:
        pass

    # The native icon buttons are retained as callback providers but removed
    # from layout.  Their icons can be black-on-black under dark themes.
    for child in list(getattr(toolbar, "winfo_children", lambda: [])()):
        try:
            cls = str(child.winfo_class()).lower()
        except Exception:
            cls = ""
        if "button" in cls:
            try:
                child.pack_forget()
            except Exception:
                try:
                    child.grid_remove()
                except Exception:
                    pass
        else:
            try:
                child.configure(background=bg, foreground=fg)
            except Exception:
                try:
                    child.configure(background=bg)
                except Exception:
                    pass

    if getattr(toolbar, "_faradaiq_visible_controls", None) is not None:
        try:
            toolbar._faradaiq_visible_controls.configure(background=bg)
        except Exception:
            pass
        return

    controls = tk.Frame(toolbar, background=bg, borderwidth=0, highlightthickness=0)
    controls.pack(side="left", padx=(2, 8), pady=1)
    toolbar._faradaiq_visible_controls = controls

    specs = [
        ("Home", "home", "Reset the plot to its original view"),
        ("Back", "back", "Return to the previous view"),
        ("Forward", "forward", "Move to the next view"),
        ("Pan", "pan", "Pan the plot"),
        ("Box zoom", "zoom", "Drag a rectangle to zoom"),
        ("Fit view", "home", "Fit the full data view"),
        ("Save figure", "save_figure", "Save the current plot"),
    ]

    buttons = []
    for label, method_name, tooltip in specs:
        method = getattr(toolbar, method_name, None)
        if not callable(method):
            continue
        button = tk.Button(
            controls,
            text=label,
            command=method,
            background=bg,
            foreground=fg,
            activebackground=active_bg,
            activeforeground=fg,
            disabledforeground="#64748b",
            relief="raised",
            borderwidth=1,
            highlightbackground=border,
            highlightcolor="#2563eb",
            highlightthickness=1,
            padx=8,
            pady=3,
            takefocus=True,
            cursor="hand2",
        )
        button.pack(side="left", padx=2)
        try:
            button._faradaiq_tooltip_text = tooltip
        except Exception:
            pass
        buttons.append(button)
    toolbar._faradaiq_visible_buttons = buttons

    # Preserve the coordinate/status message on the right, with readable text.
    for child in list(getattr(toolbar, "winfo_children", lambda: [])()):
        if child is controls:
            continue
        try:
            child.configure(background=bg, foreground=fg)
        except Exception:
            try:
                child.configure(background=bg)
            except Exception:
                pass

    try:
        toolbar.configure(height=max(38, int(toolbar.winfo_reqheight())))
    except Exception:
        pass


def _install_matplotlib_toolbar_visibility(core: MutableMapping[str, Any]) -> None:
    """Patch every future NavigationToolbar2Tk created by FaradaIQ."""
    try:
        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
    except Exception:
        return

    if getattr(NavigationToolbar2Tk, "_faradaiq_v291_visible_patch", False):
        core["_v291_style_matplotlib_toolbar"] = _style_matplotlib_toolbar
        return

    original_init = NavigationToolbar2Tk.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _style_matplotlib_toolbar(self)
        try:
            self.after_idle(lambda w=self: _style_matplotlib_toolbar(w))
        except Exception:
            pass

    NavigationToolbar2Tk.__init__ = patched_init
    NavigationToolbar2Tk._faradaiq_v291_visible_patch = True
    NavigationToolbar2Tk._faradaiq_original_init = original_init
    core["_v291_style_matplotlib_toolbar"] = _style_matplotlib_toolbar

def reassert_active_module_versions() -> None:
    """Reassert the public v291 identity after any historical patch imports.

    Several legacy feature installers assign their implementation version to
    shared modules during import.  Calling this helper is idempotent and keeps
    every currently active subsystem aligned with the public release.
    """
    import importlib

    for module_name in (
        "faradaiq.v232_batch_jobs",
        "faradaiq.v233_batch_jobs",
        "faradaiq.v250_reliability",
        "faradaiq.v261_interactive_scilink",
        "faradaiq.v261_labric_mcp",
        "faradaiq.v262_technique_scilink",
        "faradaiq.ca_cp_analysis_v262",
        "faradaiq.ocp_handoff_v291",
        "faradaiq.tafel_trace_v291",
        "faradaiq.v290_powerpoint_summary",
        "faradaiq.v290_portable_browser",
        "faradaiq.v290_electrochemistry_routing",
        "faradaiq.v290_electrochemistry_skill",
        "faradaiq.v290_deterministic_electrochemistry",
        "faradaiq.v290_short_names",
    ):
        module = importlib.import_module(module_name)
        setattr(module, "VERSION", VERSION)
        if hasattr(module, "APP_VERSION"):
            setattr(module, "APP_VERSION", SEMVER)
        if hasattr(module, "APP_TITLE"):
            setattr(module, "APP_TITLE", APP_TITLE)
        if hasattr(module, "VERSION_NUMBER"):
            setattr(module, "VERSION_NUMBER", VERSION_NUMBER)
        if hasattr(module, "SEMVER"):
            setattr(module, "SEMVER", SEMVER)
        if hasattr(module, "FRONT_SLIDE_FOOTER"):
            setattr(module, "FRONT_SLIDE_FOOTER", f"Made by FaradaIQ Studio {VERSION}")


def install(core: MutableMapping[str, Any]) -> None:
    """Install v291 last-layer release corrections into the legacy core."""
    core["FARADAIQ_PUBLIC_VERSION"] = VERSION
    core["FARADAIQ_APP_VERSION"] = VERSION
    core["APP_TITLE"] = APP_TITLE
    core["FARADAIQ_RELEASE_VERSION"] = VERSION
    core["FARADAIQ_RELEASE_TITLE"] = APP_TITLE
    core["FARADAIQ_INSTALL_TARGET"] = INSTALL_TARGET
    core["_v291_normalize_release_text"] = normalize_release_text
    core["_v291_valid_eis_file"] = valid_eis_file
    core["_v291_valid_drt_input_dir"] = valid_drt_input_dir
    core["_v291_filter_drt_commands"] = _filter_drt_commands

    _install_runtime_version_normalization(core)
    _install_tracking_version_normalization(core)
    _install_drt_gates(core)
    _install_help(core)
    _install_about(core)
    _install_matplotlib_toolbar_visibility(core)

    # Reassert the public release identity after all historical feature installers
    # have run. Internal filenames retain their implementation version.
    try:
        reassert_active_module_versions()
    except Exception:
        # Runtime title normalization still prevents stale public GUI strings.
        pass
