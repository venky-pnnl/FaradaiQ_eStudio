"""Browser controls for reports, reproducibility, and local support."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

import pandas as pd
import streamlit as st

from . import workbench_reports as reports

_REPORT = "Report builder"
_PUBLICATION = "Publication figures"
_NOTEBOOK = "Reproducibility notebook"
_SCILINK = "SciLink handoff"
_EVIDENCE = "Evidence registry"
_RECIPE = "Reviewed recipes"
_HEALTH = "Dependency health"
_SUPPORT = "Support bundle"

_MIMES = {".html": "text/html", ".csv": "text/csv", ".json": "application/json",
          ".pdf": "application/pdf", ".svg": "image/svg+xml", ".zip": "application/zip",
          ".ipynb": "application/x-ipynb+json", ".h5": "application/x-hdf5",
          ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          ".md": "text/markdown"}


def _widget(key):
    return {"key": key, "persist_state": "session"}


def _run(tag, operation):
    try:
        with st.spinner("Working with the selected inputs…"):
            result = operation()
        st.session_state[tag + "_result"] = {key: value for key, value in result.items() if key != "preview"}
    except (ValueError, OSError, ImportError, RuntimeError) as error:
        st.error(str(error))


def _result(tag):
    receipt = st.session_state.get(tag + "_result") or {}
    for index, value in enumerate(receipt.get("files", [])):
        path = Path(value)
        if not path.is_file():
            continue
        if path.stat().st_size <= 50 * 1024**2:
            st.download_button(path.name, path.read_bytes(), file_name=path.name,
                               mime=_MIMES.get(path.suffix, "application/octet-stream"),
                               key=f"{tag}_download_{index}")
        else:
            st.code(str(path), language=None)
    return receipt


def _plot_settings(data, catalog, tag):
    active = st.session_state.get("plot_recipe")
    displayed = st.session_state.get("displayed_plot")
    if displayed and displayed.get("source") == str(data.source) and displayed.get("settings") == active:
        rendered_labels = set(displayed["trace_labels"])
        if active["measurement"] in rendered_labels:
            active = {**active, "overlays": [label for label in active.get("overlays", []) if label in rendered_labels]}
        else:
            active = None
    try:
        used_labels = reports._validate_plot(active, catalog)
    except ValueError:
        used_labels = []
    mode = st.radio("Plot source", ["Active analysis plot", "Choose measurements"] if used_labels
                    else ["Choose measurements"], horizontal=True, **_widget(tag + "_mode"))
    if mode == "Active analysis plot":
        st.caption("Plot inputs · " + " · ".join(used_labels))
        return active
    base = st.selectbox("Primary measurement", list(catalog), **_widget(tag + "_primary"))
    if not base:
        return None
    try:
        table = data.read_table(catalog[base], max_rows=15)
    except TypeError:
        table = data.read_table(catalog[base])
    except (OSError, ValueError) as error:
        st.error(str(error))
        return None
    columns = [str(name) for name in table.columns]
    from .browser_analysis import measurement_roles

    roles = measurement_roles(table)
    modes = ["XY overlay"]
    if all(roles.get(role) for role in ("frequency", "zreal", "zimag")):
        modes += ["Nyquist", "Bode"]
    if len(columns) < 2:
        st.info("Choose a measurement with two or more columns.")
        return None
    representation = st.selectbox("Plot format", modes, **_widget(tag + "_format"))
    overlays = st.multiselect("Overlay measurements", [label for label in catalog if label != base],
                              **_widget(tag + "_overlays"))
    title = st.text_input("Figure title", **_widget(tag + "_title"))
    settings = {"measurement": base, "overlays": overlays, "representation": representation, "title": title}
    if representation == "XY overlay":
        one, two = st.columns(2)
        x_hint = roles.get("potential") or roles.get("time") or columns[0]
        y_hint = roles.get("current") or roles.get("potential") or columns[1]
        x = one.selectbox("X column", columns, index=columns.index(x_hint) if x_hint in columns else 0,
                          **_widget(f"{tag}_x_{base}"))
        y_cols = [name for name in columns if name != x]
        y = two.selectbox("Y column", y_cols, index=y_cols.index(y_hint) if y_hint in y_cols else 0,
                          **_widget(f"{tag}_y_{base}"))
        one, two, three = st.columns(3)
        settings.update(x=x, y=y, invert=one.checkbox("Invert Y", **_widget(tag + "_invert")),
                        log_x=two.checkbox("Log X", **_widget(tag + "_logx")),
                        log_y=three.checkbox("Log Y", **_widget(tag + "_logy")))
    return settings


def _evidence_selection(data, workspace, catalog, tag, *, allow_plot=False):
    selected = st.multiselect("Measurement tables", list(catalog), **_widget(tag + "_measurements"))
    outcomes = reports.discover_result_files(data, workspace)
    figures = list(st.multiselect("Saved figures", [str(path) for path, item in outcomes.items() if item[0] == "figure"],
                                  format_func=lambda path: outcomes[Path(path)][1], **_widget(tag + "_figures")))
    tables = list(st.multiselect("Saved result tables", [str(path) for path, item in outcomes.items() if item[0] == "result"],
                                 format_func=lambda path: outcomes[Path(path)][1], **_widget(tag + "_tables")))
    with st.expander("Add local evidence files"):
        local = st.text_area("Figure and result file paths", placeholder="One PNG, JPEG, CSV, or JSON path per line",
                             **_widget(tag + "_local"))
    if local:
        for value in local.splitlines():
            if not value.strip():
                continue
            path = Path(value.strip()).expanduser()
            if path.suffix.lower() in reports._IMAGE_TYPES:
                figures.append(str(path))
            elif path.suffix.lower() in reports._TABLE_TYPES:
                tables.append(str(path))
            else:
                st.warning(f"Unsupported evidence format for {path.name}.")
    settings = None
    if allow_plot and st.checkbox("Generate a figure from a plot recipe", **_widget(tag + "_include_plot")):
        settings = _plot_settings(data, catalog, tag + "_plot")
        if settings:
            selected = list(dict.fromkeys([*selected, *reports._validate_plot(settings, catalog)]))
    evidence = {f"measurement:{label}": label for label in selected}
    evidence.update({f"figure:{Path(path).resolve()}": Path(path).name for path in figures})
    evidence.update({f"result:{Path(path).resolve()}": Path(path).name for path in tables})
    if settings:
        evidence["included-plot"] = "Generated figure"
    claims = []
    if evidence:
        count = st.number_input("Number of conclusions", min_value=0, max_value=16, step=1,
                                **_widget(tag + "_conclusion_count"))
        for index in range(count):
            text = st.text_area(f"Conclusion {index + 1}", height=80, **_widget(f"{tag}_claim_{index}"))
            references = st.multiselect(f"Evidence for conclusion {index + 1}", list(evidence),
                                        format_func=evidence.get, **_widget(f"{tag}_refs_{index}"))
            claims.append({"text": text, "evidence": references})
    return selected, figures, tables, settings, claims


def _link_claims(data, workspace, selected, figures, tables, claims):
    if not claims:
        return []
    refs = reports.list_evidence(data, workspace, selected, figures=figures, tables=tables)
    mapping = {f"{ref['kind']}:{ref['label'] if ref['kind'] == 'measurement' else ref['path']}": ref["id"]
               for ref in refs}
    mapping["included-plot"] = "included-plot"
    return [{"text": claim["text"], "evidence": [mapping[key] for key in claim["evidence"]]} for claim in claims]


def _report(data, workspace, catalog, tag):
    title = st.text_input("Report title", value="Electrochemical analysis", **_widget(tag + "_title"))
    context = st.text_area("Experimental context", **_widget(tag + "_context"))
    methods = st.text_area("Methods", **_widget(tag + "_methods"))
    selected, figures, tables, settings, claims = _evidence_selection(data, workspace, catalog, tag, allow_plot=True)
    formats = {"HTML": "html", "Word": "docx", "PowerPoint": "pptx"}
    chosen = st.multiselect("Report formats", list(formats), default=list(formats), **_widget(tag + "_formats"))
    if st.button("Build report", disabled=not chosen or not (selected or figures or tables or settings),
                 key=tag + "_run"):
        _run(tag, lambda: reports.write_report(
            data, workspace, selected, title=title, context=context, methods=methods,
            conclusions=_link_claims(data, workspace, selected, figures, tables, claims), figures=figures,
            tables=tables, settings=settings, formats=[formats[name] for name in chosen]))
    _result(tag)


def _publication(data, workspace, catalog, tag):
    plot = _plot_settings(data, catalog, tag)
    if st.button("Export SVG, PDF, and CSV", disabled=not plot, key=tag + "_run"):
        _run(tag, lambda: reports.write_publication_export(data, workspace, plot))
    receipt = _result(tag)
    for file in receipt.get("files", []):
        if Path(file).name == "figure_data.csv" and Path(file).is_file():
            st.dataframe(pd.read_csv(file, nrows=25), hide_index=True)


def _notebook(data, workspace, catalog, tag):
    plot = _plot_settings(data, catalog, tag)
    if not plot:
        _result(tag)
        return
    labels = reports._validate_plot(plot, catalog)
    quality = st.checkbox("Include data quality summaries", value=True, **_widget(tag + "_quality"))
    metric_inputs = st.multiselect("Measurements for trace metrics", labels, **_widget(tag + "_metrics"))
    steps = [{"tool": "plot", "settings": plot}]
    if quality:
        steps.extend({"tool": "table_quality", "measurement": label} for label in labels)
    for index, label in enumerate(metric_inputs):
        technique = st.selectbox(f"Trace technique for {label}", reports.TRACE_TECHNIQUES,
                                 **_widget(f"{tag}_technique_{index}_{label}"))
        settings = {"technique": technique}
        if technique == "OCP":
            final = st.number_input(f"Final time window (%) for {label}", min_value=0.1, max_value=100.0,
                                    value=10.0, **_widget(f"{tag}_ocp_{index}_{label}"))
            settings["final_time_percent"] = final
        steps.append({"tool": "trace_metrics", "measurement": label, "settings": settings})
    recipe = {"schema": reports.RECIPE_SCHEMA, "steps": steps}
    st.caption("The notebook embeds the selected tables and runs with an eStudio Python Jupyter kernel.")
    if st.button("Export executable notebook", key=tag + "_run"):
        _run(tag, lambda: reports.write_reproducibility_notebook(data, workspace, recipe, labels))
    _result(tag)


def _scilink(data, workspace, catalog, tag):
    mode = st.radio("Handoff scope", ["Active analysis plot", "Explicit tables"],
                    horizontal=True, **_widget(tag + "_mode"))
    selected = displayed = scope = None
    if mode == "Active analysis plot":
        displayed = st.session_state.get("displayed_plot")
        try:
            settings, traces = reports.review_scilink_plot(data, displayed)
        except ValueError as error:
            st.info(str(error))
        else:
            refs = [trace.reference for trace in traces]
            scope = {"settings": settings, "traces": refs}
            st.caption("The package includes the visible X and Y values, with the plot's conversions applied.")
            st.dataframe([{"Measurement": ref["label"], "Panel": ref["panel"], "Points": ref["rows"],
                            "X axis": ref["x_axis"], "X unit": ref["units"]["x"],
                            "Y axis": ref["y_axis"], "Y unit": ref["units"]["y"]}
                          for ref in refs], hide_index=True)
    else:
        selected = st.multiselect("Measurements for SciLink", list(catalog), **_widget(tag + "_selected"))
        if selected:
            scope = {"selected": selected}
            st.caption("The package includes every row and column from each selected table.")
    context = st.text_area("Experimental context to include", **_widget(tag + "_context"))
    reviewed = False
    identity = None
    if scope:
        identity = hashlib.sha256(json.dumps({**scope, "context": context}, sort_keys=True).encode()).hexdigest()[:20]
        reviewed = st.checkbox("I reviewed the measurements and context to include", key=tag + "_review_" + identity)
    if st.button("Prepare local SciLink files", disabled=not reviewed, key=tag + "_run"):
        _run(tag, lambda: {**reports.write_scilink_handoff(data, workspace, selected=selected,
                                                           displayed_plot=displayed, context=context),
                           "scope_identity": identity})
    if identity and st.session_state.get(tag + "_result", {}).get("scope_identity") == identity:
        receipt = _result(tag)
        st.code(receipt["instructions"], language=None)


def _evidence(data, workspace, catalog, tag, base):
    if data is not None:
        selected, figures, tables, _, claims = _evidence_selection(data, workspace, catalog, tag)
        if st.button("Save evidence registry", disabled=not claims, key=tag + "_run"):
            _run(tag, lambda: reports.write_evidence_registry(
                data, workspace, selected, _link_claims(data, workspace, selected, figures, tables, claims),
                figures=figures, tables=tables))
    receipt = _result(tag)
    report = st.session_state.get(base + "_report_result") or {}
    registry = receipt.get("registry") or report.get("registry")
    incoming = st.file_uploader("Open an evidence registry JSON", type="json", max_upload_size=2, key=tag + "_file")
    if incoming is not None:
        try:
            registry = json.loads(incoming.getvalue())
        except ValueError as error:
            st.error(str(error))
            return
    if registry is None:
        return
    if not isinstance(registry, dict) or registry.get("schema") != reports.EVIDENCE_SCHEMA:
        st.warning("Choose an eStudio evidence registry.")
        return
    entries = registry.get("evidence") or {}
    conclusions = registry.get("conclusions")
    for claim in conclusions if isinstance(conclusions, list) else []:
        if isinstance(claim, dict) and isinstance(claim.get("text"), str) and isinstance(entries, dict):
            st.text(claim["text"])
            claims = claim.get("evidence")
            labels = [entries[ref]["label"] for ref in claims if isinstance(ref, str) and
                      isinstance(entries.get(ref), dict) and "label" in entries[ref]] if isinstance(claims, list) else []
            st.caption("Evidence · " + " · ".join(map(str, labels)))
    identity = hashlib.sha256(json.dumps(registry, sort_keys=True).encode()).hexdigest()
    folder = st.text_input("Replacement evidence folder", **_widget(tag + "_root"))
    if st.button("Resolve evidence paths", key=tag + "_resolve"):
        try:
            if folder and not Path(folder).expanduser().is_dir():
                raise ValueError("Choose an existing folder containing the moved evidence.")
            rows = reports.resolve_evidence(registry, roots=[folder] if folder else [], candidates=catalog.values())
            st.session_state[tag + "_resolved"] = {"registry": identity, "rows": rows}
        except (ValueError, OSError) as error:
            st.error(str(error))
    resolved = st.session_state.get(tag + "_resolved") or {}
    if resolved.get("registry") == identity:
        st.dataframe(resolved["rows"], hide_index=True)


def _recipe(data, workspace, catalog, tag):
    allowed = st.multiselect("Measurements approved for the recipe", list(catalog), **_widget(tag + "_scope"))
    example = {"schema": reports.RECIPE_SCHEMA, "steps": [{"tool": "table_quality",
                                                            "measurement": allowed[0] if allowed else next(iter(catalog), "")}]}
    with st.expander("Recipe example"):
        st.code(json.dumps(example, indent=2), language="json")
    incoming = st.file_uploader("Choose a JSON recipe", type="json", max_upload_size=1, key=tag + "_file")
    typed = st.text_area("Or paste a JSON recipe", max_chars=50_000, **_widget(tag + "_text"))
    raw = incoming.getvalue() if incoming is not None else typed
    if raw:
        try:
            recipe = json.loads(raw)
            used = reports.validate_recipe(recipe, allowed)
        except (ValueError, TypeError) as error:
            st.warning(str(error))
        else:
            st.json(recipe)
            st.caption("Inputs · " + " · ".join(used))
            identity = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:14]
            reviewed = st.checkbox("I reviewed these operations and inputs", key=f"{tag}_review_{identity}")
            if st.button("Run reviewed recipe", disabled=not reviewed, key=tag + "_run"):
                _run(tag, lambda: reports.run_reviewed_recipe(data, workspace, recipe, allowed))
    _result(tag)


def _health(workspace, tag):
    health = reports.dependency_health()
    st.write(f"{health['application']} {health['version']} · Python {health['python']}")
    st.dataframe(health["dependencies"], hide_index=True)
    target = st.selectbox("Installer", ["SciLink", "DearEIS", "Official pyDRTtools"], **_widget(tag + "_target"))
    command = reports.repair_command(target)
    st.code(subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command), language="text")
    if st.button(f"Run {target} installer", key=tag + "_run"):
        _run(tag, lambda: reports.repair_dependency(workspace, target, reviewed=True))
    prior = st.session_state.get(tag + "_result")
    if prior:
        if prior["exit_code"] == 0:
            st.success("Installer finished successfully.")
        else:
            st.error(f"Installer exited with code {prior['exit_code']}.")
        if prior.get("command") != command:
            st.code(subprocess.list2cmdline(prior["command"]) if os.name == "nt" else
                    shlex.join(prior["command"]), language="text")
        st.code(prior["output"] or "No installer output.", language="text")


def _support(data, workspace, catalog, tag):
    available = reports.discover_diagnostic_logs(workspace)
    logs = st.multiselect("Diagnostic logs", [str(path) for path in available], **_widget(tag + "_logs"))
    diagnostic = st.checkbox("Include local dependency diagnostics", value=True, **_widget(tag + "_diag"))
    settings = st.checkbox("Include current workbench settings", **_widget(tag + "_settings"))
    raw = []
    if data is not None and st.checkbox("Include selected measurement data", **_widget(tag + "_raw_enabled")):
        raw = st.multiselect("Measurements for support bundle", list(catalog), **_widget(tag + "_raw"))
    if st.button("Build redacted support bundle", disabled=not (logs or diagnostic or settings or raw),
                 key=tag + "_run"):
        from .workbench_state import capture_settings, ephemeral_widget_keys

        parameters = None
        if settings:
            excluded = ephemeral_widget_keys() | {key for key in st.session_state if key.endswith(
                ("_result", "_results", "_receipt", "_resolved", "_history", "_undo", "_redo")
            )}
            parameters = {"workbench": capture_settings(st.session_state, excluded=excluded),
                          "plot_recipe": st.session_state.get("plot_recipe")}
        _run(tag, lambda: reports.write_support_bundle(
            workspace, diagnostics=reports.dependency_health() if diagnostic else None,
            logs=logs, settings=parameters, data=data, include_raw=raw))
    receipt = _result(tag)
    if receipt.get("manifest"):
        st.json(receipt["manifest"])


def render(data, *, workspace: Path):
    catalog = reports.measurement_catalog(data) if data is not None else {}
    source = (getattr(data, "metadata", {}).get("project_id") or str(data.source)) if data is not None else ""
    identity = str(Path(workspace).resolve()) + "|" + source
    base = "w340_reports_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
    tools = [_REPORT, _PUBLICATION, _NOTEBOOK, _SCILINK, _EVIDENCE, _RECIPE] if catalog else [_EVIDENCE]
    tools += [_HEALTH, _SUPPORT]
    route = st.selectbox("Reporting tool", tools, **_widget(base + "_tool"))
    if not catalog:
        st.caption("Open a measurement or project for report, figure, and notebook exports.")
    if route == _HEALTH:
        _health(workspace, base + "_health")
    elif route == _SUPPORT:
        _support(data, workspace, catalog, base + "_support")
    elif route == _EVIDENCE:
        _evidence(data, workspace, catalog, base + "_evidence", base)
    else:
        action, name = {
            _REPORT: (_report, "report"), _PUBLICATION: (_publication, "publication"),
            _NOTEBOOK: (_notebook, "notebook"), _SCILINK: (_scilink, "scilink"),
            _RECIPE: (_recipe, "recipe"),
        }[route]
        action(data, workspace, catalog, f"{base}_{name}")
