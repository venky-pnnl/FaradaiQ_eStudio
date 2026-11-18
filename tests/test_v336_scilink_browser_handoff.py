import json

import h5py
import pytest

from faradaiq.scilink_browser_handoff import (
    DEFAULT_SCILINK_URL,
    normalize_scilink_url,
    streamlit_handoff_files,
    streamlit_handoff_text,
)
from faradaiq.scilink_bundle import make_scilink_ready_bundle


@pytest.mark.parametrize(
    ("entered", "expected"),
    [("", DEFAULT_SCILINK_URL), ("localhost:8503", "http://localhost:8503"),
     ("http://[::1]:8513/scilink/", "http://[::1]:8513/scilink/")],
)
def test_accepts_local_streamlit_addresses(entered, expected):
    assert normalize_scilink_url(entered) == expected


@pytest.mark.parametrize("url", [
    "https://example.com", "http://localhost@host.test", "http://localhost.evil.test",
    "http://127.0.0.1:0", "http://127.0.0.1:70000", "file:///tmp",
    "http://localhost/?data=local", "http://localhost\\@host.test", "http://local\nhost",
])
def test_rejects_nonlocal_ambiguous_or_invalid_addresses(url):
    with pytest.raises(ValueError, match="local SciLink"):
        normalize_scilink_url(url)


def test_bundle_handoff_lists_supported_uploads_and_original_data(tmp_path):
    source = tmp_path / "EIS" / "Test EIS.csv"
    source.parent.mkdir()
    raw = b"frequency_hz,zreal_ohm,zimag_ohm\n1000,5.1,-1.2\n500,5.8,-2.3\n250,6.6,-3.4\n"
    source.write_bytes(raw)

    receipt = make_scilink_ready_bundle(tmp_path, metadata={"sample": {"material": "NiCo"}, "session_id": "S12"})
    files = streamlit_handoff_files(receipt)
    assert files == streamlit_handoff_files(files["Data"])
    assert set(files) == {"Data", "Metadata", "Skill", "Request"}
    assert {file.parent for file in files.values()} == {files["Data"].parent}
    metadata = json.loads(files["Metadata"].read_text(encoding="utf-8"))
    assert metadata["experiment_type"] == "Curve Analysis"
    assert metadata["experiment"]["technique"] == "EIS"
    assert metadata["sample"]["material"] == "NiCo"
    assert metadata["faradaiq_bundle"]["session_id"] == "S12"
    assert metadata["faradaiq_bundle"]["datasets"][0]["units"]["frequency_hz"] == "Hz"
    with h5py.File(files["Data"]) as h5:
        assert metadata["faradaiq_bundle"]["datasets"][0]["path"] in h5
        assert next(iter(h5["source_documents"].values()))[()].tobytes() == raw
    assert source.read_bytes() == raw
    assert json.loads(files["Request"].read_text(encoding="utf-8"))["approved"] is False
    text = streamlit_handoff_text(receipt)
    assert "Data file(s)" in text and "Metadata (optional)" in text and "/tables/" in text
    assert str(files["Data"]) in text and str(files["Metadata"]) in text
    assert str(files["Request"]) not in text


def test_older_bundle_uses_its_manifest_and_ignores_unrelated_hdf5(tmp_path):
    path = tmp_path / "experiment_faradaiq_bundle.h5"
    path.write_bytes(b"legacy data")
    path.with_suffix(".json").write_text('{"scilink_ready": {}}', encoding="utf-8")
    (tmp_path / "electrochemistry_skill.md").touch()
    (tmp_path / "scilink_request.json").write_text(json.dumps({
        "data_paths": [path.name], "metadata_paths": [path.with_suffix(".json").name],
    }), encoding="utf-8")
    assert streamlit_handoff_files(path)["Metadata"] == path.with_suffix(".json")
    unrelated = tmp_path / "unrelated.h5"
    unrelated.touch()
    with pytest.raises(ValueError, match="SciLink-ready bundle"):
        streamlit_handoff_files(unrelated)
