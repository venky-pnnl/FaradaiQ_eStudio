# FaradaIQ eStudio v341

The browser Analysis page now puts the graph beside a compact measurement browser and a scrollable plot-controls panel. The graph is visible without scrolling through appearance settings.

- Search and select a measurement in the left list, or use Previous/Next and Jump to measurement.
- Keep axes, overlays and appearance settings in the right panel. Expand **Plot appearance** for titles, theme, style, height and legend visibility.
- Use **Focus plot** to expand the graph and open settings from **Plot controls**. **Fit to data** restores the numeric data span after zooming.
- Short, unique legend labels avoid displaying entire generated folder paths. **Source details**, CSV/HDF5 exports and SciLink handoffs retain full measurement identity.
- Small-current and constant nonzero signals receive data-based axis ranges, including nA, pA and fA values. Plot values are unchanged. Constant signals are identified below the plot.
- Position_Takedata and declared position/metadata support records are hidden from the initial measurement list when electrochemical traces are available. **Include position / support records** restores them for inspection. They are explicitly identified when selected.
- Recorded unit metadata supplies axis units when a header omits them. Unknown units remain unknown.

Existing analysis tools, interactive views, trace comparison, report exports, SciLink workflows and all 80 advanced workbench functions remain available. Existing workbench state keys are retained so v340 project/session settings can be read.

Built from the unchanged v340 package, SHA-256 `a8058fd86ff23d0c47992c13457340c549119bb6ccd79e0113272430ed66937e`.

See [Analysis guide](docs/analysis_workspace_v341.md) and [verification](QA_v341.md).
