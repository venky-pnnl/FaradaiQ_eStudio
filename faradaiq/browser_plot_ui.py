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


def _axis_caption(data, path, column):
    from .scilink_bundle import column_unit
    record = data.records.get(path, {})
    unit = column_unit(column)
    units = record.get('units')
    if not unit and isinstance(units, dict): unit = units.get(column)
    for role in ('x', 'y'):
        if not unit and str(record.get(role + '_label', '')).casefold() == str(column).casefold():
            unit = record.get(role + '_unit')
    return str(column) if not unit or column_unit(column) else f'{column} ({unit})'


def _plot_controls(data, labels, path, table, roles, technique, saved, key):
    st.subheader('Plot controls')
    representations = ('XY overlay', 'Nyquist', 'Bode') if all(roles.get(r) for r in ('frequency','zreal','zimag')) else ('XY overlay',)
    default_plot = saved.get('representation')
    representation = st.selectbox('Plot format', representations,
                                  index=representations.index(default_plot) if default_plot in representations else 0,
                                  key=f'{key}:kind')
    recipe = {'measurement': labels[path], 'representation': representation}
    if representation == 'XY overlay':
        columns = list(table.columns)
        timed = technique in {'CA', 'CP', 'OCP', 'Deposition'}
        default_x = (roles.get('time') if timed else roles.get('potential') or roles.get('zreal')) or roles.get('time')
        default_y = (roles.get('potential') if technique in {'CP', 'OCP'} else roles.get('current')) or roles.get('zimag')
        requested_x, requested_y = saved.get('x', default_x), saved.get('y', default_y)
        x = st.selectbox('X column', columns, index=columns.index(requested_x) if requested_x in columns else 0, key=f'{key}:x')
        y = st.selectbox('Y column', columns, index=columns.index(requested_y) if requested_y in columns else min(1,len(columns)-1), key=f'{key}:y')
        with st.expander('Axis scale'):
            invert_default = y == roles.get('zimag') and not negative_impedance_label(y)
            invert = st.checkbox('Invert Y', value=bool(saved.get('invert', invert_default) if saved.get('y') in {None,y} else invert_default), key=f'{key}:invert:{y}')
            log_x = st.checkbox('Log X', value=bool(saved.get('log_x',False)), key=f'{key}:log-x')
            log_y = st.checkbox('Log Y', value=bool(saved.get('log_y',False)), key=f'{key}:log-y')
        recipe.update(x=x,y=y,invert=invert,log_x=log_x,log_y=log_y)
    from .browser_analysis_ui import short_measurement_labels
    short = short_measurement_labels(data)
    candidates = [p for p in data.tables if p != path]
    with st.expander('Overlay measurements', expanded=bool(saved.get('overlays'))):
        overlays = st.multiselect('Overlay measurements', candidates, format_func=short.get,
                                  default=[p for p in candidates if labels[p] in saved.get('overlays',[])],
                                  key=f'{key}:overlays')
        st.caption('Each source keeps its recorded identity and units.')
    style_choices = ('Lines and points','Lines','Points')
    with st.expander('Plot appearance', expanded=False):
        title = st.text_input('Plot title', value=saved.get('title',''), key=f'{key}:title')
        x_title = st.text_input('X axis label', value=saved.get('x_title',''), key=f'{key}:x-title')
        y_title = st.text_input('Y axis label', value=saved.get('y_title',''), key=f'{key}:y-title')
        theme = st.selectbox('Plot theme', ('Light','Dark'), index=int(saved.get('theme')=='Dark'), key=f'{key}:theme')
        style = saved.get('trace_style')
        trace_style = st.selectbox('Trace style',style_choices,index=style_choices.index(style) if style in style_choices else 0,key=f'{key}:style')
        height = st.slider('Plot height (px)',360,900,value=int(saved.get('height',560)),step=20,key=f'{key}:height')
        legend = st.checkbox('Show legend',value=bool(saved.get('legend',True)),key=f'{key}:legend')
    recipe.update(overlays=[labels[p] for p in overlays],title=title,x_title=x_title,y_title=y_title,
                  theme=theme,trace_style=trace_style,height=height,legend=legend)
    # Assign after rendering this run: changing another widget's live value here
    # would conflict with Streamlit. The button callback runs before the rerun.
    st.button('Reset plot defaults',key=f'reset-plot:{path}',on_click=_reset_plot,args=(key,))
    return recipe, overlays


def _reset_plot(key):
    for stored in list(st.session_state):
        if stored.startswith(key+':'): st.session_state.pop(stored,None)
    for stored in ('plot_recipe','prepared_plot','displayed_plot'):
        st.session_state.pop(stored,None)


def render_measurement_plot(data, labels, path, table, roles, technique, *, focus=False):
    import numpy as np
    from .browser_productivity import measurement_label, measurement_reference, prepare_xy_series
    from .browser_analysis_ui import legend_labels

    st.session_state.pop('displayed_plot',None)
    key=f'plot:{path}'
    saved=st.session_state.get('plot_recipe') or {}
    if saved.get('measurement') != labels[path]: saved={}
    if focus:
        canvas=st.container(key='analysis-canvas')
        with st.popover('Plot controls',use_container_width=False):
            recipe,overlays=_plot_controls(data,labels,path,table,roles,technique,saved,key)
    else:
        canvas,controls=st.columns((4,1.65),gap='medium')
        with controls,st.container(border=True,height=650,key='analysis-controls'):
            recipe,overlays=_plot_controls(data,labels,path,table,roles,technique,saved,key)
    st.session_state['plot_recipe']=recipe
    from .workbench_state import StateHistory
    if 'plot_history' not in st.session_state: st.session_state['plot_history']=StateHistory()
    st.session_state['plot_history'].push(recipe)
    representation=recipe['representation']
    title,x_title,y_title,height,theme,legend=(recipe[k] for k in ('title','x_title','y_title','height','theme','legend'))
    with canvas:
        toolbar=st.columns((2.2,1,1))
        toolbar[0].caption('Drag to zoom · double-click to fit · toolbar for pan and PNG')
        if toolbar[1].button('Fit to data',key=f'{key}:fit-data',use_container_width=True):
            st.session_state[f'{key}:view-revision']=st.session_state.get(f'{key}:view-revision',0)+1
        with toolbar[2].popover('Source details',use_container_width=True):
            st.caption('Original measurement identity')
            st.write(measurement_label(data,path))
            st.code(str(path),language=None)
        messages=[]
        try:
            series=[(path,table)]
            for item in overlays:
                try:series.append((item,data.read_table(item)))
                except (OSError,ValueError) as error:messages.append(f'Skipped {labels[item]}: {error}')
            names=legend_labels(data,[item for item,_ in series])
            if representation=='XY overlay':
                x,y,invert,log_x,log_y,trace_style=(recipe[k] for k in ('x','y','invert','log_x','log_y','trace_style'))
                if roles.get('potential') in {x,y}:
                    active_reference=measurement_reference(data,path)
                    compatible_references=[]
                    for item,frame in series:
                        reference=measurement_reference(data,item)
                        if item!=path and active_reference and reference and ''.join(active_reference.casefold().split())!=''.join(reference.casefold().split()):
                            messages.append(f'Skipped {labels[item]}: potential is recorded vs {reference}, while the active measurement uses {active_reference}. Convert references in Compare and process first.')
                        else:compatible_references.append((item,frame))
                    series=compatible_references
                series,skipped=prepare_xy_series(series,x,y)
                messages.extend(f'Skipped overlay: {message}' for message in skipped)
                figure=build_xy_figure(series,x,y,invert=invert,log_x=log_x,log_y=log_y,title=title,
                                       x_title=x_title or _axis_caption(data,path,x),y_title=y_title or (('−' if invert else '')+_axis_caption(data,path,y)),
                                       height=height,theme=theme,legend=legend,trace_style=trace_style,names=names)
                if log_x or log_y:
                    hidden=sum(int(np.sum((np.asarray(trace.x,dtype=float)<=0) if log_x else np.zeros(len(trace.x),dtype=bool)))+
                               int(np.sum((np.asarray(trace.y,dtype=float)<=0) if log_y else np.zeros(len(trace.y),dtype=bool))) for trace in figure.data)
                    if hidden:messages.append('Log axes hide nonpositive values. Plot exports retain them; use linear axes to see them.')
                plotted=series
            else:
                from .browser_analysis import measurement_roles,eis_table
                compatible=[]
                for item,frame in series:
                    try:
                        frame_roles=measurement_roles(frame)
                        spectrum=eis_table(frame,negate_imaginary=negative_impedance_label(frame_roles.get('zimag')))
                        if spectrum.attrs.get('excluded_rows'):messages.append(f"{labels[item]}: excluded {spectrum.attrs['excluded_rows']} invalid EIS row(s).")
                        compatible.append((item,spectrum))
                    except ValueError as error:messages.append(f'Skipped {labels[item]}: {error}')
                figure=build_impedance_figure(compatible,representation=representation,title=title,height=max(height,580),theme=theme,legend=legend,names=names)
                plotted=compatible
            if not figure.data:
                st.warning('No selected traces have the required columns.')
                return
        except (ValueError,OSError) as error:
            st.error(f'Cannot plot the selected measurements: {error}')
            return
        figure.update_layout(uirevision=f'{path}:{representation}:{recipe.get("x")}:{recipe.get("y")}:{recipe.get("log_x")}:{recipe.get("log_y")}:{recipe.get("invert")}:{st.session_state.get(f"{key}:view-revision",0)}')
        trace_labels=tuple(label for item,_ in plotted for label in ([labels[item]]*(2 if representation=='Bode' else 1)))
        for trace, identity in zip(figure.data, trace_labels):
            trace.meta={'measurement':identity}
        st.plotly_chart(figure,theme=None,width='stretch',key=f'{key}:chart',config={'displaylogo':False,'scrollZoom':True,'toImageButtonOptions':{
            'filename':f'{safe_component(path.stem)}_{APP_VERSION}','scale':2}})
        st.session_state['displayed_plot']={'source':str(data.source),'settings':recipe.copy(),'figure':figure,'trace_labels':trace_labels}
        if representation=='XY overlay':
            values=np.asarray(figure.data[0].y,dtype=float)
            finite=values[np.isfinite(values)]
            if len(finite) and finite.max()==finite.min():
                st.caption(f'Constant recorded signal: {finite[0]:.7g}. Review the selected measurement or source table if a varying trace was expected.')
        if overlays:st.caption('Compatible axes use the active measurement’s units. Potential overlays retain recorded reference scales; numerical comparisons require a reviewed common reference.')
        for message in messages:st.warning(message)
        with st.expander('Export displayed plot'):
            st.caption('CSV and HDF5 retain the displayed numeric values, including sign and Bode transformations.')
            if st.button('Prepare plot files',key=f'{key}:prepare'):
                st.session_state['prepared_plot']=(recipe.copy(),displayed_plot_csv(figure),displayed_plot_hdf5(figure),figure.to_html(include_plotlyjs=True).encode('utf-8'))
            prepared=st.session_state.get('prepared_plot')
            if prepared and prepared[0]==recipe:
                basename=f'{safe_component(path.stem)}_plot_{APP_VERSION}'
                for title,payload,suffix,mime in zip(('Download displayed CSV','Download displayed HDF5','Download standalone interactive plot'),prepared[1:],('csv','h5','html'),('text/csv','application/x-hdf5','text/html')):
                    st.download_button(title,payload,f'{basename}.{suffix}',mime=mime,key=f'{key}:download:{suffix}')
            st.caption('Use the plot toolbar for a 2× PNG or fullscreen view.')


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
