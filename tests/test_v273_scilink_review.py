from __future__ import annotations
import inspect
from faradaiq import ui_tabs_v226
from faradaiq import v262_technique_scilink as review


def test_review_opens_before_recursive_candidate_discovery():
    source = inspect.getsource(review.show_analysis_review)
    assert 'candidates = _review_candidates(source, [])' in source
    assert 'old.collect_share_candidates(source, cfg)' not in source
    assert 'win = tk.Toplevel(parent)' in source
    assert 'win.transient(parent)' in source
    assert 'win.attributes("-topmost", True)' in source
    assert 'win.focus_force()' in source


def test_ca_cp_button_name_and_template_are_unambiguous():
    source = inspect.getsource(ui_tabs_v226._build_analyze_panel)
    assert 'Ask SciLink: CA/CP analysis…' in source
    assert '"CA charge and transient metrics"' in source


def test_review_starts_with_exactly_two_required_generated_items():
    source = inspect.getsource(review.show_analysis_review)
    assert 'candidates = _review_candidates(source, [])' in source
    assert 'Add supporting files…' in source
    assert 'win._faradaiq_scilink_file_rows = file_rows' in source
