import json
import re

from qsuite.diffing import diff_scans
from qsuite.models import Status
from qsuite.report import render_html, render_json, render_terminal
from qsuite.report.common import build_day_views


def test_day_views_merge_providers_best_first(make, scan_result):
    d = scan_result.start
    scan_result.cells = [make(d, Status.NONE, "ba"),
                         make(d, Status.AVAILABLE, "alaska", miles=70000, seats=2)]
    v = build_day_views(scan_result)[d]
    assert v.status is Status.AVAILABLE
    assert v.providers_available == ["alaska"]
    assert v.best_miles == 70000
    assert v.per_provider == {"ba": Status.NONE, "alaska": Status.AVAILABLE}


def test_day_view_takes_the_best_price_across_providers(make, scan_result):
    d = scan_result.start
    scan_result.cells = [make(d, Status.AVAILABLE, "ba", miles=95000, seats=1),
                         make(d, Status.AVAILABLE, "alaska", miles=70000, seats=4)]
    v = build_day_views(scan_result)[d]
    assert (v.best_miles, v.best_seats) == (70000, 4)


def test_terminal_render_marks_available_dates(scan_result):
    out = render_terminal(scan_result, color=False)
    assert "November 2026" in out
    assert "●" in out
    assert "Legend:" in out
    assert "date(s) with saver business space" in out


def test_terminal_render_has_no_ansi_when_color_off(scan_result):
    assert "\033[" not in render_terminal(scan_result, color=False)


def test_terminal_render_emits_ansi_when_color_on(scan_result):
    assert "\033[" in render_terminal(scan_result, color=True)


def test_html_is_wellformed_and_self_contained(scan_result):
    out = render_html(scan_result)
    assert out.startswith("<!DOCTYPE html>")
    assert out.rstrip().endswith("</html>")
    assert out.count("<table") == out.count("</table>")
    # No unresolved template placeholders.
    assert not re.search(r"\{\{[A-Z_]+\}\}", out)
    # Everything inline: no external fetches.
    assert "<script src=" not in out
    assert "<link rel=\"stylesheet\"" not in out


def test_html_declares_dark_mode_under_both_scopes(scan_result):
    out = render_html(scan_result)
    assert "prefers-color-scheme: dark" in out
    assert '[data-theme="dark"]' in out


def test_html_cells_carry_a_glyph_so_colour_is_never_alone(scan_result):
    out = render_html(scan_result)
    assert 'class="g">●' in out
    assert "Legend" in out


def test_html_includes_an_accessible_table_view(scan_result):
    out = render_html(scan_result)
    assert "Table view" in out
    assert 'class="data"' in out


def test_html_warns_when_qatars_own_engine_was_not_queried(scan_result):
    from qsuite.models import ProviderRun, WindowKind
    scan_result.runs = [ProviderRun(provider="ba", window_kind=WindowKind.MONTH)]
    assert "Qatar&#x27;s own engine was not queried" in render_html(scan_result) \
        or "Qatar's own engine was not queried" in render_html(scan_result)


def test_json_export_is_parseable_and_complete(scan_result):
    payload = json.loads(render_json(scan_result))
    assert payload["route"] == "YUL-SIN"
    assert len(payload["calendar"]) == 30
    assert set(payload["summary"]["counts"]) >= {"available", "none"}
    assert payload["summary"]["available_dates"]


def test_json_includes_changes_when_a_diff_is_supplied(scan_result, make, route, cabin):
    d = scan_result.start
    prev = [make(d, Status.NONE)]
    diff = diff_scans(scan_result.cells, prev, route, cabin)
    payload = json.loads(render_json(scan_result, diff))
    assert "changes" in payload
    assert payload["changes"]
