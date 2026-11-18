import importlib
import sys
import traceback

REQUIRED = [
    ("tkinter", None, "Tkinter GUI support"),
    ("numpy", "numpy==1.26.4", "numeric arrays; pinned below NumPy 2"),
    ("pandas", "pandas==2.2.2", "tables / CSV export"),
    ("scipy", "scipy==1.13.1", "scientific fitting and pySECCM tools"),
    ("matplotlib", "matplotlib==3.8.4", "plots"),
    ("PIL", "pillow==10.3.0", "image/photo/GIF support"),
    ("cvxpy", "cvxpy==1.6.7", "pyDRTtools optimization"),
    ("PyQt5", "PyQt5==5.15.10", "pyDRTtools GUI backend"),
    ("altair", "altair>=6.0,<7", "interactive linked 2D visualization"),
    ("plotly", "plotly>=6.0,<7", "interactive 3D and hyperspectral visualization"),
    ("vl_convert", "vl-convert-python>=1.9,<2", "self-contained Altair HTML export"),
    ("impedance", "impedance>=1.7,<2", "linear Kramers-Kronig consistency checks"),
    ("docx", "python-docx>=1.1,<2", "DOCX scientific report export"),
    ("tkinterdnd2", "tkinterdnd2>=0.4,<1", "native file/folder drag and drop for export jobs"),
    ("requests", "requests>=2.32,<3", "OpenAI-compatible text-only LLM transactions"),
    ("edison_client", "edison-client>=0.15.0,<0.16", "Edison Scientific reviewed-file transactions"),
    ("lmfit", "lmfit>=1.3,<2", "SciLink CurveFittingAgent model execution"),
    ("sklearn", "scikit-learn>=1.4,<2", "SciLink curve-series regression and fitting support"),
    ("fastmcp", "fastmcp>=3.4.2,<4", "package-local LabRIC MCP stdio proxy and connection test"),
    ("mcp", "mcp>=1.12,<2", "Model Context Protocol runtime for LabRIC integration"),
]

OPTIONAL = []

print("Python executable:", sys.executable)
print("Python version:", sys.version.replace("\n", " "))

missing_nonpip = []
broken = []

for module_name, pip_spec, note in REQUIRED:
    try:
        mod = importlib.import_module(module_name)
        if module_name == "numpy":
            ver = getattr(mod, "__version__", "unknown")
            print("numpy version:", ver)
            try:
                major = int(str(ver).split(".")[0])
            except Exception:
                major = 0
            if major >= 2:
                broken.append((module_name, f"NumPy {ver} detected. This launcher uses numpy==1.26.4 to stay compatible with Anaconda SciPy/Pandas/Matplotlib wheels."))
    except Exception:
        tb = traceback.format_exc()
        if pip_spec:
            broken.append((module_name, tb))
        else:
            missing_nonpip.append(f"{module_name}: {note}\n{tb}")

if missing_nonpip:
    print("\nMissing non-pip components:")
    for item in missing_nonpip:
        print("  -", item)

if broken:
    print("\nBroken or binary-incompatible imports detected:")
    for mod, err in broken:
        print("---", mod, "---")
        print(err)
    repair_specs = ["numpy==1.26.4", "pandas==2.2.2", "scipy==1.13.1", "matplotlib==3.8.4", "contourpy==1.2.0", "pillow==10.3.0", "cvxpy==1.6.7", "PyQt5==5.15.10", "altair>=6.0,<7", "plotly>=6.0,<7", "vl-convert-python>=1.9,<2", "impedance>=1.7,<2", "python-docx>=1.1,<2", "tkinterdnd2>=0.4,<1", "requests>=2.32,<3", "edison-client>=0.15.0,<0.16", "lmfit>=1.3,<2", "scikit-learn>=1.4,<2", "fastmcp>=3.4.2,<4", "mcp>=1.12,<2"]
    print("\nREPAIR_PIP_PACKAGES=" + " ".join(repair_specs))
    sys.exit(4)

if missing_nonpip:
    sys.exit(3)

print("\nOptional integrations:")
print("  [OPTION]  DearEIS: the main installer checks and repairs its separate .deareis_venv.")
print("  [OPTION]  Official pyDRTtools GUI: the main installer checks and repairs it in the private eStudio runtime.")
print("             Rerunning setup retries either integration. Bundled DRT remains available.")

print("\nAll required FaradaIQ Studio / pySECCM / DRT / scientific intelligence dependencies imported successfully.")
sys.exit(0)
