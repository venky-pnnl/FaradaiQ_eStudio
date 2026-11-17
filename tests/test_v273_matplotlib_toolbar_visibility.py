from pathlib import Path


def test_v273_release_installs_global_matplotlib_toolbar_patch():
    root = Path(__file__).resolve().parents[1]
    text = (root / "faradaiq" / "v273_release_integrity.py").read_text(encoding="utf-8")
    assert "_install_matplotlib_toolbar_visibility(core)" in text
    assert "NavigationToolbar2Tk.__init__ = patched_init" in text
    for label in ("Home", "Back", "Forward", "Pan", "Box zoom", "Save figure"):
        assert f'(\"{label}\",' in text
    assert "#f8fafc" in text
    assert "#0f172a" in text
