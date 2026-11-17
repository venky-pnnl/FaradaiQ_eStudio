from pathlib import Path

def test_core_paths_import():
    from faradaiq.runtime.paths import package_root, bundled_path
    assert package_root().exists()
    assert bundled_path("faradaiq_studio_core.py").exists()

def test_drt_command_builds():
    from faradaiq.drt.runner import build_headless_drt_command
    cmd = build_headless_drt_command(Path("input"), run_types=["simple"], plot_types=["eis"], export_types=["drt"])
    assert "headless_pyDRTtools.py" in " ".join(cmd)
    assert "--engine" in cmd

def test_parser_paths():
    from faradaiq.pyseccm.runner import parser_script
    assert parser_script("cv").name == "parse_CV.py"
    assert parser_script("eis").name == "parse_EIS.py"
