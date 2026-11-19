# Reports and reproducibility

Open **Workbench → Reports and reproducibility**. The reporting tool uses the current project and the input scope shown on screen.

| Tool | Workflow |
| --- | --- |
| Report builder | Select measurement tables, saved results, figures, or a generated plot. Enter methods and experimental context. Link each conclusion to its evidence. Download standalone HTML, Word, or PowerPoint and the full evidence package. |
| Publication figures | Reuse the active Analysis plot or select plot inputs and axes. Download SVG and PDF with the plotted CSV. The CSV records axis units, panel, source identifiers, and source hashes. |
| Reproducibility notebook | Reuse or define a plot and select quality and trace metrics. Open the exported notebook with an eStudio Python Jupyter kernel and run all cells. The notebook checks its embedded table hashes before replaying. |
| SciLink handoff | Review the active Analysis plot or choose explicit tables. Download the HDF5, metadata, and electrochemistry skill for manual review in SciLink Analyze. |
| Evidence registry | Link conclusions to individual sources. For moved files, open the registry and select a replacement root. The resolver checks file contents against recorded hashes. |
| Reviewed recipes | Select the approved measurements, open or paste a JSON recipe, and review the displayed steps. Run the approved recipe to create local results. |
| Dependency health | Review the local dependency status and installer command. Run the selected installer when you want to repair it. |
| Support bundle | Choose diagnostics, settings, and logs to include. Measurement tables require an additional selection. Review the generated ZIP before sharing it. |

## Report inputs

PNG and JPEG files can be embedded as figures. CSV or JSON files become result tables. Add local paths in the report or evidence controls, or select outputs written by workbench tools for the current source. The HTML report embeds the complete selected evidence and links conclusions to their individual evidence sections. Word and PowerPoint display compact table previews. The evidence ZIP contains the complete input snapshots.

## Reviewed recipe format

The allowed operations are listed below. Measurement names match the relative labels shown in the reporting controls.

| Operation | Required fields | Optional settings |
| --- | --- | --- |
| plot | `settings.measurement` and `settings.representation` | `overlays`, `title`, `x_title`, `y_title`, `invert`, `log_x`, `log_y`, `legend`, `theme`, `trace_style`, `height` |
| table_quality | `measurement` | |
| trace_metrics | `measurement` and `settings.technique` | `potential`, `current`, `time`, OCP `final_time_percent`, CP `applied_current_A` |

Choose **XY overlay**, **Bode**, or **Nyquist** for the plot representation. XY overlays also require the primary measurement's `x` and `y` column names. Unit conversion uses explicit supported units in the column headers. The trace metrics accept CV, LSV, FSCV, CA, deposition, CP, OCP, DPV, SWV, NPV, and stripping.

Example for a project containing `CV/sample.csv` and `CV/replicate.csv`:

```json
{
  "schema": "faradaiq-workbench-recipe-1",
  "steps": [
    {
      "tool": "plot",
      "settings": {
        "measurement": "CV/sample.csv",
        "overlays": ["CV/replicate.csv"],
        "representation": "XY overlay",
        "x": "potential_V",
        "y": "current_A",
        "title": "Sample and replicate"
      }
    },
    {"tool": "table_quality", "measurement": "CV/sample.csv"},
    {"tool": "trace_metrics", "measurement": "CV/sample.csv", "settings": {"technique": "CV"}}
  ]
}
```
