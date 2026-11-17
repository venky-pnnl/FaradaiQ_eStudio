from pathlib import Path


def test_runtime_has_bounded_title_settling_and_no_lifetime_repaint_loop():
    text = Path("faradaiq/runtime_v217.py").read_text(encoding="utf-8")
    assert "_enforce_v217_title" not in text
    assert "self.after(750" not in text
    assert "for delay in (40, 160)" in text
    assert "_v218_title_guard" in text
    assert "def guarded_title" in text


def test_parser_monitor_does_not_mirror_each_tick_into_general_status():
    text = Path("faradaiq_studio_core.py").read_text(encoding="utf-8")
    marker = "Do not mirror every 900 ms parser update into the legacy general"
    assert marker in text


def test_cache_progress_uses_dedicated_var_and_terminal_general_updates_only():
    text = Path("faradaiq/post_export_cache_v216.py").read_text(encoding="utf-8")
    assert "Only publish terminal states there" in text
    assert 'VERSION = "v273"' in text


def test_help_includes_stable_status_and_open_scilink_policy():
    import faradaiq_studio_core as core
    names = [str(item[0]) for item in core._V207_HELP_TABS]
    assert "v232 stable status, themed editors, and background progress" in names
    assert "SciLink open scientific workflow" in names
