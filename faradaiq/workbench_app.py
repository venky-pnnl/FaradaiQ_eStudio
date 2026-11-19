"""Lazy advanced workspaces for multi-instrument sessions."""
from importlib import import_module

SECTIONS = {
    'Projects and imports': ('workbench_project_ui', 'Add instruments, organize measurements, and recover projects.'),
    'Compare and process': ('workbench_compare_ui', 'Normalize, process, compare replicates, and save recipes.'),
    'Electrochemical science': ('workbench_science_ui', 'Analyze scan-rate, transient, kinetic, and deposition series.'),
    'Impedance and DRT series': ('workbench_impedance_ui', 'Build circuits and compare fits and relaxation features.'),
    'Spatial and event comparisons': ('workbench_spatial_ui', 'Register maps, compare regions, correlate modalities, and align events.'),
    'Reports and reproducibility': ('workbench_reports_ui', 'Create reports, notebooks, reviewed handoffs, and diagnostics.'),
}


def render(data, *, workspace):
    import streamlit as st
    st.header('Advanced workbench')
    st.caption('Multiple instruments, one session. Each measurement retains its source and units.')
    section = st.selectbox('Workbench area', list(SECTIONS), key='workbench_section', persist_state='session')
    name, description = SECTIONS[section]
    st.caption(description)
    if data is None and section not in {'Projects and imports', 'Reports and reproducibility'}:
        st.info('Open data or add sources in Projects and imports first.')
        return
    import_module('faradaiq.' + name).render(data, workspace=workspace)
