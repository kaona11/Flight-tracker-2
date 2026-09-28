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


def test_no_color_is_respected_even_when_stdout_is_a_tty(scan_result, monkeypatch):
    """--no-color must win over auto-detection, for terminals that mangle ANSI."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    assert "\033[" not in render_terminal(scan_result, color=False)


def test_no_color_env_var_is_respected(scan_result, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    assert "\033[" not in render_terminal(scan_result, color=None)


def test_color_is_off_when_output_is_redirected(scan_result, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    assert "\033[" not in render_terminal(scan_result, color=None)


def test_color_suppressed_when_the_console_cannot_render_ansi(scan_result, monkeypatch):
    """A Windows console without VT processing must get plain text.

    Emitting ANSI there prints literal escape sequences that also break the
    calendar's column alignment -- strictly worse than no colour at all.
    """
    monkeypatch.setattr("qsuite.report.terminal._enable_windows_ansi", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    assert "\033[" not in render_terminal(scan_result, color=None)
    assert "\033[" not in render_terminal(scan_result, color=True)


def test_ansi_is_emitted_when_the_console_can_render_it(scan_result, monkeypatch):
    monkeypatch.setattr("qsuite.report.terminal._enable_windows_ansi", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    assert "\033[" in render_terminal(scan_result, color=None)


def test_ansi_enable_is_a_noop_off_windows(monkeypatch):
    from qsuite.report.terminal import _enable_windows_ansi
    monkeypatch.setattr("os.name", "posix")
    assert _enable_windows_ansi() is True


def test_month_columns_adapt_to_terminal_width():
    from qsuite.report.terminal import _months_that_fit
    assert _months_that_fit(40) == 1      # narrow: one month per row
    assert _months_that_fit(60) == 2
    assert _months_that_fit(90) == 3
    assert _months_that_fit(300) == 4     # capped, so lines stay scannable


def test_narrow_terminal_lines_do_not_exceed_the_width(scan_result):
    out = render_terminal(scan_result, color=False, months_per_row=1)
    assert max(len(line) for line in out.splitlines() if "·" not in line) < 80
