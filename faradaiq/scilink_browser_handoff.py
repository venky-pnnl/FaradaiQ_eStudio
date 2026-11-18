"""Prepare file selections for the local SciLink Streamlit application."""

from __future__ import annotations

from ipaddress import ip_address
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


DEFAULT_SCILINK_URL = "http://127.0.0.1:8501"
SCILINK_STREAMLIT_COMMAND = "scilink ui --streamlit"
_SKILL_NAME = "electrochemistry_skill.md"
_REQUEST_NAME = "scilink_request.json"
_METADATA_NAME = "scilink_upload_metadata.json"


def normalize_scilink_url(value: str = "") -> str:
    """Accept an explicit HTTP(S) loopback address, with an optional base path."""
    address = str(value or "").strip() or DEFAULT_SCILINK_URL
    if "://" not in address:
        address = "http://" + address
    error = "Enter a local SciLink HTTP(S) address, such as http://127.0.0.1:8501."
    if any(char.isspace() or ord(char) < 32 or char == "\\" for char in address):
        raise ValueError(error)
    try:
        parsed = urlsplit(address)
        host = parsed.hostname
        if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.port == 0):
            raise ValueError
        if host != "localhost" and not ip_address(host).is_loopback:
            raise ValueError
    except ValueError:
        raise ValueError(error) from None
    return parsed.geturl()


def write_streamlit_metadata(bundle_dir: Path, ready: dict, data_name: str) -> Path:
    """Write the Analyze uploader's metadata without changing the bundle manifest."""
    original = ready["experimental_metadata"]

    def recorded(*values):
        return next((str(value).strip() for value in values
                     if isinstance(value, str) and value.strip()), "")

    original_experiment = original.get("experiment")
    experiment = original_experiment if isinstance(original_experiment, dict) else {}
    original_sample = original.get("sample")
    sample = original_sample if isinstance(original_sample, dict) else {}
    datasets = [
        {field: entry[field] for field in ("path", "shape", "columns", "units", "technique")}
        for entry in ready["datasets"] if entry["path"].startswith("/tables/")
    ]
    techniques = ", ".join(sorted({entry["technique"] for entry in datasets if entry["technique"]}))
    details = recorded(experiment.get("details"), original.get("experimental_description_or_prompt"),
                       original.get("experimental_description"), original.get("description"))
    metadata = {
        "experiment_type": recorded(original.get("experiment_type")) or "Curve Analysis",
        "experiment": {"technique": recorded(experiment.get("technique"), original.get("technique"), techniques)
                                     or "Electrochemistry"},
        "sample": {"material": recorded(sample.get("material"), original.get("sample_material"),
                                        original.get("material"), original_sample) or "Not recorded"},
        "title": ready["sample_name"],
        "faradaiq_bundle": {"schema": ready["schema"], "version": ready["version"],
                            "data_file": data_name, "session_id": ready["session_id"], "datasets": datasets},
    }
    if details:
        metadata["experiment"]["details"] = details
    for field in ("date", "instrument"):
        value = recorded(experiment.get(field), original.get(field))
        if value:
            metadata["experiment"][field] = value
    description = recorded(sample.get("description"))
    if description:
        metadata["sample"]["description"] = description
    path = bundle_dir / _METADATA_NAME
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def streamlit_handoff_files(receipt_or_hdf5) -> dict[str, Path]:
    """Resolve files from a new receipt or a previously created SciLink-ready HDF5."""
    hdf5 = Path(receipt_or_hdf5["h5_path"] if isinstance(receipt_or_hdf5, dict)
                else receipt_or_hdf5).expanduser().resolve()
    request_path = hdf5.parent / _REQUEST_NAME
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Select the HDF5 file from a SciLink-ready bundle folder.") from exc
    if (not hdf5.is_file() or hdf5.suffix.lower() not in {".h5", ".hdf5"}
            or not isinstance(request, dict) or request.get("data_paths") != [hdf5.name]):
        raise ValueError("Select the HDF5 file from a SciLink-ready bundle folder.")

    files = {
        "Data": hdf5,
        "Metadata": hdf5.parent / _METADATA_NAME,
        "Skill": hdf5.parent / _SKILL_NAME,
        "Request": request_path,
    }
    if not files["Metadata"].is_file():
        files["Metadata"] = hdf5.with_suffix(".json")
    for role, path in files.items():
        if not path.is_file() or path.resolve().parent != hdf5.parent:
            raise ValueError(f"The SciLink-ready bundle is missing its {role.lower()} file: {path.name}")
    return files


def streamlit_handoff_text(receipt_or_hdf5) -> str:
    """List the local paths to select in SciLink's documented upload controls."""
    files = streamlit_handoff_files(receipt_or_hdf5)
    return (
        "In SciLink, choose Analyze, then review and select these files.\n\n"
        f"Data file(s):\n{files['Data']}\n\n"
        f"Metadata (optional):\n{files['Metadata']}\n\n"
        f"Skills → Upload Skills (optional):\n{files['Skill']}\n\n"
        "The electrochemical measurements are in /tables/. "
        "Use each dataset's column labels and recorded units. "
        "Review the prompt and selected inputs before running SciLink."
    )


def streamlit_analysis_prompt(receipt_or_hdf5) -> str:
    """Suggest scope using the uploaded file name and its internal dataset paths."""
    files = streamlit_handoff_files(receipt_or_hdf5)
    return (
        f"Review the electrochemical datasets under /tables/ in {files['Data'].name} "
        f"using the uploaded {files['Metadata'].name} and the labels and units recorded "
        "for each dataset. Keep measurements separate by technique and acquisition. "
        "Flag missing units or ambiguous conventions before comparing measurements. "
        "Keep the original values and report any transformations used in the analysis."
    )


def open_local_folder(path) -> None:
    """Open an existing folder on the computer running eStudio."""
    folder = Path(path).expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise NotADirectoryError(str(folder))
    if sys.platform == "win32":
        os.startfile(str(folder))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
