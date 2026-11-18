"""Streamlit controls for interactive curves, desktop-generated views, and exports."""
from __future__ import annotations

from pathlib import Path
import webbrowser

import streamlit as st

from .browser_plotting import (
    build_impedance_figure, build_xy_figure, displayed_plot_csv, displayed_plot_hdf5,
    native_view_records, negative_impedance_label, write_native_view_archive,
)
from .readable_names import safe_component
from .release_identity import APP_VERSION


def measurement_technique(data, path, roles):
    from .v290_portable_browser import _technique_key

    record = data.records.get(path, {})
    declared = record.get("technique_key") or record.get("technique") or ""
    technique = _technique_key(declared, path=path.name)
    aliases = {
        "CV": "CV", "CV/LSV": "CV", "LSV": "LSV", "FSCV": "FSCV", "CA": "CA",
        "CP": "CP", "OCP": "OCP", "DPV": "DPV", "SWV": "SWV", "NPV": "NPV",
        "STRIPPING": "Stripping", "DEPOSITION": "Deposition",
    }
    if technique in aliases:
        return aliases[technique]
    if roles.get("time") and roles.get("potential") and not roles.get("current"):
        return "OCP"
    if roles.get("time") and roles.get("current") and not roles.get("potential"):
        return "CA"
    return "CV"


def render_measurement_plot(data, labels, path, table, roles, technique):
    saved = (st.session_state.get("plot_recipe") or {})
    if saved.get("measurement") != labels[path]:
        saved = {}
    key = f"plot:{path}"
    representations = ("XY overlay", "Nyquist", "Bode") if all(
        roles.get(role) for role in ("frequency", "zreal", "zimag")) else ("XY overlay",)
    default_plot = saved.get("representation")
    representation = st.selectbox("Plot format", representations,
                                  index=representations.index(default_plot) if default_plot in representations else 0,
                                  key=f"{key}:kind")
    candidates = [item for item in data.tables if item != path]
    overlays = st.multiselect("Overlay measurements", candidates, format_func=labels.get,
                              default=[item for item in candidates if labels[item] in saved.get("overlays", [])],
                              key=f"{key}:overlays")
    trace_default = "Lines and points"
    style_choices = (trace_default, "Lines", "Points")
    with st.expander("Plot appearance"):
        title = st.text_input("Plot title", value=saved.get("title", ""), key=f"{key}:title")
        x_title = st.text_input("X axis label", value=saved.get("x_title", ""), key=f"{key}:x-title")
        y_title = st.text_input("Y axis label", value=saved.get("y_title", ""), key=f"{key}:y-title")
        theme = st.selectbox("Plot theme", ("Light", "Dark"),
                             index=int(saved.get("theme") == "Dark"), key=f"{key}:theme")
        style = saved.get("trace_style")
        trace_style = st.selectbox("Trace style", style_choices,
                                   index=style_choices.index(style) if style in style_choices else 0, key=f"{key}:style")
        height = st.slider("Plot height (px)", 360, 900, value=int(saved.get("height", 480)), step=20, key=f"{key}:height")
        legend = st.checkbox("Show legend", value=bool(saved.get("legend", True)), key=f"{key}:legend")
    recipe = {
        "measurement": labels[path], "representation": representation, "overlays": [labels[p] for p in overlays],
        "title": title, "x_title": x_title, "y_title": y_title, "theme": theme,
        "trace_style": trace_style, "height": height, "legend": legend,
    }
    columns = list(table.columns)
    if representation == "XY overlay":
        timed = technique in {"CA", "CP", "OCP", "Deposition"}
        default_x = (roles.get("time") if timed else roles.get("potential") or roles.get("zreal")) or roles.get("time")
        default_y = (roles.get("potential") if technique in {"CP", "OCP"} else roles.get("current")) or roles.get("zimag")
        requested_x = saved.get("x", default_x)
        requested_y = saved.get("y", default_y)
        axis_ui = st.columns(2)
        x = axis_ui[0].selectbox("X column", columns, index=columns.index(requested_x) if requested_x in columns else 0,
                                 key=f"{key}:x")
        y = axis_ui[1].selectbox("Y column", columns, index=columns.index(requested_y) if requested_y in columns else 1,
                                 key=f"{key}:y")
        options = st.columns(3)
        invert_default = y == roles.get("zimag") and not negative_impedance_label(y)
        previous_invert = saved.get("invert", invert_default) if saved.get("y") in {None, y} else invert_default
        invert = options[0].checkbox("Invert Y", value=bool(previous_invert), key=f"{key}:invert:{y}")
        log_x = options[1].checkbox("Log X", value=bool(saved.get("log_x", False)), key=f"{key}:log-x")
        log_y = options[2].checkbox("Log Y", value=bool(saved.get("log_y", False)), key=f"{key}:log-y")
        recipe.update(x=x, y=y, invert=invert, log_x=log_x, log_y=log_y)

    st.session_state["plot_recipe"] = recipe
    try:
        series = [(path, table), *((item, data.read_table(item)) for item in overlays)]
        if representation == "XY overlay":
            figure = build_xy_figure(series, x, y, invert=invert, log_x=log_x, log_y=log_y, title=title,
                                     x_title=x_title, y_title=y_title, height=height, theme=theme, legend=legend,
                                     trace_style=trace_style)
        else:
            figure = build_impedance_figure(series, representation=representation, title=title,
                                            height=max(height, 580), theme=theme, legend=legend)
        if not figure.data:
            st.warning("No selected traces have the required columns.")
            return
    except (ValueError, OSError) as error:
        st.error(f"Cannot plot the selected measurements: {error}")
        return
    st.plotly_chart(figure, config={"displaylogo": False, "toImageButtonOptions": {
        "filename": f"{safe_component(path.stem)}_{APP_VERSION}", "scale": 2,
    }})
    with st.expander("Export displayed plot"):
        st.caption("CSV and HDF5 contain the displayed data values, including any sign or Bode transformation.")
        if st.button("Prepare plot files", key=f"{key}:prepare"):
            st.session_state["prepared_plot"] = (
                recipe.copy(), displayed_plot_csv(figure), displayed_plot_hdf5(figure),
                figure.to_html(include_plotlyjs=True).encode("utf-8"),
            )
        prepared = st.session_state.get("prepared_plot")
        if prepared and prepared[0] == recipe:
            basename = f"{safe_component(path.stem)}_plot_{APP_VERSION}"
            for title, payload, suffix, mime in zip(
                ("Download displayed CSV", "Download displayed HDF5", "Download standalone interactive plot"),
                prepared[1:], ("csv", "h5", "html"), ("text/csv", "application/x-hdf5", "text/html"),
            ):
                st.download_button(title, payload, f"{basename}.{suffix}", mime=mime, key=f"{key}:download:{suffix}")
        st.caption("Use the plot toolbar for a 2× PNG or to view the plot fullscreen.")


def render_native_views(data, labels, active, *, workspace, submit, busy, show_file):
    from .browser_analysis import make_native_view

    st.subheader("Interactive views")
    st.caption("Use the desktop plot engines to build interactive 2D maps, 3D views, and hyperspectral stacks.")
    selected = st.multiselect("Measurements for interactive view", data.tables, default=[active],
                              format_func=labels.get, key=f"native-view:{active}")
    mode = st.selectbox("Interactive view", ("2D", "3D", "Hyperspectral"))
    cols = st.columns(3)
    metric = cols[0].selectbox("Point metric", ("Auto", "peak_abs", "mean", "integral", "min", "max"))
    spatial_unit = cols[1].selectbox("Position units", ("µm", "mm", "m"))
    palette = cols[2].selectbox("Color palette", ("Viridis", "Cividis", "Plasma", "Turbo"))
    if st.button("Build interactive view", disabled=not selected or busy(), type="primary"):
        records, positions = native_view_records(data, selected)
        output = workspace() / "Results" / "Interactive views"
        label = data.source.stem
        def operation(_cancel, report):
            report("Building an interactive view from the selected measurements.")
            receipt = make_native_view(records, output, mode=mode, metric=metric, positions=positions,
                                       label=label, options={"spatial_unit": spatial_unit, "palette": palette})
            entry = Path(receipt["html"])
            return receipt | {"archive": write_native_view_archive(entry), "source": str(data.source)}
        submit("Interactive view", operation)
    result = st.session_state.get("Interactive view")
    if result and result.get("source") == str(data.source):
        path = Path(result["html"])
        st.write(result.get("engine", "Interactive view"))
        st.code(str(path), language=None)
        if path.is_file() and st.button("Open interactive view in a separate tab"):
            if not webbrowser.open(path.resolve().as_uri(), new=2):
                st.warning("Open the saved HTML file using your browser.")
        show_file("Download interactive view with assets", result["archive"])
