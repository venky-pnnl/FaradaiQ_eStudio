FaradaIQ integrated tools
=========================

This folder contains external/open-source tools bundled for local FaradaIQ workflows.

PySimpleEChem
-------------
Source bundled from the user-provided PySimpleEChem-main.zip archive, which corresponds to:
https://github.com/kevinsmia1939/PySimpleEChem

FaradaIQ Studio v273 uses the PySimpleEChem CV workflow as the CV analysis backend and keeps the
full upstream scripts in integrated_tools/PySimpleEChem for direct/manual use. The upstream
LICENSE file is preserved in that folder.

FaradaIQ integration choices:
- The native PySimpleEChem PyQt workflow can be launched from the FaradaIQ CV panel.
- A FaradaIQ headless adapter reuses the PySimpleEChem CV parameter/results model for
  browser dashboards, plot/view analysis, and export bundles.
- Older FaradaIQ lightweight CV peak buttons are hidden so CV analysis is routed through
  the PySimpleEChem workflow.

Optional DearEIS integration:
FaradaIQ does not vendor DearEIS. The package includes only a FaradaIQ handoff adapter and optional installer that installs DearEIS into a separate .deareis_venv so it does not disturb FaradaIQ pinned dependencies. DearEIS credit: DearEIS developers / Ville Yrjana. Documentation: https://vyrjana.github.io/DearEIS/
