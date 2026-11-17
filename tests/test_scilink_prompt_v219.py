from pathlib import Path

from faradaiq.scilink_prompt_v219 import (
    build_open_scilink_prompt,
    sanitize_open_prompt,
    write_open_prompt,
)


FORBIDDEN = (
    "Do not call select_agent",
    "run_analysis, or literature tools",
    "unless the exact scientific request explicitly asks",
    "ground this answer",
)


def test_open_scilink_prompt_has_freedom_and_required_scientific_request(tmp_path: Path):
    handoff = tmp_path / "SciLink_handoff"
    bundle = handoff / "FaradaIQ_bundle"
    bundle.mkdir(parents=True)
    h5 = bundle / "sample_faradaiq_bundle.h5"
    js = bundle / "sample_faradaiq_bundle.json"
    h5.write_bytes(b"h5")
    js.write_text("{}", encoding="utf-8")
    prompt = build_open_scilink_prompt(handoff)
    assert "full freedom" in prompt.lower()
    assert "next experiment plan" in prompt.lower()
    assert "scientific justification" in prompt.lower()
    assert "any SciLink capabilities, agents, analysis routes, coding tools, or literature resources" in prompt
    for phrase in FORBIDDEN:
        assert phrase.casefold() not in prompt.casefold()


def test_legacy_restrictions_are_removed_from_refined_text():
    dirty = """Use the data.\nDo not call select_agent, run_analysis, or literature tools unless the exact scientific request explicitly asks for them.\nUse the existing SciLink conversation as context, but ground this answer.\nReturn the next experiment plan and justification.\n"""
    clean = sanitize_open_prompt(dirty)
    assert "Return the next experiment plan and justification." in clean
    for phrase in FORBIDDEN:
        assert phrase.casefold() not in clean.casefold()


def test_prompt_writer_updates_both_prompt_files(tmp_path: Path):
    handoff = tmp_path / "handoff"
    prompt = build_open_scilink_prompt(handoff)
    path = write_open_prompt(handoff, prompt)
    assert path.exists()
    assert (handoff / "scilink_task_prompt.md").read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
