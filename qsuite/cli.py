"""Command line entry point.

    qsuite scan     one full pass over the range, render, diff, alert
    qsuite watch    the same, on a loop, alerting only on what's new
    qsuite risk     per-site bot-detection and calendar-view verdicts
    qsuite plan     dry-run: how many requests will this cost, per engine?
    qsuite history  what previous scans found
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import random
import shlex
import sys
from pathlib import Path
from typing import Optional

from . import risk as riskmod
from .alerts import build_channels, dispatch, filter_changes
from .config import Config
from .diffing import OPEN, Diff, diff_scans, suppress_already_alerted
from .models import Cabin, Route, ScanResult
from .providers import DEFAULT_PROVIDERS, names as provider_names
from .ranges import resolve_range
from .report import render_html, render_json, render_terminal
from .scanner import Scanner, coverage_report
from .store import Store

log = logging.getLogger("qsuite")


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------

def _add_global_flags(target: argparse.ArgumentParser) -> None:
    """Flags valid either side of the subcommand: `-v scan` and `scan -v`."""
    target.add_argument("--config", help="path to a YAML config file",
                        default=argparse.SUPPRESS)
    target.add_argument("-v", "--verbose", action="count",
                        default=argparse.SUPPRESS,
                        help="-v for progress, -vv for debug")
    target.add_argument("-q", "--quiet", action="store_true",
                        default=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qsuite",
        description="Scan Qatar Airways Qsuite business award availability across a "
                    "whole date range in one run, calendar-view first.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  qsuite scan --route YUL-SIN --days 330
  qsuite scan --days 90 --provider alaska,ba --html out/cal.html
  qsuite scan --days 330 --provider fixture        # offline demo, no live requests
  qsuite plan --days 330                           # request budget, no requests sent
  qsuite watch --days 330 --interval 24h           # daily, alert only on new dates
  qsuite risk                                      # per-site automation verdicts
""")
    # Global flags live on a parent parser as well as the top level, so
    # `qsuite -v scan` and `qsuite scan -v` both work. Being strict about flag
    # position is a poor way to greet someone at 1am.
    #
    # The defaults are SUPPRESS on purpose: with an ordinary default, the
    # subparser re-applies it *after* the top-level parse and silently wipes
    # out `qsuite -v scan`. SUPPRESS leaves the attribute absent instead, so
    # whichever parser actually saw the flag wins, and _defaults() fills the
    # rest in afterwards.
    # Declared once and added to each parser through the public API. An earlier
    # version copied argparse's private `_actions` between parsers; that broke
    # on newer Pythons, and this does the same job with nothing private.
    common = argparse.ArgumentParser(add_help=False)
    for target in (p, common):
        _add_global_flags(target)
    sub = p.add_subparsers(dest="command", required=True)

    def add_scan_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--route", help="e.g. YUL-SIN")
        sp.add_argument("--cabin", choices=[c.value for c in Cabin])
        sp.add_argument("--start", help="YYYY-MM-DD")
        sp.add_argument("--end", help="YYYY-MM-DD")
        sp.add_argument("--days", type=int,
                        help="scan this many days starting tomorrow (default 330)")
        sp.add_argument("--provider", help=f"comma-separated: {', '.join(provider_names())} "
                                           f"(default: {','.join(DEFAULT_PROVIDERS)})")
        sp.add_argument("--concurrency", type=int,
                        help="max providers running at once (default 6)")
        sp.add_argument("--db", help="sqlite path (default data/qsuite.sqlite3)")
        sp.add_argument("--capture", metavar="DIR",
                        help="save raw upstream responses here, for fixing parsers")
        sp.add_argument("--no-headless", action="store_true",
                        help="show the browser (browser providers only)")
        sp.add_argument("--proxy", help="proxy URL for every request")

    scan = sub.add_parser("scan", help="run one full scan", parents=[common])
    add_scan_args(scan)
    scan.add_argument("--html", metavar="PATH", help="write the calendar heatmap here")
    scan.add_argument("--json", metavar="PATH", help="write the full result here")
    scan.add_argument("--no-terminal", action="store_true",
                      help="skip the terminal calendar")
    scan.add_argument("--no-color", action="store_true",
                      help="plain text, no ANSI colour (also honours NO_COLOR)")
    scan.add_argument("--no-alerts", action="store_true",
                      help="compute the diff but send nothing")
    scan.add_argument("--no-save", action="store_true",
                      help="do not persist this scan (so it will not be diffed against)")
    scan.add_argument("--alert-on-first-run", action="store_true",
                      help="alert on everything found when there is no previous scan "
                           "(by default the first run is treated as a baseline)")

    watch = sub.add_parser("watch", help="re-scan on a schedule, alerting only on new dates", parents=[common])
    add_scan_args(watch)
    watch.add_argument("--interval", default="24h",
                       help="e.g. 6h, 90m, 24h (default 24h)")
    watch.add_argument("--jitter", type=float, default=0.1,
                       help="randomise each interval by +/- this fraction (default 0.1)")
    watch.add_argument("--max-runs", type=int, help="stop after this many scans")
    watch.add_argument("--html", metavar="PATH", help="rewrite the heatmap after each scan")
    watch.add_argument("--no-color", action="store_true",
                       help="plain text, no ANSI colour (also honours NO_COLOR)")

    riskp = sub.add_parser("risk", help="per-site bot-detection and calendar-view verdicts", parents=[common])
    riskp.add_argument("--json", action="store_true")

    plan = sub.add_parser("plan", help="show the request budget without sending anything", parents=[common])
    add_scan_args(plan)

    cal = sub.add_parser("calibrate", parents=[common],
                         help="turn a request copied from your browser into working config")
    cal.add_argument("--provider", required=True,
                     help=f"which engine this request is for: {', '.join(provider_names())}")
    cal.add_argument("--route", help="the route you searched, e.g. YUL-SIN")
    cal.add_argument("--date", help="the date you searched, YYYY-MM-DD")
    cal.add_argument("--url", metavar="URL",
                     help="the request URL on its own — the simplest input, and "
                          "the one that needs no clipboard or cURL parsing. Read "
                          "it off the DevTools Headers tab ('Request URL').")
    cal.add_argument("--curl-file", metavar="PATH",
                     help="file holding the copied cURL (default: read stdin)")
    cal.add_argument("--write", metavar="PATH", nargs="?", const="qsuite.yml",
                     help="append the config to this file (default qsuite.yml)")

    hist = sub.add_parser("history", help="summarise previous scans", parents=[common])
    hist.add_argument("--route")
    hist.add_argument("--cabin", choices=[c.value for c in Cabin])
    hist.add_argument("--db")
    hist.add_argument("--limit", type=int, default=10)
    hist.add_argument("--prune", type=int, metavar="KEEP",
                      help="delete all but the newest KEEP scans per route")
    return p


def _fill_global_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Supply the defaults SUPPRESS deliberately left out (see build_parser)."""
    for dest, default in (("config", None), ("verbose", 0), ("quiet", False)):
        if not hasattr(args, dest):
            setattr(args, dest, default)
    return args


def apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    if getattr(args, "route", None):
        cfg.route = Route.parse(args.route)
    if getattr(args, "cabin", None):
        cfg.cabin = Cabin(args.cabin)
    for key in ("start", "end", "days"):
        v = getattr(args, key, None)
        if v:
            setattr(cfg, key, v)
    if getattr(args, "provider", None):
        cfg.providers = [p.strip() for p in args.provider.split(",") if p.strip()]
    if getattr(args, "concurrency", None):
        cfg.global_concurrency = args.concurrency
    if getattr(args, "db", None):
        cfg.db_path = args.db
    if getattr(args, "capture", None):
        cfg.capture_dir = args.capture
    if getattr(args, "no_headless", False):
        cfg.headless = False
    if getattr(args, "proxy", None):
        cfg.proxy = {"server": args.proxy}
    return cfg


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

async def cmd_scan(cfg: Config, args: argparse.Namespace) -> int:
    store = None if getattr(args, "no_save", False) else Store(cfg.db_path)
    try:
        async with Scanner(cfg) as scanner:
            result = await scanner.scan()

        diff = _diff_against_previous(store, result, cfg)
        _emit(result, diff, args, cfg)

        if store is not None:
            store.save(result)

        if not getattr(args, "no_alerts", False) and diff is not None:
            _alert(cfg, result, diff, store, args)

        return _exit_code(result)
    finally:
        if store is not None:
            store.close()


async def cmd_watch(cfg: Config, args: argparse.Namespace) -> int:
    interval = parse_interval(args.interval)
    runs = 0
    log.info("watching %s %s every %s (Ctrl-C to stop)",
             cfg.route, cfg.cabin.value, args.interval)
    while True:
        runs += 1
        started = dt.datetime.now(dt.timezone.utc)
        log.info("scan %d starting at %s", runs, started.isoformat(timespec="seconds"))
        try:
            await cmd_scan(cfg, args)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - a watcher must survive a bad scan
            log.error("scan %d failed: %r — continuing", runs, exc)

        if args.max_runs and runs >= args.max_runs:
            log.info("reached --max-runs %d, stopping", args.max_runs)
            return 0

        # Jitter the wait so a daily run never lands on the same second, which
        # is both politer to the sites and harder to fingerprint.
        spread = interval * max(0.0, args.jitter)
        wait = max(60.0, interval + random.uniform(-spread, spread))
        nxt = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=wait)
        log.info("next scan at %s (in %.0f min)",
                 nxt.isoformat(timespec="seconds"), wait / 60)
        try:
            await asyncio.sleep(wait)
        except (KeyboardInterrupt, asyncio.CancelledError):
            return 0


def cmd_risk(args: argparse.Namespace) -> int:
    if args.json:
        import json
        print(json.dumps([p.to_dict() for p in riskmod.PROFILES.values()], indent=2))
        return 0

    print("Per-site calendar support and bot-detection risk\n"
          "===============================================\n")
    print("Prioritised by what actually matters: does it have a month view, and "
          "how likely is it\nto push back. 330-day scan assumed.\n")
    hdr = f"{'site':<9}{'calendar':<16}{'reqs':>5}  {'block':<8}{'captcha':<9}{'account':<9}login"
    print(hdr)
    print("-" * len(hdr))
    for r in riskmod.summary_rows():
        print(f"{r['provider']:<9}{r['calendar']:<16}{r['requests_for_330d']:>5}  "
              f"{r['block_risk']:<8}{r['captcha_risk']:<9}{r['account_risk']:<9}"
              f"{'yes' if r['needs_auth'] else 'no'}")
    print()
    for r in riskmod.summary_rows():
        print(f"{r['label']}")
        print(_labelled("protection", r["protection"]))
        print(_labelled("automation", r["automatable"]))
        print(_labelled("caveat", r["notes"]))
        print(f"  pacing     : concurrency {r['recommended_concurrency']}, "
              f"{r['recommended_delay_s']}s between requests")
        print()
    print("Rule of thumb: sweep the whole range with the month-view engines, then "
          "confirm the\nhandful of dates they flag on Qatar's own site. That keeps "
          "the request count and the\naccount risk an order of magnitude lower than "
          "scanning everything everywhere.")
    return 0


async def cmd_plan(cfg: Config, args: argparse.Namespace) -> int:
    from .providers import REGISTRY
    from .ranges import plan as plan_windows

    start, end = resolve_range(cfg.start, cfg.end, cfg.days)
    days = (end - start).days + 1
    print(f"Request budget for {cfg.route} {cfg.cabin.value}, "
          f"{start:%d %b %Y} to {end:%d %b %Y} ({days} days)\n")
    hdr = f"{'engine':<9}{'window':<8}{'requests':>9}{'concurrency':>13}{'delay':>8}{'est. time':>11}"
    print(hdr)
    print("-" * len(hdr))

    total_requests = 0
    slowest = 0.0
    for name in cfg.providers:
        cls = REGISTRY.get(name)
        if cls is None:
            print(f"{name:<9}unknown provider")
            continue
        strip = int(cfg.options_for(name).get("strip_width", cls.strip_width))
        windows = plan_windows(start, end, cls.window_kind, strip)
        profile = riskmod.get(name)
        conc = profile.recommended_concurrency if profile else cls.default_concurrency
        delay = profile.recommended_delay_s if profile else cls.default_delay_s
        est = (len(windows) / max(1, conc)) * max(delay, 1.0)
        total_requests += len(windows)
        slowest = max(slowest, est)
        print(f"{name:<9}{cls.window_kind.value:<8}{len(windows):>9}{conc:>13}"
              f"{delay:>7.1f}s{est / 60:>10.1f}m")

    naive = days * len(cfg.providers)
    print("-" * len(hdr))
    print(f"{'total':<9}{'':<8}{total_requests:>9}")
    print(f"\nProviders run concurrently, so wall clock is roughly the slowest engine: "
          f"~{slowest / 60:.1f} min.")
    if naive:
        print(f"Day-by-day on the same engines would be {naive} requests "
              f"({naive / max(1, total_requests):.0f}x more).")
    return 0


def cmd_calibrate(cfg: Config, args: argparse.Namespace) -> int:
    from .calibrate import calibrate

    if args.provider not in provider_names():
        print(f"error: unknown provider {args.provider!r}; known: "
              f"{', '.join(provider_names())}", file=sys.stderr)
        return 1

    if args.url:
        # A bare URL is all the templating actually needs; the cURL form only
        # ever added headers. Quoting a URL on a command line is far less
        # fiddly than getting a multi-line command through a shell.
        text = f"curl {shlex.quote(args.url)}"
    elif args.curl_file:
        text = Path(args.curl_file).read_text()
    else:
        # Only prompt when someone is actually there to read it. When stdin is
        # a pipe -- the recommended path, `Get-Clipboard | qsuite calibrate` --
        # a prompt is just noise in the output.
        if sys.stdin.isatty():
            print("Paste the copied cURL command, then send end-of-input:\n"
                  "  Windows  Ctrl-Z then Enter\n"
                  "  Mac/Linux  Ctrl-D\n"
                  "\nEasier: pipe the clipboard in instead and skip this prompt --\n"
                  "  Windows    Get-Clipboard | qsuite calibrate --provider ...\n"
                  "  Mac        pbpaste | qsuite calibrate --provider ...\n"
                  "  Linux      xclip -o | qsuite calibrate --provider ...\n"
                  "or save it to a file and pass --curl-file.\n", file=sys.stderr)
        text = sys.stdin.read()

    if not text.strip():
        print("error: no input received. Pipe the clipboard in "
              "(Get-Clipboard | ... on Windows, pbpaste | ... on Mac) or use "
              "--curl-file, which avoids end-of-input handling entirely.",
              file=sys.stderr)
        return 1

    route = Route.parse(args.route) if args.route else cfg.route
    date = dt.date.fromisoformat(args.date) if args.date else None
    if date is None:
        print("warning: no --date given, so only the airport codes will be "
              "templated. Pass the date you searched to generalise this "
              "request across months.", file=sys.stderr)

    try:
        yaml_block, template, notes, dropped = calibrate(
            text, args.provider, route.origin, route.destination, date)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if dropped:
        print(f"Dropped {', '.join(dropped)} from the captured request — these "
              f"are credentials and do not belong in a config file. Set them as "
              f"environment variables if the engine needs them.\n", file=sys.stderr)
    for n in notes:
        print(f"  {n}", file=sys.stderr)
    if not any("->" in n for n in notes):
        print("warning: nothing was templated. Check that --route and --date "
              "match what you actually searched, or this endpoint will query "
              "the same date every time.", file=sys.stderr)
    print(file=sys.stderr)

    if args.write:
        path = Path(args.write)
        existing = path.read_text() if path.exists() else ""
        # Appending a second `providers:` key would make the YAML ambiguous.
        block = yaml_block
        if "providers:" in existing:
            block = "\n".join(line for line in yaml_block.splitlines()[1:])
            print(f"note: {path} already has a `providers:` section — appending "
                  f"the provider entry only. Check the indentation lines up.",
                  file=sys.stderr)
        with path.open("a") as fh:
            fh.write(("\n" if existing and not existing.endswith("\n") else "")
                     + block + "\n")
        print(f"appended to {path}", file=sys.stderr)
    else:
        print(yaml_block)
        print(f"\n# Save that into qsuite.yml, or re-run with --write.\n"
              f"# Then test it:\n"
              f"#   qsuite scan --days 30 --provider {args.provider} "
              f"--capture captures/ -v")
    return 0


def cmd_history(cfg: Config, args: argparse.Namespace) -> int:
    store = Store(args.db or cfg.db_path)
    try:
        if args.prune:
            removed = store.prune(args.prune)
            print(f"pruned {removed} old scan(s)")
            return 0
        route = Route.parse(args.route) if args.route else cfg.route
        cabin = Cabin(args.cabin) if args.cabin else cfg.cabin
        ids = store.scan_ids(route, cabin, limit=args.limit)
        if not ids:
            print(f"no scans recorded for {route} {cabin.value}")
            return 0
        print(f"Scans for {route} {cabin.value} (newest first)\n")
        print(f"{'scan id':<26}{'available':>10}{'waitlist':>10}{'read':>7}{'cells':>7}")
        print("-" * 60)
        for sid in ids:
            cells = store.cells_for_scan(sid)
            avail = len({c.date for c in cells if c.status.value == "available"})
            wl = len({c.date for c in cells if c.status.value == "waitlist"})
            read = len({c.date for c in cells
                        if c.status.value in ("available", "waitlist", "none")})
            print(f"{sid:<26}{avail:>10}{wl:>10}{read:>7}{len(cells):>7}")
        return 0
    finally:
        store.close()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _diff_against_previous(store: Optional[Store], result: ScanResult,
                           cfg: Config) -> Optional[Diff]:
    if store is None:
        return None
    prev_id = store.previous_scan_id(result.route, result.cabin, before=result.scan_id)
    if prev_id is None:
        log.info("no previous scan for %s %s — this run is the baseline",
                 result.route, result.cabin.value)
        return Diff(route=result.route, cabin=result.cabin)
    previous = store.cells_for_scan(prev_id)
    diff = diff_scans(result.cells, previous, result.route, result.cabin)
    log.info("diff against %s: %s", prev_id, diff.summary())
    return diff


def _emit(result: ScanResult, diff: Optional[Diff], args: argparse.Namespace,
          cfg: Config) -> None:
    if not getattr(args, "no_terminal", False):
        color = False if getattr(args, "no_color", False) else None
        print(render_terminal(result, diff, color=color))

    html_path = getattr(args, "html", None)
    if html_path:
        p = Path(html_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_html(result, diff))
        print(f"\ncalendar heatmap → {p}")

    json_path = getattr(args, "json", None)
    if json_path:
        p = Path(json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_json(result, diff))
        print(f"full result → {p}")


def _alert(cfg: Config, result: ScanResult, diff: Diff, store: Optional[Store],
           args: argparse.Namespace) -> None:
    is_baseline = not diff.changes and store is not None and \
        len(store.scan_ids(result.route, result.cabin, limit=2)) <= 1
    if is_baseline:
        if not getattr(args, "alert_on_first_run", False):
            # Alerting on a baseline would dump the entire calendar into your
            # inbox, which is exactly the "re-reading the whole thing" this is
            # meant to replace.
            log.info("baseline run — nothing to compare against, so nothing alerted "
                     "(use --alert-on-first-run to report everything found)")
            return
        diff = _everything_open_as_changes(result)
        log.info("first run with --alert-on-first-run: reporting all %d open date(s)",
                 len(diff.changes))

    sent_before = store.already_alerted(result.route, result.cabin) if store else set()
    pruned = suppress_already_alerted(diff, sent_before)
    changes = filter_changes(pruned, cfg.alerts)
    if not changes:
        log.info("no new dates worth alerting on")
        return

    channels = build_channels(cfg.alerts)
    results = dispatch(channels, result.route, result.cabin, changes)
    if store is not None and any(results.values()):
        store.mark_alerted(result.route, result.cabin,
                           [(c.date, c.provider) for c in changes])
    log.info("alerts: %s", results or "no channels configured")


def _everything_open_as_changes(result: ScanResult) -> Diff:
    """Treat every open date as FIRST_SEEN, for --alert-on-first-run.

    FIRST_SEEN rather than NEWLY_OPENED is the honest label: with no previous
    scan we genuinely cannot say whether a date just opened or has been sitting
    there for weeks.
    """
    from .diffing import Change, ChangeKind
    changes = [
        Change(kind=ChangeKind.FIRST_SEEN, date=c.date, provider=c.provider,
               status=c.status, prev_status=None, miles=c.cheapest_miles,
               seats=c.max_seats, qsuite=c.qsuite_confirmed, note=c.note)
        for c in sorted(result.cells, key=lambda x: (x.date, x.provider))
        if c.status in OPEN
    ]
    return Diff(route=result.route, cabin=result.cabin, changes=changes)


def _exit_code(result: ScanResult) -> int:
    """0 = clean, 2 = ran but at least one engine was blocked or errored.

    A cron job should be able to tell "found nothing" from "could not look".
    """
    cov = coverage_report(result)
    if not cov:
        return 1
    return 0 if all(c["trustworthy"] for c in cov.values()) else 2


def parse_interval(text: str) -> float:
    """Parse ``30s`` / ``90m`` / ``6h`` / ``2d`` into seconds."""
    t = text.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if t and t[-1] in units:
        try:
            return float(t[:-1]) * units[t[-1]]
        except ValueError:
            pass
    try:
        return float(t)
    except ValueError:
        raise ValueError(f"could not parse interval {text!r}; try 6h, 90m or 24h") from None


def _labelled(label: str, text: str, width: int = 90) -> str:
    """One wrapped `  label : text` block, hanging-indented under the label."""
    import textwrap
    prefix = f"  {label:<11}: "
    return textwrap.fill(text, width=width, initial_indent=prefix,
                         subsequent_indent=" " * len(prefix))


def _setup_logging(args: argparse.Namespace) -> None:
    level = logging.WARNING
    if args.verbose == 1:
        level = logging.INFO
    elif args.verbose >= 2:
        level = logging.DEBUG
    if args.quiet:
        level = logging.ERROR
    logging.basicConfig(
        level=level, stream=sys.stderr,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")


def main(argv: Optional[list[str]] = None) -> int:
    args = _fill_global_defaults(build_parser().parse_args(argv))
    _setup_logging(args)

    if args.command == "risk":
        return cmd_risk(args)

    try:
        cfg = apply_overrides(Config.load(args.config), args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        if args.command == "scan":
            return asyncio.run(cmd_scan(cfg, args))
        if args.command == "watch":
            return asyncio.run(cmd_watch(cfg, args))
        if args.command == "plan":
            return asyncio.run(cmd_plan(cfg, args))
        if args.command == "calibrate":
            return cmd_calibrate(cfg, args)
        if args.command == "history":
            return cmd_history(cfg, args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
