# FaradaIQ eStudio v337

- The GitHub README and styled homepage show the import → analysis → export workflow, the full supported-method and file-format tables, and the Windows and macOS setup. The illustrated overview includes a current browser capture. The detailed guide covers SciLink, DearEIS, pyDRTtools, Edison, LabRIC, and the method demos.
- The full credits include the original SECCM parser, analysis-method developers, integration teams, the scientific and reporting software communities, method citations, and the NETS funding acknowledgment.
- `Upload_to_GitHub.bat` rebuilds from the current package inventory. It removes retired files from `main` when their contents still match a prior eStudio release manifest. Independent files and maintainer changes to retired files are preserved. It publishes the new installer ZIP and checksum as the latest release.
- Run the single v337 installer for Windows or macOS. It sets up eStudio, checks the analysis tools and managed SciLink, and opens the desktop app, local browser, and SciLink Streamlit when setup succeeds.

## GitHub homepage

GitHub renders the README on the repository front page. The styled edition is available in the ZIP at `docs/homepage.html`. To publish that same edition as a website, open [repository Pages settings](https://github.com/venky-pnnl/FaradaiQ_eStudio/settings/pages) and choose **Deploy from a branch → main → /docs**. The [Pages URL](https://venky-pnnl.github.io/FaradaiQ_eStudio/) opens the styled homepage once GitHub finishes deploying it.

See the [workflow guide](docs/workflows.md#install-and-start) and [verification notes](QA_v337.md).
