"""The headline deliverable: the whole range as one calendar heatmap.

Design notes, because they were deliberate:

* Availability is a **status** encoding, not a sequential one — a date is
  good/warning/critical/neutral, not "more or less" of something. So it uses the
  fixed status palette, and every cell carries a **glyph as well as a colour**,
  which is what makes it readable in greyscale, under colour-vision deficiency,
  and in forced-colors mode.
* Empty and unread days are deliberately *recessive*. Over a 330-day range the
  interesting cells are a handful, and they should be the only things that pull
  the eye.
* "Nothing found" and "could not read" are different colours and different
  glyphs, and the source-coverage panel says which engines were trustworthy.
  Conflating them is the failure mode that makes a scanner actively harmful.
* Dark mode is declared under both the OS media query and the explicit theme
  attribute, so a viewer's toggle wins either way.
"""

from __future__ import annotations

import calendar
import datetime as dt
import html
import json
from typing import Optional

from ..diffing import ChangeKind, Diff
from ..models import ScanResult, Status
from ..risk import summary_rows
from ..scanner import coverage_report
from .common import ORDER, STATUS_STYLE, DayView, build_day_views, month_blocks, status_counts

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
  :root{
    color-scheme: light;
    --surface-1:#fcfcfb; --plane:#f9f9f7;
    --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#898781;
    --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10);
    --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
    --empty-bg:#f2f1ed; --empty-ink:#898781;
  }
  @media (prefers-color-scheme: dark){
    :root:not([data-theme="light"]){
      color-scheme: dark;
      --surface-1:#1a1a19; --plane:#0d0d0d;
      --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781;
      --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
      --empty-bg:#232322; --empty-ink:#898781;
    }
  }
  :root[data-theme="dark"]{
    color-scheme: dark;
    --surface-1:#1a1a19; --plane:#0d0d0d;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
    --empty-bg:#232322; --empty-ink:#898781;
  }
  *{box-sizing:border-box}
  body{
    margin:0; padding:24px 16px 64px;
    background:var(--plane); color:var(--text-primary);
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
    font-size:15px; line-height:1.5;
  }
  .wrap{max-width:1180px; margin:0 auto}
  h1{font-size:22px; margin:0 0 4px; letter-spacing:-0.01em}
  .sub{color:var(--text-secondary); font-size:14px; margin:0 0 20px}
  .card{
    background:var(--surface-1); border:1px solid var(--border);
    border-radius:10px; padding:18px; margin-bottom:18px;
  }
  h2{font-size:15px; margin:0 0 12px; font-weight:600}
  .tiles{display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px}
  .tile{
    flex:1 1 150px; background:var(--surface-1); border:1px solid var(--border);
    border-radius:10px; padding:14px 16px;
  }
  .tile .v{font-size:28px; font-weight:650; letter-spacing:-0.02em; line-height:1.1}
  .tile .k{font-size:12px; color:var(--text-secondary); margin-top:2px}
  .tile.hero .v{color:var(--good)}
  .controls{display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:16px}
  .controls label{font-size:13px; color:var(--text-secondary)}
  button,select{
    font:inherit; font-size:13px; padding:6px 10px; border-radius:7px;
    border:1px solid var(--baseline); background:var(--surface-1);
    color:var(--text-primary); cursor:pointer;
  }
  button[aria-pressed="true"]{border-color:var(--good); font-weight:600}
  .months{display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:18px}
  @media (max-width:420px){
    body{padding:16px 16px 48px}
    .months{grid-template-columns:1fr}
    .tile{flex:1 1 100%}
  }
  .month h3{
    font-size:13px; font-weight:600; margin:0 0 6px; color:var(--text-secondary);
    letter-spacing:0.02em;
  }
  table.cal{border-collapse:separate; border-spacing:2px; width:100%}
  table.cal th{
    font-size:10px; font-weight:500; color:var(--muted); padding:0 0 2px;
    text-transform:uppercase; letter-spacing:0.04em;
  }
  td.cell{
    width:30px; height:32px; text-align:center; vertical-align:middle;
    border-radius:4px; padding:3px 0 2px; font-variant-numeric:tabular-nums;
    background:var(--empty-bg); color:var(--empty-ink); position:relative;
  }
  td.cell.pad{background:transparent}
  td.cell .g{display:block; font-size:8px; line-height:9px; opacity:.9}
  td.cell .d{display:block; font-size:12px; line-height:15px}
  /* An empty day is just its number, vertically centred in the cell. */
  td.cell .d:only-child{line-height:27px}
  td.good{background:var(--good); color:#fff; font-weight:600}
  td.warning{background:var(--warning); color:#0b0b0b; font-weight:600}
  td.critical{background:var(--critical); color:#fff}
  td.serious{background:var(--serious); color:#0b0b0b}
  td.unknown{background:var(--empty-bg); color:var(--empty-ink)}
  td.cell:focus{outline:2px solid var(--text-primary); outline-offset:1px}
  body.only-hits td.cell:not(.good):not(.warning){opacity:.25}
  .legend{display:flex; flex-wrap:wrap; gap:14px; align-items:center; font-size:13px}
  .legend .item{display:flex; gap:6px; align-items:center}
  .swatch{
    width:16px; height:16px; border-radius:4px; display:inline-flex;
    align-items:center; justify-content:center; font-size:10px;
    border:1px solid var(--border);
  }
  .scroll-x{overflow-x:auto; -webkit-overflow-scrolling:touch}
  table.data{border-collapse:collapse; width:100%; font-size:13px; min-width:520px}
  table.data th,table.data td{
    text-align:left; padding:6px 8px; border-bottom:1px solid var(--grid);
    font-variant-numeric:tabular-nums;
  }
  table.data th{color:var(--text-secondary); font-weight:600}
  .pill{
    display:inline-block; padding:1px 7px; border-radius:999px; font-size:11px;
    border:1px solid var(--border);
  }
  .pill.good{background:var(--good); color:#fff}
  .pill.warning{background:var(--warning); color:#0b0b0b}
  .pill.critical{background:var(--critical); color:#fff}
  .warn{
    border-left:3px solid var(--warning); padding:10px 14px; margin:10px 0;
    background:var(--plane); border-radius:0 8px 8px 0; font-size:13px;
  }
  .warn.bad{border-left-color:var(--critical)}
  details summary{cursor:pointer; font-weight:600; font-size:14px}
  #tip{
    position:fixed; pointer-events:none; opacity:0; transition:opacity .08s;
    background:var(--text-primary); color:var(--surface-1);
    padding:7px 10px; border-radius:7px; font-size:12px; max-width:320px;
    z-index:50; line-height:1.4;
  }
  .foot{color:var(--muted); font-size:12px; margin-top:24px}
  @media (forced-colors: active){
    td.cell{forced-color-adjust:none; border:1px solid CanvasText}
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>{{TITLE}}</h1>
  <p class="sub">{{SUBTITLE}}</p>

  <div class="tiles">{{TILES}}</div>

  {{WARNINGS}}

  <div class="card">
    <h2>Availability calendar</h2>
    <div class="controls">
      <button id="toggle-hits" aria-pressed="false">Dim everything except hits</button>
      <button id="toggle-theme">Toggle dark mode</button>
      <label>Each cell is one date. Hover or focus a cell for the detail.</label>
    </div>
    <div class="months">{{MONTHS}}</div>
  </div>

  <div class="card">
    <h2>Legend</h2>
    <div class="legend">{{LEGEND}}</div>
  </div>

  <div class="card">
    <h2>Source coverage &amp; bot-detection risk</h2>
    <p class="sub" style="margin:0 0 10px">How much of the range each engine could
      actually read this run. An engine that was blocked is telling you
      &ldquo;not seen&rdquo;, never &ldquo;not available&rdquo;.</p>
    {{COVERAGE}}
    <details style="margin-top:14px">
      <summary>Per-site automation &amp; risk notes</summary>
      {{RISK}}
    </details>
  </div>

  {{CHANGES}}

  <div class="card">
    <details>
      <summary>Table view (every date, for screen readers and copy-paste)</summary>
      {{TABLE}}
    </details>
  </div>

  <p class="foot">{{FOOTER}}</p>
</div>
<div id="tip" role="status"></div>
<script>
const tip = document.getElementById('tip');
function showTip(el, e){
  const t = el.getAttribute('data-tip'); if(!t) return;
  tip.textContent = t; tip.style.opacity = '1';
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  const r = el.getBoundingClientRect();
  let x = (e && e.clientX !== undefined ? e.clientX : r.left + r.width/2) + pad;
  let y = (e && e.clientY !== undefined ? e.clientY : r.top) - h - pad/2;
  if (x + w > innerWidth - 8) x = innerWidth - w - 8;
  if (y < 8) y = (r.bottom + pad/2);
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}
function hideTip(){ tip.style.opacity = '0'; }
for (const el of document.querySelectorAll('td.cell[data-tip]')){
  el.addEventListener('mousemove', e => showTip(el, e));
  el.addEventListener('mouseleave', hideTip);
  el.addEventListener('focus', () => showTip(el, null));
  el.addEventListener('blur', hideTip);
}
const hits = document.getElementById('toggle-hits');
hits.addEventListener('click', () => {
  const on = document.body.classList.toggle('only-hits');
  hits.setAttribute('aria-pressed', String(on));
});
document.getElementById('toggle-theme').addEventListener('click', () => {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light'
    : cur === 'light' ? 'dark'
    : (matchMedia('(prefers-color-scheme: dark)').matches ? 'light' : 'dark');
  document.documentElement.setAttribute('data-theme', next);
});
window.QSUITE_DATA = {{DATA}};
</script>
</body>
</html>
"""


def render_html(result: ScanResult, diff: Optional[Diff] = None) -> str:
    views = build_day_views(result)
    counts = status_counts(views)
    cov = coverage_report(result)
    total_days = (result.end - result.start).days + 1

    title = (f"Qsuite award availability — {result.route} "
             f"{result.cabin.value.title()}")
    subtitle = (
        f"{result.start:%d %b %Y} to {result.end:%d %b %Y} "
        f"({total_days} days) · scanned {result.started_at:%d %b %Y %H:%M UTC} · "
        f"{len(result.runs)} engine(s), {result.total_requests} upstream request(s)"
    )

    return (_TEMPLATE
            .replace("{{TITLE}}", html.escape(title))
            .replace("{{SUBTITLE}}", html.escape(subtitle))
            .replace("{{TILES}}", _tiles(result, views, counts, total_days))
            .replace("{{WARNINGS}}", _warnings(cov, result))
            .replace("{{MONTHS}}", _months(result, views))
            .replace("{{LEGEND}}", _legend())
            .replace("{{COVERAGE}}", _coverage_table(cov))
            .replace("{{RISK}}", _risk_table())
            .replace("{{CHANGES}}", _changes(diff))
            .replace("{{TABLE}}", _data_table(views))
            .replace("{{FOOTER}}", html.escape(_footer(result)))
            .replace("{{DATA}}", json.dumps(_compact(views))))


def _tiles(result: ScanResult, views: dict[dt.date, DayView],
           counts: dict[Status, int], total_days: int) -> str:
    qsuite_days = sum(1 for v in views.values()
                      if v.status is Status.AVAILABLE and v.qsuite)
    unread = counts[Status.BLOCKED] + counts[Status.ERROR] + counts[Status.UNKNOWN]
    cheapest = min((v.best_miles for v in views.values()
                    if v.best_miles is not None), default=None)
    tiles = [
        ("hero", counts[Status.AVAILABLE], "dates with saver business space"),
        ("", qsuite_days, "of those confirmed on Qsuite metal"),
        ("", f"{cheapest:,}" if cheapest else "—", "lowest points seen"),
        ("", result.total_requests, f"requests for {total_days} days"),
        ("", unread, "dates no engine could read"),
    ]
    return "".join(
        f'<div class="tile {cls}"><div class="v">{html.escape(str(v))}</div>'
        f'<div class="k">{html.escape(k)}</div></div>'
        for cls, v, k in tiles
    )


def _warnings(cov: dict[str, dict], result: ScanResult) -> str:
    out: list[str] = []
    bad = [n for n, c in cov.items() if not c["trustworthy"]]
    if bad:
        out.append(
            '<div class="warn bad"><strong>Incomplete coverage.</strong> '
            + html.escape(", ".join(sorted(bad)))
            + " could not read the whole range this run (blocked, errored, or "
              "returned nothing for some dates). Empty cells attributable only to "
              "those engines mean &ldquo;not seen&rdquo;, not &ldquo;not available&rdquo;."
              "</div>")
    if "qatar" not in {r.provider for r in result.runs}:
        out.append(
            '<div class="warn"><strong>Qatar\'s own engine was not queried.</strong> '
            "These results come from partner engines, whose view of Qatar inventory "
            "can lag or differ. Confirm any date you intend to book on "
            "qatarairways.com before committing.</div>")
    return "".join(out)


def _months(result: ScanResult, views: dict[dt.date, DayView]) -> str:
    out: list[str] = []
    for year, month in month_blocks(result.start, result.end):
        rows: list[str] = []
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
            cells: list[str] = []
            for d in week:
                v = views.get(d)
                if d.month != month or v is None:
                    cells.append('<td class="cell pad"></td>')
                    continue
                st = STATUS_STYLE[v.status]
                role = st["role"] if st["role"] in ("good", "warning", "critical",
                                                    "serious") else "unknown"
                glyph = "" if v.status is Status.NONE else \
                    f'<span class="g">{st["glyph"]}</span>'
                cells.append(
                    f'<td class="cell {role}" tabindex="0" '
                    f'data-date="{d.isoformat()}" '
                    f'data-tip="{html.escape(v.tooltip(), quote=True)}">'
                    f'{glyph}<span class="d">{d.day}</span></td>'
                )
            rows.append("<tr>" + "".join(cells) + "</tr>")
        out.append(
            f'<div class="month"><h3>{calendar.month_name[month]} {year}</h3>'
            '<table class="cal"><thead><tr>'
            + "".join(f"<th>{d}</th>" for d in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"))
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        )
    return "".join(out)


def _legend() -> str:
    items = []
    for s in ORDER:
        st = STATUS_STYLE[s]
        role = st["role"] if st["role"] in ("good", "warning", "critical", "serious") else ""
        style = f' style="background:var(--{role});color:#fff"' if role else ""
        items.append(
            f'<span class="item"><span class="swatch"{style}>{st["glyph"]}</span>'
            f'{html.escape(st["label"])}</span>')
    return "".join(items)


def _coverage_table(cov: dict[str, dict]) -> str:
    rows = []
    for name, c in sorted(cov.items()):
        pill = ("good", "trustworthy") if c["trustworthy"] else ("critical", "partial")
        rows.append(
            f"<tr><td>{html.escape(name)}</td>"
            f'<td><span class="pill {pill[0]}">{pill[1]}</span></td>'
            f"<td>{c['coverage_pct']}%</td><td>{c['requests']}</td>"
            f"<td>{c['duration_s']}s</td><td>{c['blocked']}</td>"
            f"<td>{c['errored']}</td><td>{c['unknown']}</td></tr>")
    return ('<div class="scroll-x"><table class="data"><thead><tr><th>Engine</th><th>Verdict</th>'
            "<th>Range read</th><th>Requests</th><th>Time</th><th>Blocked</th>"
            "<th>Errors</th><th>Unread</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def _risk_table() -> str:
    rows = []
    for r in summary_rows():
        rows.append(
            f"<tr><td>{html.escape(r['label'])}</td>"
            f"<td>{html.escape(r['calendar'])}</td>"
            f"<td>{r['requests_for_330d']}</td>"
            f"<td>{html.escape(r['block_risk'])}</td>"
            f"<td>{html.escape(r['captcha_risk'])}</td>"
            f"<td>{html.escape(r['account_risk'])}</td>"
            f"<td>{'yes' if r['needs_auth'] else 'no'}</td>"
            f"<td>{html.escape(r['automatable'])}</td></tr>")
    return ('<div class="scroll-x"><table class="data" style="margin-top:10px"><thead><tr><th>Site</th>'
            "<th>Calendar view</th><th>Requests / 330d</th><th>Block risk</th>"
            "<th>CAPTCHA risk</th><th>Account risk</th><th>Login needed</th>"
            "<th>Automation verdict</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def _changes(diff: Optional[Diff]) -> str:
    if diff is None:
        return ""
    alertable = diff.alertable
    if not alertable:
        return ('<div class="card"><h2>Changes since the last scan</h2>'
                '<p class="sub" style="margin:0">Nothing new opened up.</p></div>')
    rows = []
    for c in alertable:
        tag = "NEW" if c.kind is ChangeKind.NEWLY_OPENED else "FIRST SEEN"
        rows.append(
            f'<tr><td><span class="pill good">{tag}</span></td>'
            f"<td>{c.date:%a %d %b %Y}</td><td>{html.escape(c.provider)}</td>"
            f"<td>{c.seats or '—'}</td>"
            f"<td>{f'{c.miles:,}' if c.miles else '—'}</td>"
            f"<td>{'confirmed' if c.qsuite else 'unconfirmed'}</td></tr>")
    return ('<div class="card"><h2>Changes since the last scan</h2>'
            f'<p class="sub" style="margin:0 0 10px">{html.escape(diff.summary())}</p>'
            '<div class="scroll-x"><table class="data"><thead><tr><th></th><th>Date</th><th>Engine</th>'
            "<th>Seats</th><th>Points</th><th>Qsuite</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div></div>")


def _data_table(views: dict[dt.date, DayView]) -> str:
    rows = []
    for d, v in sorted(views.items()):
        rows.append(
            f"<tr><td>{d.isoformat()}</td>"
            f"<td>{html.escape(STATUS_STYLE[v.status]['label'])}</td>"
            f"<td>{html.escape(', '.join(v.providers_available) or '—')}</td>"
            f"<td>{v.best_seats or '—'}</td>"
            f"<td>{f'{v.best_miles:,}' if v.best_miles else '—'}</td>"
            f"<td>{'yes' if v.qsuite else 'no'}</td></tr>")
    return ('<div class="scroll-x"><table class="data"><thead><tr><th>Date</th><th>Verdict</th>'
            "<th>Engines</th><th>Seats</th><th>Points</th><th>Qsuite</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _compact(views: dict[dt.date, DayView]) -> list[dict]:
    return [{"d": d.isoformat(), "s": v.status.value, "p": v.providers_available,
             "m": v.best_miles, "n": v.best_seats, "q": v.qsuite}
            for d, v in sorted(views.items())]


def _footer(result: ScanResult) -> str:
    return ("Availability is a snapshot and moves minute to minute; always confirm on "
            "the booking engine before making plans. Qsuite verdicts are inferred from "
            "aircraft type and can change with an equipment swap. "
            f"Scan id {result.scan_id}.")
