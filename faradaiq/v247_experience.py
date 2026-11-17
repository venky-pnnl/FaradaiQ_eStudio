"""FaradaIQ Studio v273 interaction, performance, and handoff refinements.

Key changes:
- one continuous CV forward->reverse slider with Play/Pause
- compact Plotly animation frames and lower-cost contour grids
- exact prompt preservation in SciLink review
- file selection filters and explicit Select/Clear controls
- completed-job load restores Main Studio first, then the prebuilt Browser
- debounced cache-first Browser navigation with adjacent-record prefetch
- Ollama gpt-oss:120b-cloud default/migration from the former gemma3:1b default
"""
from __future__ import annotations

import json
import math
import os
import queue
import threading
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

VERSION = "v273"
DEFAULT_OLLAMA_MODEL = "gpt-oss:120b-cloud"
_OLD_OLLAMA_DEFAULT = "gemma3:1b"
_CORE: MutableMapping[str, Any] = {}
_PREVIOUS_REVIEW: Any = None


def _g(name: str, default: Any = None) -> Any:
    return _CORE.get(name, default)


def _gui_from(source: Any) -> Any:
    return getattr(source, "gui", source)


def _browser_from(source: Any) -> Any:
    if source is not None and hasattr(source, "tree"):
        return source
    return None


def _selected_record_names(source: Any) -> list[str]:
    browser = _browser_from(source)
    names: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            names.append(text)

    if browser is None:
        return names
    try:
        for iid in list(browser.tree.selection() or []):
            item = browser.tree.item(iid)
            add(item.get("text"))
            values = item.get("values") or []
            if values:
                add(values[0])
    except Exception:
        pass
    try:
        lb = getattr(browser, "plot_view_list", None)
        if lb is not None:
            for index in list(lb.curselection() or []):
                add(lb.get(index))
    except Exception:
        pass
    return names


def _candidate_extension(item: Mapping[str, Any]) -> str:
    raw = str(item.get("path") or item.get("display_path") or "")
    suffix = Path(raw).suffix.lower()
    return suffix or "(generated)"


def _candidate_group(item: Mapping[str, Any]) -> str:
    return str(item.get("source_group") or item.get("group") or "Other").strip() or "Other"


def _candidate_role(item: Mapping[str, Any]) -> str:
    return str(item.get("role") or "Data file").strip() or "Data file"


def _enhanced_scilink_review(source: Any, service: str, prompt_override: str | None = None) -> Any:
    """Open SciLink review with exact prompt preservation and file filters.

    The field remains blank when no caller-supplied prompt exists. When a user
    typed text in Smart Suggestions or AI Chat, that exact text is preserved.
    """
    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v245_queue_scilink as minimal

    if service != "scilink":
        if callable(_PREVIOUS_REVIEW):
            return _PREVIOUS_REVIEW(source, service, prompt_override=prompt_override or "")
        raise RuntimeError("The Edison review workflow is unavailable.")

    tk, ttk = _g("tk"), _g("ttk")
    ScrolledText = _g("ScrolledText")
    messagebox = _g("messagebox")
    gui = _gui_from(source)
    cfg = old.load_sharing_settings()
    candidates = old.collect_share_candidates(source, cfg)
    initial_prompt = "" if prompt_override is None else str(prompt_override)

    win = tk.Toplevel(gui)
    win.title(f"FaradaIQ Studio {VERSION} - Review SciLink")
    win.geometry("1220x850")
    outer = ttk.Frame(win, style="Card.TFrame", padding=(12, 12))
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    ttk.Label(outer, text="Review SciLink request", style="Section.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text=(
            "SciLink receives only the exact text in Prompt and the Request JSON. "
            "All files begin unchecked. Use filters and explicit selection controls to opt in."
        ),
        style="Muted.TLabel",
        wraplength=1160,
    ).pack(anchor="w", pady=(2, 8))

    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)
    prompt_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    files_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    request_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    settings_tab = ttk.Frame(tabs, style="Card.TFrame", padding=8)
    tabs.add(prompt_tab, text="Prompt")
    tabs.add(files_tab, text="Files to Share")
    tabs.add(request_tab, text="Request JSON")
    tabs.add(settings_tab, text="Service Details")

    prompt_box = ScrolledText(prompt_tab, wrap="word")
    prompt_box.pack(fill="both", expand=True)
    if initial_prompt:
        prompt_box.insert("1.0", initial_prompt)

    # File selection toolbar -------------------------------------------------
    search_var = tk.StringVar(value="")
    role_var = tk.StringVar(value="All roles")
    group_var = tk.StringVar(value="All groups")
    type_var = tk.StringVar(value="All types")
    selected_only_var = tk.BooleanVar(value=False)
    summary_var = tk.StringVar(value="Selected 0 files. All files start unchecked.")

    toolbar = ttk.Frame(files_tab, style="Card.TFrame")
    toolbar.pack(fill="x", pady=(0, 6))
    ttk.Label(toolbar, text="Filter", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 4))
    search_entry = ttk.Entry(toolbar, textvariable=search_var, width=32)
    search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 6))
    roles = sorted({_candidate_role(item) for item in candidates})
    groups = sorted({_candidate_group(item) for item in candidates})
    types = sorted({_candidate_extension(item) for item in candidates})
    ttk.Combobox(toolbar, textvariable=role_var, values=["All roles", *roles], state="readonly", width=19).grid(row=0, column=2, padx=3)
    ttk.Combobox(toolbar, textvariable=group_var, values=["All groups", *groups], state="readonly", width=19).grid(row=0, column=3, padx=3)
    ttk.Combobox(toolbar, textvariable=type_var, values=["All types", *types], state="readonly", width=13).grid(row=0, column=4, padx=3)
    ttk.Checkbutton(toolbar, text="Selected only", variable=selected_only_var).grid(row=0, column=5, padx=(6, 3))
    toolbar.columnconfigure(1, weight=1)

    actions = ttk.Frame(files_tab, style="Card.TFrame")
    actions.pack(fill="x", pady=(0, 5))
    ttk.Label(actions, textvariable=summary_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    canvas = tk.Canvas(files_tab, highlightthickness=0, borderwidth=0)
    ybar = ttk.Scrollbar(files_tab, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=ybar.set)
    canvas.pack(side="left", fill="both", expand=True)
    ybar.pack(side="right", fill="y")
    inner = ttk.Frame(canvas, style="Card.TFrame")
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))

    file_rows: list[dict[str, Any]] = []
    for row_index, item in enumerate(candidates):
        var = tk.BooleanVar(value=False)
        row = ttk.Frame(inner, style="Card.TFrame", padding=(2, 2))
        row.grid(row=row_index, column=0, sticky="ew")
        row.columnconfigure(2, weight=1)
        display_path = item.get("display_path") or item.get("path") or "(generated only if checked)"
        ttk.Checkbutton(row, variable=var).grid(row=0, column=0, sticky="nw", padx=(0, 4))
        ttk.Label(row, text=_candidate_role(item), style="Card.TLabel", width=21).grid(row=0, column=1, sticky="nw", padx=(0, 8))
        ttk.Label(row, text=str(display_path), style="Muted.TLabel", wraplength=700).grid(row=0, column=2, sticky="nw")
        ttk.Label(row, text=old._format_size(item.get("size_bytes")), style="Muted.TLabel", width=10).grid(row=0, column=3, sticky="ne", padx=(8, 0))
        file_rows.append({"item": item, "var": var, "row": row, "visible": True})
    inner.columnconfigure(0, weight=1)

    request_box = ScrolledText(request_tab, wrap="word")
    request_box.pack(fill="both", expand=True)
    request_box.configure(state="disabled")

    mode_var = tk.StringVar(value="Autonomous")
    mode_row = ttk.Frame(settings_tab, style="Card.TFrame")
    mode_row.pack(fill="x", pady=(0, 8))
    ttk.Label(mode_row, text="SciLink mode", style="Card.TLabel").pack(side="left")
    ttk.Combobox(mode_row, textvariable=mode_var, values=("Autonomous", "Co-pilot"), state="readonly", width=18).pack(side="left", padx=(8, 0))
    ttk.Label(mode_row, text="Autonomous is the default.", style="Muted.TLabel").pack(side="left", padx=(8, 0))
    ttk.Label(settings_tab, text="Requested filenames (optional, one per line)", style="Card.TLabel").pack(anchor="w")
    filenames_box = ScrolledText(settings_tab, wrap="none", height=6)
    filenames_box.pack(fill="x", pady=(2, 8))
    detail_box = ScrolledText(settings_tab, wrap="word", height=18)
    detail_box.pack(fill="both", expand=True)
    details = cleanup._service_details_text("scilink", cfg)
    detail_box.insert("1.0", details)
    detail_box.configure(state="disabled")

    def requested_names() -> list[str]:
        return [line.strip() for line in filenames_box.get("1.0", "end-1c").splitlines() if line.strip()]

    def visible_rows() -> list[dict[str, Any]]:
        return [entry for entry in file_rows if entry.get("visible")]

    def selected_items() -> list[dict[str, Any]]:
        return [dict(entry["item"]) for entry in file_rows if entry["var"].get()]

    def preview_request() -> dict[str, Any]:
        data: list[str] = []
        support: list[str] = []
        metadata: list[str] = []
        for item in selected_items():
            role = _candidate_role(item).lower()
            group = _candidate_group(item).lower()
            generated = str(item.get("generated") or "").lower()
            path = str(item.get("path") or "").strip()
            if generated == "session_metadata":
                metadata.append("faradaiq_session_metadata.json (generated in new job folder)")
                continue
            if not path:
                continue
            suffix = Path(path).suffix.lower()
            if "metadata" in role or "manifest" in role or "metadata" in group or suffix in {".json", ".yaml", ".yml", ".xml"}:
                metadata.append(path)
            elif any(t in role or t in group for t in ("literature", "figure", "visualization", "additional", "support", "reference")):
                support.append(path)
            else:
                data.append(path)
        return {
            "selected_data_paths": data,
            "supporting_file_paths": support,
            "metadata_paths": metadata,
            "output_folder": "(new SciLink job folder created after Start)",
            "selected_record_names": _selected_record_names(source),
            "requested_filenames": requested_names(),
        }

    def refresh_summary(*_args: Any) -> None:
        selected = [entry for entry in file_rows if entry["var"].get()]
        visible = visible_rows()
        total = sum(int(entry["item"].get("size_bytes") or 0) for entry in selected)
        summary_var.set(
            f"Selected {len(selected)} of {len(file_rows)} item(s), {old._format_size(total)}. "
            f"Visible after filter: {len(visible)}."
        )
        request_box.configure(state="normal")
        request_box.delete("1.0", "end")
        request_box.insert("1.0", json.dumps(preview_request(), ensure_ascii=False, indent=2))
        request_box.configure(state="disabled")

    def apply_filter(*_args: Any) -> None:
        query_text = search_var.get().strip().lower()
        role_filter = role_var.get()
        group_filter = group_var.get()
        type_filter = type_var.get()
        selected_only = bool(selected_only_var.get())
        for entry in file_rows:
            item = entry["item"]
            haystack = " ".join(
                str(item.get(key) or "")
                for key in ("role", "source_group", "path", "display_path", "name", "label", "generated")
            ).lower()
            match = (
                (not query_text or query_text in haystack)
                and (role_filter == "All roles" or _candidate_role(item) == role_filter)
                and (group_filter == "All groups" or _candidate_group(item) == group_filter)
                and (type_filter == "All types" or _candidate_extension(item) == type_filter)
                and (not selected_only or bool(entry["var"].get()))
            )
            entry["visible"] = bool(match)
            if match:
                entry["row"].grid()
            else:
                entry["row"].grid_remove()
        refresh_summary()

    def set_rows(rows: Sequence[dict[str, Any]], value: bool) -> None:
        for entry in rows:
            entry["var"].set(value)
        apply_filter()

    ttk.Button(actions, text="Select visible", command=lambda: set_rows(visible_rows(), True)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Clear visible", command=lambda: set_rows(visible_rows(), False)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Select all", command=lambda: set_rows(file_rows, True)).pack(side="right", padx=(4, 0))
    ttk.Button(actions, text="Clear all", command=lambda: set_rows(file_rows, False)).pack(side="right", padx=(4, 0))

    for var in (search_var, role_var, group_var, type_var, selected_only_var):
        try:
            var.trace_add("write", apply_filter)
        except Exception:
            pass
    for entry in file_rows:
        try:
            entry["var"].trace_add("write", refresh_summary)
        except Exception:
            pass
    filenames_box.bind("<KeyRelease>", refresh_summary)
    apply_filter()

    footer = ttk.Frame(outer, style="Card.TFrame")
    footer.pack(fill="x", pady=(8, 0))
    status_var = tk.StringVar(
        value="Exact caller-entered prompt preserved when available. Files are unchecked. Mode is Autonomous."
    )
    ttk.Label(footer, textvariable=status_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

    def start() -> None:
        exact_prompt = prompt_box.get("1.0", "end-1c")
        if not exact_prompt.strip():
            messagebox.showwarning("Prompt required", "Enter the SciLink prompt before starting.", parent=win)
            return
        status_var.set("Creating exact-prompt SciLink job...")
        try:
            job = minimal._write_minimal_scilink_job(
                source,
                exact_prompt,
                selected_items(),
                cfg,
                mode=mode_var.get(),
                requested_filenames=requested_names(),
            )
            win.destroy()
            old._launch_scilink(source, job, cfg)
        except Exception as exc:
            messagebox.showerror("Could not start SciLink", f"{exc}\n\n{traceback.format_exc()}", parent=win)

    ttk.Button(footer, text="Start new SciLink job", style="Accent.TButton", command=start).pack(side="right", padx=(6, 0))
    ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right")
    return win


# Contour/animation performance ---------------------------------------------
def _fast_grid_values(x: Any, y: Any, values: Any, interpolation: str):
    """Contour-ready grid capped for fast interactive animation."""
    import numpy as np
    import pandas as pd

    df = pd.DataFrame({"x": x, "y": y, "v": values}).replace([np.inf, -np.inf], np.nan).dropna()
    if df.empty:
        raise ValueError("No finite spatial values are available.")
    df = df.groupby(["x", "y"], as_index=False).v.mean()
    ux = np.sort(df.x.unique())
    uy = np.sort(df.y.unique())
    regular = len(ux) >= 2 and len(uy) >= 2 and len(df) >= max(4, int(0.72 * len(ux) * len(uy)))
    method = str(interpolation or "Linear").lower()
    if regular:
        pivot = df.pivot_table(index="y", columns="x", values="v", aggfunc="mean").reindex(index=uy, columns=ux)
        z = pivot.to_numpy(float)
        if np.isnan(z).any() and method != "none":
            try:
                from scipy.interpolate import griddata
                xx, yy = np.meshgrid(ux, uy)
                fill = griddata(df[["x", "y"]].to_numpy(float), df.v.to_numpy(float), (xx, yy), method="nearest" if method == "nearest" else "linear", rescale=True)
                z = np.where(np.isfinite(z), z, fill)
            except Exception:
                pass
        return ux, uy, z, True
    if method == "none":
        return ux, uy, np.empty((0, 0)), False

    n_points = max(1, len(df))
    side = max(36, min(58, int(round(math.sqrt(n_points) * 10))))
    gx = np.linspace(float(df.x.min()), float(df.x.max()), side)
    gy = np.linspace(float(df.y.min()), float(df.y.max()), side)
    xx, yy = np.meshgrid(gx, gy)
    pts = df[["x", "y"]].to_numpy(float)
    vals = df.v.to_numpy(float)
    lo = np.nanmin(pts, axis=0)
    span = np.nanmax(pts, axis=0) - lo
    span = np.where(np.isfinite(span) & (span > 0), span, 1.0)
    pts_n = (pts - lo) / span
    q = np.column_stack([((xx.ravel() - lo[0]) / span[0]), ((yy.ravel() - lo[1]) / span[1])])
    qx = q[:, 0].reshape(xx.shape)
    qy = q[:, 1].reshape(yy.shape)
    z = None
    requested = method if method in {"nearest", "linear", "cubic"} else "linear"
    try:
        from scipy.interpolate import griddata
        tried: list[str] = []
        for candidate in (requested, "linear", "nearest"):
            if candidate in tried or (candidate == "cubic" and n_points < 6):
                continue
            tried.append(candidate)
            try:
                trial = griddata(pts_n, vals, (qx, qy), method=candidate)
            except Exception:
                continue
            if trial is not None and np.isfinite(trial).any():
                z = np.asarray(trial, dtype=float)
                if candidate != "nearest" and np.isnan(z).any():
                    nearest = griddata(pts_n, vals, (qx, qy), method="nearest")
                    z = np.where(np.isfinite(z), z, nearest)
                break
    except Exception:
        z = None
    if z is None or not np.isfinite(z).any():
        eps = 1e-14
        out = np.empty(len(q), dtype=float)
        for i in range(0, len(q), 8000):
            block = q[i:i + 8000]
            d2 = np.sum((block[:, None, :] - pts_n[None, :, :]) ** 2, axis=2)
            nearest_idx = np.argmin(d2, axis=1)
            exact = np.min(d2, axis=1) <= eps
            weights = 1.0 / np.maximum(d2, eps)
            estimate = (weights @ vals) / np.maximum(np.sum(weights, axis=1), eps)
            estimate[exact] = vals[nearest_idx[exact]]
            out[i:i + len(block)] = estimate
        z = out.reshape(xx.shape)
    return gx, gy, np.asarray(z, dtype=float), False


def _compact_frame_trace(trace: Any) -> Any:
    import plotly.graph_objects as go
    kind = getattr(trace, "type", "")
    if kind == "contour":
        return go.Contour(x=trace.x, y=trace.y, z=trace.z, zmin=getattr(trace, "zmin", None), zmax=getattr(trace, "zmax", None))
    if kind == "heatmap":
        return go.Heatmap(x=trace.x, y=trace.y, z=trace.z, zmin=getattr(trace, "zmin", None), zmax=getattr(trace, "zmax", None))
    if kind == "scatter":
        marker_color = None
        try:
            marker_color = trace.marker.color
        except Exception:
            pass
        marker = {"color": marker_color} if marker_color is not None else None
        return go.Scatter(x=trace.x, y=trace.y, marker=marker)
    return trace


def _animation_buttons(frame_duration: int = 280) -> list[dict[str, Any]]:
    return [
        dict(
            type="buttons",
            direction="right",
            x=0.0,
            y=1.14,
            showactive=False,
            buttons=[
                dict(
                    label="▶ Play",
                    method="animate",
                    args=[None, {"fromcurrent": True, "mode": "immediate", "frame": {"duration": frame_duration, "redraw": True}, "transition": {"duration": 0}}],
                ),
                dict(
                    label="❚❚ Pause",
                    method="animate",
                    args=[[None], {"mode": "immediate", "frame": {"duration": 0, "redraw": False}, "transition": {"duration": 0}}],
                ),
            ],
        )
    ]


def _continuous_cv_cycle_view(browser: Any, curves: Sequence[Any], group_label: str, suffix: str, options: Mapping[str, Any]) -> dict[str, Any]:
    import numpy as np
    import plotly.graph_objects as go
    from faradaiq import multidim_v216 as md

    cycle = int(curves[0].cycle or 0)
    branch_outputs: dict[str, Any] = {}
    for branch in ("forward", "reverse"):
        try:
            branch_outputs[branch] = md._v216_branch_cube(curves, branch, max_bands=64)
        except Exception:
            pass
    if not branch_outputs:
        raise ValueError("No forward or reverse CV scan branches could be reconstructed.")

    all_signals = np.concatenate([item[1].ravel() for item in branch_outputs.values()])
    scaled_all, signal_unit, _ = md._v216_scaled(all_signals, curves[0].y_name, str(options.get("signal_unit", "Auto")))
    signal_factor = 1.0
    finite_base = all_signals[np.isfinite(all_signals)]
    finite_scaled = scaled_all[np.isfinite(scaled_all)]
    if len(finite_base) and len(finite_scaled):
        idx = np.flatnonzero(np.abs(finite_base) > 0)
        if len(idx):
            signal_factor = float(finite_scaled[idx[0]] / finite_base[idx[0]])
    cmin, cmax = (None, None) if str(options.get("range_scope") or "Lock across cube") == "Per frame" else md._v222_color_range(scaled_all, options)
    spatial_unit = str(options.get("spatial_unit", "µm"))
    first_curves = next(iter(branch_outputs.values()))[2]
    x_m = np.array([np.nan if c.x_coord is None else c.x_coord for c in first_curves], dtype=float)
    y_m = np.array([np.nan if c.y_coord is None else c.y_coord for c in first_curves], dtype=float)
    coords_ok = np.sum(np.isfinite(x_m) & np.isfinite(y_m)) >= max(4, int(0.7 * len(first_curves)))
    x_disp, spatial_label, spatial_fmt = md._v216_spatial_scaled(x_m, spatial_unit)
    y_disp, _, _ = md._v216_spatial_scaled(y_m, spatial_unit)
    spectral_values = np.concatenate([item[0] for item in branch_outputs.values()])
    _, spectral_label, _ = md._v216_scaled(spectral_values, curves[0].x_name, str(options.get("spectral_unit", "Auto")))

    frames: list[Any] = []
    steps: list[dict[str, Any]] = []
    first_data: Optional[list[Any]] = None
    effective_modes: list[str] = []
    frame_index = 0
    total_frames = sum(len(item[0]) for item in branch_outputs.values())
    label_slots = {int(round(k * max(0, total_frames - 1) / 10.0)) for k in range(11)}

    for branch in ("forward", "reverse"):
        if branch not in branch_outputs:
            continue
        grid, matrix, branch_curves, branch_label = branch_outputs[branch]
        grid_disp, branch_axis_unit, _ = md._v216_scaled(grid, curves[0].x_name, str(options.get("spectral_unit", "Auto")))
        if branch_axis_unit:
            spectral_label = branch_axis_unit
        bx_m = np.array([np.nan if c.x_coord is None else c.x_coord for c in branch_curves], dtype=float)
        by_m = np.array([np.nan if c.y_coord is None else c.y_coord for c in branch_curves], dtype=float)
        bx, spatial_label, spatial_fmt = md._v216_spatial_scaled(bx_m, spatial_unit)
        by, _, _ = md._v216_spatial_scaled(by_m, spatial_unit)
        branch_code = "FWD" if branch == "forward" else "REV"
        for j, potential in enumerate(grid_disp):
            values = matrix[:, j] * signal_factor
            frame_name = f"c{cycle:02d}_{branch}_{j:03d}"
            title = (
                f"FaradaIQ {VERSION}: {group_label} | Cycle {cycle or 1:02d} | "
                f"{branch_label} | {md._v216_pretty_quantity(curves[0].x_name)} = {potential:.6g} {spectral_label}"
            )
            if coords_ok:
                traces, effective = md._v216_spatial_traces(
                    bx, by, values, options,
                    f"{md._v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}",
                    cmin, cmax,
                )
                effective_modes.append(effective)
            else:
                traces = [go.Scatter(x=[c.point for c in branch_curves], y=values, mode="lines+markers", name="Point-dependent slice")]
                effective_modes.append("Point-dependent line")
            if first_data is None:
                first_data = traces
            compact = [_compact_frame_trace(trace) for trace in traces]
            frames.append(go.Frame(data=compact, traces=list(range(len(compact))), name=frame_name, layout=go.Layout(title=title)))
            tick = f"{branch_code} {potential:.4g}" if frame_index in label_slots or j == 0 or j == len(grid_disp) - 1 else ""
            steps.append(dict(
                method="animate",
                label=tick,
                args=[[frame_name], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}],
            ))
            frame_index += 1

    if first_data is None:
        raise ValueError("No CV hyperspectral frames were generated.")
    fig = go.Figure(data=first_data, frames=frames)
    layout_images = md._v216_background_layout(options, x_disp, y_disp) if coords_ok else []
    fig.update_layout(
        title=frames[0].layout.title.text if frames else f"FaradaIQ {VERSION}: {group_label}",
        xaxis_title=f"X position / {spatial_label}" if coords_ok else "Point / acquisition position",
        yaxis_title=f"Y position / {spatial_label}" if coords_ok else f"{md._v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}",
        images=layout_images,
        sliders=[dict(
            active=0,
            x=0.05,
            len=0.90,
            y=-0.10,
            currentvalue={"prefix": "Scan: ", "visible": True},
            pad={"t": 30},
            steps=steps,
        )],
        updatemenus=_animation_buttons(260),
        meta={"branch_codes": ["FWD", "REV"], "slider_layout": "continuous_forward_then_reverse", "animation": "play_pause"},
        annotations=[dict(
            text="One continuous slider follows the acquisition path from forward scan into reverse/return scan.",
            xref="paper", yref="paper", x=0.5, y=1.07, showarrow=False,
        )],
        margin=dict(l=70, r=50, b=120, t=120),
    )
    if coords_ok:
        if bool(options.get("lock_xy_aspect", False)):
            fig.update_xaxes(tickformat=spatial_fmt, scaleanchor="y", scaleratio=1)
        else:
            fig.update_xaxes(tickformat=spatial_fmt)
        fig.update_yaxes(tickformat=spatial_fmt)
    out_dir = md._view_dir(browser)
    stamp = md.time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{md._slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_{md._slug(suffix)}_cycle{cycle or 1:02d}_{stamp}.html"
    meta = {
        "schema": "faradaiq-hyperspectral-cv-branch-cube-v247",
        "version": VERSION,
        "group_label": group_label,
        "cycle": cycle or 1,
        "branches": {k: {"label": v[3], "n_bands": len(v[0]), "potential_min": float(np.nanmin(v[0])), "potential_max": float(np.nanmax(v[0]))} for k, v in branch_outputs.items()},
        "total_slider_frames": len(frames),
        "slider_layout": "continuous_forward_then_reverse",
        "play_pause": True,
        "coordinates_available": bool(coords_ok),
        "spatial_unit": spatial_label,
        "signal_unit": signal_unit,
        "spectral_unit": spectral_label,
        "effective_render_modes": sorted(set(effective_modes)),
        "background_loaded": bool(layout_images),
        "options": dict(options),
        "source_files": [c.path.name for c in curves],
    }
    md._v216_write_figure(fig, out, meta)
    npz = out.with_suffix(".cube.npz")
    payload: dict[str, Any] = {
        "points": np.array([c.point for c in first_curves]),
        "x_coordinates_m": x_m,
        "y_coordinates_m": y_m,
        "cycle": np.array([cycle or 1]),
    }
    for branch, (grid, matrix, _curves, _label) in branch_outputs.items():
        payload[f"{branch}_potential_V"] = grid
        payload[f"{branch}_values_SI"] = matrix
    np.savez_compressed(npz, **payload)
    return {"html": str(out), "cube": str(npz), "metadata": str(out.with_suffix('.visualization.json')), "engine": "Plotly CV continuous branch hyperspectral", "label": f"Cycle {cycle or 1:02d}", **meta}


def _generic_with_play(browser: Any, curves: Sequence[Any], group_label: str, suffix: str, options: Mapping[str, Any]) -> dict[str, Any]:
    """Generic hyperspectral viewer with compact frames and Play/Pause."""
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    from faradaiq import multidim_v216 as md

    grid, matrix, curves = md._common_grid(curves, max_bands=70)
    x_m = np.array([np.nan if c.x_coord is None else c.x_coord for c in curves], dtype=float)
    y_m = np.array([np.nan if c.y_coord is None else c.y_coord for c in curves], dtype=float)
    coords_ok = np.sum(np.isfinite(x_m) & np.isfinite(y_m)) >= max(4, int(0.7 * len(curves)))
    x_disp, spatial_label, spatial_fmt = md._v216_spatial_scaled(x_m, str(options.get("spatial_unit", "µm")))
    y_disp, _, _ = md._v216_spatial_scaled(y_m, str(options.get("spatial_unit", "µm")))
    grid_disp, spectral_unit, _ = md._v216_scaled(grid, curves[0].x_name, str(options.get("spectral_unit", "Auto")))
    matrix_disp, signal_unit, _ = md._v216_scaled(matrix, curves[0].y_name, str(options.get("signal_unit", "Auto")))
    cmin, cmax = (None, None) if str(options.get("range_scope") or "Lock across cube") == "Per frame" else md._v222_color_range(matrix_disp, options)
    frames: list[Any] = []
    steps: list[dict[str, Any]] = []
    effective_modes: list[str] = []
    label_slots = {int(round(k * max(0, len(grid_disp) - 1) / 10.0)) for k in range(11)}
    if coords_ok:
        first_data: Optional[list[Any]] = None
        for j, axis_value in enumerate(grid_disp):
            traces, effective = md._v216_spatial_traces(
                x_disp, y_disp, matrix_disp[:, j], options,
                f"{md._v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}", cmin, cmax,
            )
            effective_modes.append(effective)
            name = f"band_{j:03d}"
            title = f"FaradaIQ {VERSION}: {group_label} | {md._v216_pretty_quantity(curves[0].x_name)} = {axis_value:.6g} {spectral_unit}"
            if first_data is None:
                first_data = traces
            compact = [_compact_frame_trace(trace) for trace in traces]
            frames.append(go.Frame(data=compact, traces=list(range(len(compact))), name=name, layout=go.Layout(title=title)))
            label = f"{axis_value:.4g} {spectral_unit}" if j in label_slots or j in {0, len(grid_disp)-1} else ""
            steps.append(dict(method="animate", label=label, args=[[name], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}]))
        fig = go.Figure(data=first_data or [], frames=frames)
        images = md._v216_background_layout(options, x_disp, y_disp)
        fig.update_layout(
            title=frames[0].layout.title.text if frames else group_label,
            xaxis_title=f"X position / {spatial_label}",
            yaxis_title=f"Y position / {spatial_label}",
            images=images,
            sliders=[dict(active=0, currentvalue={"prefix": f"{md._v216_pretty_quantity(curves[0].x_name)}: "}, pad={"t": 42}, steps=steps)],
            updatemenus=_animation_buttons(300),
            margin=dict(l=70, r=50, b=95, t=115),
        )
        if bool(options.get("lock_xy_aspect", False)):
            fig.update_xaxes(tickformat=spatial_fmt, scaleanchor="y", scaleratio=1)
        else:
            fig.update_xaxes(tickformat=spatial_fmt)
        fig.update_yaxes(tickformat=spatial_fmt)
    else:
        points = np.array([c.point for c in curves], dtype=float)
        fig = make_subplots(rows=1, cols=2, subplot_titles=("Point × spectral-axis cube", "Selected spectral slice"), column_widths=[0.68, 0.32])
        fig.add_trace(go.Heatmap(z=matrix_disp, x=grid_disp, y=points, colorbar=dict(title=f"{md._v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}")), row=1, col=1)
        fig.add_trace(go.Scatter(x=points, y=matrix_disp[:, 0], mode="lines+markers", name="slice"), row=1, col=2)
        for j in range(len(grid_disp)):
            name = f"band_{j:03d}"
            frames.append(go.Frame(data=[go.Scatter(y=matrix_disp[:, j])], traces=[1], name=name, layout=go.Layout(title=f"FaradaIQ {VERSION}: {group_label} | {md._v216_pretty_quantity(curves[0].x_name)} = {grid_disp[j]:.6g} {spectral_unit}")))
            label = f"{grid_disp[j]:.4g} {spectral_unit}" if j in label_slots or j in {0, len(grid_disp)-1} else ""
            steps.append(dict(method="animate", label=label, args=[[name], {"mode": "immediate", "frame": {"duration": 0, "redraw": False}, "transition": {"duration": 0}}]))
        fig.frames = frames
        fig.update_xaxes(title_text=f"{md._v216_pretty_quantity(curves[0].x_name)} / {spectral_unit}", row=1, col=1)
        fig.update_yaxes(title_text="Point", row=1, col=1)
        fig.update_xaxes(title_text="Point", row=1, col=2)
        fig.update_yaxes(title_text=f"{md._v216_pretty_quantity(curves[0].y_name)} / {signal_unit or 'SI'}", row=1, col=2)
        fig.update_layout(title=f"FaradaIQ {VERSION}: {group_label}", sliders=[dict(active=0, currentvalue={"prefix": "Slice: "}, pad={"t": 42}, steps=steps)], updatemenus=_animation_buttons(260))
        images = []

    out_dir = md._view_dir(browser)
    stamp = md.time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{md._slug(getattr(browser, 'base_name', 'data'))}_hyperspectral_{md._slug(suffix)}_{stamp}.html"
    meta = {
        "schema": "faradaiq-hyperspectral-cube-v247",
        "version": VERSION,
        "group_label": group_label,
        "technique": str(curves[0].record.get("technique_key") or "OTHER"),
        "spectral_axis_name": curves[0].x_name,
        "signal_name": curves[0].y_name,
        "n_points": len(curves),
        "n_unique_points": len(set(c.point for c in curves)),
        "n_bands": len(grid),
        "coordinates_available": bool(coords_ok),
        "regular_xy_grid": bool(coords_ok and md._regular_grid(pd.DataFrame({"x_coord": x_m, "y_coord": y_m}))),
        "spatial_unit": spatial_label,
        "signal_unit": signal_unit,
        "spectral_unit": spectral_unit,
        "effective_render_modes": sorted(set(effective_modes)),
        "background_loaded": bool(images),
        "play_pause": True,
        "compact_animation_frames": True,
        "options": dict(options),
        "source_files": [c.path.name for c in curves],
    }
    md._v216_write_figure(fig, out, meta)
    npz = out.with_suffix(".cube.npz")
    np.savez_compressed(npz, spectral_axis=grid, values=matrix, spectral_axis_SI=grid, values_SI=matrix, points=np.array([c.point for c in curves]), x_coordinates=np.array(x_m), y_coordinates=np.array(y_m), x_coordinates_m=x_m, y_coordinates_m=y_m, methods=np.array([c.method for c in curves]), cycles=np.array([c.cycle for c in curves]))
    return {"html": str(out), "engine": "Plotly fast hyperspectral", "cube": str(npz), "metadata": str(out.with_suffix('.visualization.json')), "label": group_label, **meta}


# Browser navigation --------------------------------------------------------
def _install_fast_browser_selection() -> None:
    from faradaiq import browser_ui_v209 as bu
    if getattr(bu, "_v247_fast_selection_installed", False):
        return

    def read_cached(browser: Any, path: Path, max_rows: int = 1200) -> Any:
        try:
            key = (str(path.resolve()), int(path.stat().st_mtime_ns), max_rows)
        except Exception:
            key = (str(path), 0, max_rows)
        cache: OrderedDict[Any, Any] = getattr(browser, "_v247_preview_cache", OrderedDict())
        if key in cache:
            value = cache.pop(key)
            cache[key] = value
            browser._v247_preview_cache = cache
            return value
        reader = browser._v209_core.get("_v138_csv_read")
        value = reader(path, max_rows=max_rows) if callable(reader) else None
        cache[key] = value
        while len(cache) > 72:
            cache.popitem(last=False)
        browser._v247_preview_cache = cache
        return value

    def prefetch(browser: Any, selected_iid: str) -> None:
        try:
            parent = browser.tree.parent(selected_iid)
            siblings = list(browser.tree.get_children(parent))
            index = siblings.index(selected_iid)
            targets = [siblings[i] for i in (index - 1, index + 1) if 0 <= i < len(siblings)]
            paths: list[Path] = []
            for iid in targets:
                rows = bu._tree_records(browser, iid)
                if len(rows) == 1:
                    path = Path(str(rows[0].get("path") or ""))
                    if path.is_file() and path.suffix.lower() not in bu._MEDIA_EXTS:
                        paths.append(path)
            for path in paths:
                threading.Thread(
                    target=lambda p=path: read_cached(browser, p, 1200),
                    name="faradaiq-preview-prefetch",
                    daemon=True,
                ).start()
        except Exception:
            pass

    def fast_on_tree_select(browser: Any, _event: Any = None) -> None:
        try:
            pending = getattr(browser, "_v247_selection_after", None)
            if pending is not None:
                browser.after_cancel(pending)
        except Exception:
            pass
        try:
            iids = list(browser.tree.selection())
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            for iid in iids:
                for rec in bu._tree_records(browser, iid):
                    key = bu._path_key(rec.get("path"))
                    if key not in seen:
                        rows.append(rec)
                        seen.add(key)
            mode = bu._mode_for_selection(browser, iids, rows)
            bu._set_mode_defaults(browser, mode)
            try:
                browser.qa_text.configure(state="normal")
                browser.qa_text.delete("1.0", "end")
                browser.qa_text.insert("1.0", bu._metadata_text(rows, mode))
                browser.qa_text.configure(state="disabled")
            except Exception:
                pass

            def commit() -> None:
                browser._v247_selection_after = None
                if len(rows) == 1:
                    path = Path(str(rows[0].get("path") or ""))
                    browser.selected_path = path
                    if path.suffix.lower() in bu._MEDIA_EXTS:
                        browser.selected_df = None
                        bu._display_media(browser, [path], 0)
                    else:
                        try:
                            browser.selected_df = read_cached(browser, path, 1200)
                        except Exception:
                            browser.selected_df = None
                try:
                    signature = (tuple(iids), mode)
                    if rows and signature != getattr(browser, "_v247_last_log_selection", None):
                        browser.log(f"Selection: {len(rows)} table(s), mode={mode}, cache-first preview")
                        browser._v247_last_log_selection = signature
                except Exception:
                    pass
                if len(iids) == 1:
                    prefetch(browser, iids[0])

            try:
                browser._v247_selection_after = browser.after(65, commit)
            except Exception:
                commit()
        except Exception as exc:
            try:
                browser.log(f"Selection metadata update failed: {exc}")
            except Exception:
                pass

    bu._on_tree_select = fast_on_tree_select
    bu._v247_fast_selection_installed = True


# Batch completed-job load --------------------------------------------------
def _install_main_and_browser_load() -> None:
    Window = _g("BatchQueueWindow")
    if Window is None:
        try:
            from faradaiq.v233_batch_jobs import BatchQueueWindow as Window
        except Exception:
            return
    if getattr(Window, "_v247_main_browser_load", False):
        return

    def load_selected_in_main(self: Any) -> None:
        job = self.selected_job()
        if job is None:
            return
        # Compatibility fallback for lightweight hosts/tests that expose only
        # the legacy restore action. The full BatchQueueWindow follows the
        # explicit Main Studio first, Browser second path below.
        if not callable(getattr(getattr(self, "model", None), "write_job_session", None)) or not isinstance(getattr(self, "core", None), Mapping):
            restore = getattr(self, "restore_selected_session", None)
            if callable(restore):
                restore()
            try:
                self.status_var.set(f"Loaded {job.experiment_name}; saved inspection and browser outputs were reused.")
            except Exception:
                pass
            return
        try:
            path = self.model.write_job_session(job, include_browser=bool(job.outputs.get("browser_cache")))
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            clean_payload = dict(payload)
            clean_payload.pop("batch_job", None)
            apply_session = self.core.get("_v177_apply_full_session")
            if not callable(apply_session):
                raise RuntimeError("FaradaIQ full-session loader is unavailable.")

            # Main Studio is restored first and explicitly synchronized.
            apply_session(self.parent, clean_payload, open_browsers=False)
            try:
                self.parent.file_var.set(job.source_path)
                self.parent.output_dir_var.set(job.output_dir)
                self.parent.current_summary = dict(job.inspection_summary or {})
                self.parent.current_analysis_root = str(job.outputs.get("analysis_root") or job.output_dir)
            except Exception:
                pass
            try:
                self.parent.update_idletasks()
                self.parent.lift()
                self.parent.focus_force()
            except Exception:
                pass

            # Open the prebuilt Browser only after Main Studio is loaded.
            restore_browser = self.core.get("_v177_restore_browser")
            browsers = list(clean_payload.get("open_browser_sessions") or [])
            opened = 0
            if callable(restore_browser):
                for browser_payload in browsers:
                    if restore_browser(self.parent, browser_payload) is not None:
                        opened += 1
            try:
                self.refresh(select_job=str(job.job_id))
                self.status_var.set(
                    f"Loaded {job.experiment_name} into Main Studio and opened {opened} prebuilt Browser window(s). Running jobs were unchanged."
                )
                self.parent.after(300, self.parent.lift)
            except Exception:
                pass
        except Exception as exc:
            if self.messagebox:
                self.messagebox.showerror("Load job failed", f"{exc}\n\n{traceback.format_exc()}", parent=self.win)

    Window.load_selected_in_main = load_selected_in_main
    Window._v247_main_browser_load = True


# Model default -------------------------------------------------------------
def _install_default_model() -> None:
    selected_provider = str(_CORE.get("_FARADAIQ_SELECTED_LLM_PROVIDER", "") or os.environ.get("FARADAIQ_LLM_PROVIDER", "Ollama"))
    if selected_provider.lower() in {"", "ollama"}:
        for key in ("FARADAIQ_LLM_MODEL", "FARADAIQ_OLLAMA_MODEL", "OLLAMA_MODEL"):
            current = str(os.environ.get(key, "") or "").strip()
            if current in {"", _OLD_OLLAMA_DEFAULT}:
                os.environ[key] = DEFAULT_OLLAMA_MODEL
    _CORE["_FARADAIQ_DEFAULT_OLLAMA_MODEL"] = DEFAULT_OLLAMA_MODEL

    import faradaiq.v239_scilink_edison as old
    previous = old._active_provider_settings
    if not getattr(previous, "_v247_default_wrapper", False):
        def active_provider_settings() -> dict[str, Any]:
            data = dict(previous())
            if str(data.get("provider") or "").lower() == "ollama" and str(data.get("model") or "").strip() in {"", _OLD_OLLAMA_DEFAULT}:
                data["model"] = DEFAULT_OLLAMA_MODEL
                if not str(data.get("base_url") or "").strip():
                    data["base_url"] = "http://localhost:11434/v1"
            return data
        active_provider_settings._v247_default_wrapper = True  # type: ignore[attr-defined]
        old._active_provider_settings = active_provider_settings


def _normalize_titles() -> None:
    for cls_name in ("SessionReaderGUI", "FaradaIQH5Browser", "FaradaIQDataBrowser"):
        cls = _g(cls_name)
        if cls is None:
            continue
        old_title = getattr(cls, "title", None)
        if not callable(old_title) or getattr(cls, "_v247_title_patch", False):
            continue
        def title(self: Any, text: Any = None, _old=old_title) -> Any:
            import re
            if text is None:
                return _old(self)
            return _old(self, re.sub(r"v\d+(?:\.\d+)?", VERSION, str(text)))
        cls.title = title
        cls.wm_title = title
        cls._v247_title_patch = True


def install(core: MutableMapping[str, Any]) -> None:
    global _CORE, _PREVIOUS_REVIEW
    _CORE = core
    _install_default_model()

    import faradaiq.v239_scilink_edison as old
    import faradaiq.v244_workflow_cleanup as cleanup
    import faradaiq.v245_queue_scilink as minimal
    from faradaiq import multidim_v216 as md

    _PREVIOUS_REVIEW = cleanup.show_analysis_review
    old.VERSION = VERSION
    cleanup.VERSION = VERSION
    minimal.VERSION = VERSION
    md.VERSION = VERSION

    old.show_analysis_review = _enhanced_scilink_review
    cleanup.show_analysis_review = _enhanced_scilink_review
    minimal.show_analysis_review = _enhanced_scilink_review
    core["_v247_show_analysis_review"] = _enhanced_scilink_review
    core["_v245_show_analysis_review"] = _enhanced_scilink_review
    core["_v244_show_analysis_review"] = _enhanced_scilink_review
    core["_v239_show_analysis_review"] = _enhanced_scilink_review

    md._v216_grid_values = _fast_grid_values
    md._v216_cv_cycle_view = _continuous_cv_cycle_view
    md._v216_generic_hyperspectral_view = _generic_with_play
    core["FARADAIQ_CV_SLIDER_LAYOUT"] = "continuous_forward_then_reverse"
    core["FARADAIQ_HYPERSPECTRAL_PLAYBACK"] = True

    _install_fast_browser_selection()
    _install_main_and_browser_load()
    _normalize_titles()
