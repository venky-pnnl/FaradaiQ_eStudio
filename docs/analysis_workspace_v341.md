# FaradaIQ eStudio v341: browser Analysis

Open data, then choose **Analysis** in the browser sidebar. The workspace places measurements on the left, the interactive graph in the center and plot controls on the right.

## Select and compare

Search by filename, folder, point, cycle or potential. Click a measurement row to open it. Previous/Next moves within the filtered list; **Jump to measurement** offers a searchable dropdown. Short labels identify files without repeating their entire output hierarchy.

Position_Takedata and declared position/metadata support records are hidden initially when other measurements exist. Enable **Include position / support records** to inspect them. If the dataset contains only support records, they remain visible. A position record may contain constant monitor values; the page identifies it rather than treating its flat trace as proof that the imported experiment has no signal.

Use **Overlay measurements** in the right panel to add sources. Recognized compatible units are reconciled to the active axes, as in v340. Conflicting known potential references require conversion before overlaying. Numeric cross-source analyses remain in **Workbench → Compare and process** with their unit/reference checks.

## Plot and inspect

**X column**, **Y column** and **Plot format** stay near the top of the controls panel. **Axis scale** contains log axes and Y inversion. **Plot appearance** contains custom titles, theme, line/point style, graph height and legend visibility. The panel scrolls independently, so expanded settings do not move the graph down the page.

Drag to zoom, use the graph toolbar to pan or save PNG, and select **Fit to data** to return to the measured span. **Focus plot** hides the measurement/control columns and expands the plot. Settings remain available in its **Plot controls** popover. Turning Focus plot off restores the normal workspace and selected measurement.

Small numerical spans, including picoampere and femtoampere signals, are fitted explicitly. Display ranges do not alter source or exported values. A constant recorded signal gets a caption identifying its actual value. Unknown physical units are not inferred from the shape of the data.

## Keep identity and results

Legends use compact, unique labels. **Source details** shows the full active measurement path. **Export displayed plot** retains full measurement identities in CSV and HDF5. Standalone HTML retains the interactive figure and its metadata. SciLink handoffs still use the exact rendered trace scope and the unchanged electrochemistry analysis skill.

**View measurement table** and **Data quality and detected columns** remain below the graph. The other Analysis tabs retain **Analysis tools**, **Interactive views** and **Compare trace metrics**. Sidebar plot undo/redo and quick-save sessions remain available. v340 workbench projects keep their state-key format.

## Install

Extract the complete `FaradaIQ_eStudio_v341_package.zip`, run the v341 installer for Windows or macOS, then open **FaradaIQ eStudio Browser**. Keep v340 as a rollback installation if needed. The versioned launchers and stable app shortcuts use v341 after installation.

See `QA_v341.md` in the package for the automated checks and platform limits. The original acquisition shown in the screenshots was not supplied, so the small-signal and Position_Takedata cases were exercised with synthetic tables.
