# FaradaIQ Studio v320 installer QA

v320 uses one complete package: `FaradaIQ_Studio_v320_package.zip`.

The extracted package verifies `PACKAGE_MANIFEST_SHA256_v320.txt` with `_verify_package_manifest_v320.ps1` before environment bootstrap and installs side by side under `C:\FaradaIQ\Studio\V320`.
