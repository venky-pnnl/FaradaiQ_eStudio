from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _build_job(tmp_path: Path) -> Path:
    job = tmp_path / "GenericSeries_A1_job"
    data = job / "data"
    skills = job / "skills"
    data.mkdir(parents=True)
    skills.mkdir()
    (data / "0001_CV_point_008_scanrate_25mVs_cycle_01.csv").write_text(
        "potential_V,current_A\n-0.5,-1e-8\n0.0,0\n0.5,1e-8\n0.0,0\n-0.5,-1e-8\n", encoding="utf-8"
    )
    (data / "scilink_bundle_context.json").write_text(json.dumps({
        "series_metadata": {"variable": "scan_rate_mV_s", "unit": "mV/s", "values": {"0001_CV_point_008_scanrate_25mVs_cycle_01.csv": 25.0}}
    }), encoding="utf-8")
    (skills / "electrochemistry_skill.md").write_text("## Overview\nInterpret electrochemistry.\n", encoding="utf-8")
    (job / "analysis_prompt.md").write_text("Analyze the CV series.", encoding="utf-8")
    routing = {
        "interpretation_only": True, "run_analysis_allowed": False, "select_agent_allowed": False,
        "techniques_detected": ["CV"], "routes": [{"route": "deterministic_electrochemical_cv_analysis"}],
    }
    (job / "electrochemistry_routing_plan.json").write_text(json.dumps(routing), encoding="utf-8")
    deterministic = {
        "route": "technique_specific_deterministic_electrochemistry",
        "run_analysis_allowed": False, "select_agent_allowed": False, "interpretation_only": True,
        "analysis_record_count": 3,
        "analyses": [{"technique": "CV", "identity": {"cycle": 1, "sweep": 1}, "metrics": {"current_range_A": 2e-8}}],
    }
    (job / "deterministic_electrochemistry_analysis.json").write_text(json.dumps(deterministic), encoding="utf-8")
    series = {"series_metadata": {"variable": "scan_rate_mV_s", "unit": "mV/s", "values": {"0001_CV_point_008_scanrate_25mVs_cycle_01.csv": 25.0}}}
    (job / "series_metadata.json").write_text(json.dumps(series), encoding="utf-8")
    return job


def test_interpretation_only_dry_run_has_no_analysis_tools(tmp_path):
    job = _build_job(tmp_path)
    script = Path(__file__).resolve().parents[1] / "faradaiq_scilink_interpretation_only.py"
    cmd = [
        sys.executable, str(script), "--job-dir", str(job), "--skills", str(job / "skills/electrochemistry_skill.md"),
        "--data", str(job / "data"), "--metadata", str(job / "data/scilink_bundle_context.json"),
        "--routing-plan", str(job / "electrochemistry_routing_plan.json"),
        "--deterministic", str(job / "deterministic_electrochemistry_analysis.json"),
        "--series-metadata", str(job / "series_metadata.json"), "--prompt-file", str(job / "analysis_prompt.md"),
        "--model", "test-model", "--base-url", "http://localhost:11434/v1", "--session-dir", str(job / "session"),
        "--dry-run", "--non-interactive",
    ]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "SciLink mode: interpretation-only" in cp.stdout
    assert "Available analysis tools: none" in cp.stdout
    assert "Available tools: read-only file access only" in cp.stdout
    assert "Generic CurveFittingAgent required" not in cp.stdout
    guard = json.loads((job / "scilink_interpretation_only_guard.json").read_text(encoding="utf-8"))
    assert guard["available_tools"] == ["list_job_files", "read_job_file"]
    assert guard["analysis_tool_calls_allowed"] is False
    assert guard["analysis_orchestrator_instantiated"] is False
    assert "select_agent" in guard["blocked_tools"] and "run_analysis" in guard["blocked_tools"]
    raw = json.loads((job / "raw_electrochemistry_context.json").read_text(encoding="utf-8"))
    assert raw["exact_paths_resolved_strict"] is True
    assert all("GenericSeries_A1" in p for p in raw["exact_file_paths"])
    assert not any("GenericSeries_AA1" in p for p in raw["exact_file_paths"])
    assert (job / "session/results/scilink_interpretation_only/analysis_report.html").is_file()


def test_empty_deterministic_file_is_blocked_without_fallback(tmp_path):
    job = _build_job(tmp_path)
    payload = json.loads((job / "deterministic_electrochemistry_analysis.json").read_text())
    payload["analyses"] = []
    (job / "deterministic_electrochemistry_analysis.json").write_text(json.dumps(payload))
    script = Path(__file__).resolve().parents[1] / "faradaiq_scilink_interpretation_only.py"
    cmd = [sys.executable, str(script), "--job-dir", str(job), "--skills", str(job / "skills/electrochemistry_skill.md"),
           "--data", str(job / "data"), "--metadata", str(job / "data/scilink_bundle_context.json"),
           "--routing-plan", str(job / "electrochemistry_routing_plan.json"), "--deterministic", str(job / "deterministic_electrochemistry_analysis.json"),
           "--series-metadata", str(job / "series_metadata.json"), "--prompt-file", str(job / "analysis_prompt.md"),
           "--model", "test", "--base-url", "http://localhost:11434/v1", "--session-dir", str(job / "session"), "--dry-run", "--non-interactive"]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert cp.returncode == 7
    assert "Generic fallback is prohibited" in cp.stdout
