# FaradaIQ eStudio v341: workbench guide

The advanced workbench brings several instrument sources into one comparison session. It adds the 80 functions in the accompanying [feature coverage](workbench_features_v341.md). Existing desktop and browser tools remain accessible.

## Start a comparison session

1. Install with `Install_FaradaIQ_eStudio_v341.bat` on Windows or `Install_FaradaIQ_eStudio_v341.command` on macOS. Open **FaradaIQ eStudio Browser → Workbench**. The desktop's **Advanced workbench** button opens the same local tools with the current source.
2. Choose **Projects and imports → Import sources**. Enter local files or folders, one path per line, or upload files. Keep required instrument companion files together. Preview a source and review quantities, units, acquisition structure and diagnostics.
3. Choose source headers or map columns and input units. Save a reusable JSON import profile if needed. Enter the instrument and source label, then queue imports. **Add currently open data** applies the reviewed mapping to the open dataset.
4. Use **Organize** to rename measurements and add tags, reference-electrode context, annotations and sample information. Same-named sources have separate handles and readable measurement paths. Renaming changes the displayed label.
5. Choose **Compare and process** to normalize individual measurements, apply processing, compare sources, or calculate replicate and treatment summaries. Pickers include the source path even when legend labels are the same.
6. Save the project in **Projects and packages**, or use **Quick save session** in the sidebar. Sessions retain the project, experiment context, plotting choices and workbench settings. Opening another project clears the preceding project's session destination and context.

Each handle identifies an imported source. Its records retain instrument, original path, units, names and tags. `Measurements` contains the active catalog. `Sources` retains canonical recovery copies for undo, redo and checkpoints. Calculations read these tables and write separate results. Removing a handle removes it from current comparisons and current-data exports. Source files remain unchanged.

## Choose a workbench area

| Area | Use it for |
| --- | --- |
| Projects and imports | Preview and map inputs; manage instruments, measurements and tags; queue imports; watch a folder; search, checkpoint, relocate and package projects. |
| Compare and process | Area/reference corrections, baseline/smoothing/masks, cycles, plot windows, linked/side-by-side panels, difference/ratio, replicate/treatment statistics, batch recipes and uncertainty. |
| Electrochemical science | CV, scan-rate, charge, capacitance, ECSA, kinetics, transients, deposition, efficiency, Tafel/corrosion, Mott–Schottky, temperature and rotating-electrode tools. |
| Impedance and DRT series | Circuit construction/model comparison, local/global fits, residuals/covariance, frequency masks, batch lin-KK, regularization scans, peak tracking/integration and condition maps. |
| Spatial and event comparisons | Affine optical registration, ROIs/line profiles, before/after differences, quality flags, scalar correlations and stimulus-event alignment. |
| Reports and reproducibility | HTML/DOCX/PPTX reports, vector figures/values, replay notebooks, selected SciLink handoffs, evidence links, reviewed recipes, health checks and redacted support exports. |

## Compare compatible measurements

Declare input units before numerical comparison. Current and current density are distinct quantities; an area conversion needs measured electrode area. Potential conversion needs recorded/target references, calibrated offsets where applicable, and measured pH and temperature for RHE conversion. Independent handles require a declared compatible potential basis for numerical potential-axis comparisons, even when the instruments share a model name. Series analyses check reference metadata and explicit conversion offsets too.

Curve arithmetic uses measured points in the shared domain without extrapolation. Select a sweep for reversing CV data and resolve repeated x values explicitly. Processing previews distinguish proposed settings from applied recipes. **Apply processing**, undo and redo retain per-measurement history. Full portable projects and relocated sessions retain histories and selections.

Replicate/treatment summaries require independent acquisitions. Duplicate views of the same original table cannot increase sample size. A separate acquisition with identical bytes at a different path can be included after an explicit duplicate override. Pointwise confidence intervals and paired/Welch comparisons describe selected observations; they do not establish experimental independence.

Circuit fits support series resistance, parallel RC/R–CPE branches and optional semi-infinite Warburg. Outputs include bounds, residuals, degrees of freedom and identifiability diagnostics. Rank-deficient models do not receive physical parameter estimates or comparable model scores. DRT uses nonnegative regularized RC inversion on a common finite log-time grid; tracks break at failed acquisitions. See [impedance methods](workbench_impedance_methods.md) and [electrochemical methods](workbench_science_methods.md) for equations and assumptions.

## Save and recover work

Edits write recovery checkpoints. **Recover latest checkpoint** restores the latest saved catalog. **Apply relocation** copies canonical data and remaps settings; original provenance remains recorded. A portable ZIP includes all or selected sources. Full packages retain comparison settings. Subsets start with fresh comparison settings to avoid omitted measurements.

Watch importing checks supported files in the selected directory while its route stays open. It waits for a quiet interval and rechecks signatures during import. Diagnostics detect known structural problems and missing companions; valid-looking partial acquisitions still need review. STOP cancels waiting imports after the current source finishes. Long optimizations complete at their current operation boundary.

## Export a reviewed result

Publication exports include SVG/PDF, plotted values, units and source metadata. Reports contain chosen evidence and conclusion links, with full selected tables in an evidence package. Notebooks embed selected inputs and replay supported XY/EIS plotting, quality and trace-metric recipes in an eStudio v341 environment.

For SciLink, render a plot in **Analysis → Plots and table**, then choose **Reports and reproducibility → SciLink handoff → Active analysis plot**. Review the exact finite, log-visible traces and context. The handoff contains those plotted X/Y values, provenance and the unchanged electrochemistry skill. Explicit table mode exports chosen tables instead. Client-side zoom/legend changes are not captured. Exporting does not submit a provider request.

Reviewed local recipes accept bounded supported operations and chosen tables, without arbitrary generated code. Health checks are local; repair requires reviewing an installer action. Support bundles redact recognized credentials and omit raw tables unless explicitly selected. Review bundles before sharing.

## Long filenames and the import error

v341 parses native data in a temporary directory, then budgets actual final descendants. It avoids repeatedly nesting the source stem and excludes a redundant copy of installed parser scripts. Readable folders and a name map retain original identity when generated filenames must be shortened. Source bytes, scientific tables and HEKA/DRT outputs are preserved.

Regression coverage includes long-name PalmSens/Solartron, HEKA DAT2 and archived companions, and an approximately 1.6 MB synthetic uploaded HEKA file. The exact screenshot file was unavailable. An output root that itself leaves too little room gets an error pointing to **Settings → Local output folder**; choose a short root such as `C:\Data\ANALYSIS\Faradaiq`.

See [QA_v341.md](../QA_v341.md) for verification and platform limits.
