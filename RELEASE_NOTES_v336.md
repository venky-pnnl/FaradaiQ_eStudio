# FaradaIQ eStudio v336

The Windows and macOS installers use a single setup flow. They provision the eStudio environment, check and install DearEIS and the official pyDRTtools GUI as needed, and check for a managed SciLink update before launching the apps. On a rerun, setup checks the integration status again. eStudio remains available if a third-party integration fails, with a diagnostic log for the failed step.

**Upload_to_GitHub.bat** now fetches GitHub's existing `main` and builds the source commit on top of it from the verified ZIP payload. Publishing from an extracted ZIP preserves existing history and remote-only files. A concurrent edit to a release file stops the upload and names the overlapping file. The release ZIP and checksum are uploaded after the GitHub branch accepts the source commit.

See the [workflow guide](docs/workflows.md#install-and-start) and [verification notes](QA_v336.md).
