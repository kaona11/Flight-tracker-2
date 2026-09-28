# qsuite-scanner

Scan Qatar Airways **Qsuite business-class award availability** across an entire
date range in one run, and get back a single calendar heatmap instead of hours
of clicking through one date at a time.

Built for YUL–SIN (Montreal → Singapore via Doha), works for any route.

```bash
qsuite scan --route YUL-SIN --days 330 --html out/calendar.html
```

```
       October 2026                 November 2026                 December 2026
Mon Tue Wed Thu Fri Sat Sun   Mon Tue Wed Thu Fri Sat Sun   Mon Tue Wed Thu Fri Sat Sun
              1 ◐ 2   3 ● 4                             1         1   2   3   4   5   6
  5 ● 6 ● 7   8 ● 9  10 ●11     2   3   4   5 ● 6   7   8     7   8   9  10  11 ●12  13
 12  13  14 ●15  16 ●17 ●18   ● 9  10  11  12 ●13  14  15    14  15  16  17  18  19  20

Legend:  ● Saver space   ◐ Waitlist   · Nothing found   ✕ Blocked by site   ? Not seen
```

## Why it's fast

Most award tools ask one date at a time. This one asks each site for its
**month-at-a-glance calendar** where one exists, so a 330-day scan is about
twelve requests per engine instead of three hundred and thirty:

```
$ qsuite plan --days 330 --provider alaska,ba,qantas,qatar,aa

engine   window   requests  concurrency   delay  est. time
----------------------------------------------------------
alaska   month          12            3    2.0s       0.1m
ba       month          12            2    3.0s       0.3m
qantas   month          12            2    2.5s       0.2m
qatar    strip          48            1    8.0s       6.4m
aa       month          12            1    6.0s       1.2m
----------------------------------------------------------
total                   96

Providers run concurrently, so wall clock is roughly the slowest engine: ~6.4 min.
Day-by-day on the same engines would be 1650 requests (17x more).
```

Where a site has no calendar view — Qatar's own engine only offers a short
flexible-date strip — the windows are run **concurrently** across a pool of
headless browser contexts, each with its own cookie jar, rather than
sequentially.

## Install

```bash
pip install -e .                      # core: the HTTP engines
pip install -e ".[browser]"           # + Qatar's own site (needs a real browser)
playwright install chromium           # only if you installed [browser]
```

**On Windows**, use the `py` launcher, which works whether or not `pip` and the
Scripts directory made it onto PATH:

```powershell
py -m pip install -e .
py -m qsuite.cli --help     # equivalent to `qsuite --help`
```

`py -m qsuite.cli` is the reliable fallback anywhere the `qsuite` command itself
is not found — same program, no PATH involved.

Try it with no live requests at all:

```bash
qsuite scan --days 90 --provider fixture --html out/demo.html
```

## The five engines

| Engine | Calendar | Requests / 330d | Login | Account risk |
|---|---|---|---|---|
| **Alaska** Mileage Plan | month | ~12 | no | **low** |
| **BA** Reward Flight Finder | month | ~12 | yes | medium |
| **Qantas** Classic Rewards | month | ~12 | yes | low |
| **AA** AAdvantage | month | ~12 | no | medium |
| **Qatar** Privilege Club | strip only | ~48 | yes | **high** |

`qsuite risk` prints the full breakdown — protection vendor, automation verdict,
recommended pacing, and the specific caveat for each site. The reasoning is in
[docs/SOURCES.md](docs/SOURCES.md).

**The default sweep is `alaska,ba,qantas`.** Qatar's own engine is deliberately
opt-in: it has no month view and the highest account-flag risk in the set. Use it
to *confirm* dates the month-view engines flagged, not to sweep:

```bash
qsuite scan --provider alaska,ba,qantas --days 330 --html out/calendar.html
qsuite scan --provider qatar --start 2027-02-10 --end 2027-02-20
```

## Output

`--html` writes a self-contained calendar heatmap: every date in the range as
one cell, coloured by verdict and carrying a glyph so it reads in greyscale and
under colour-vision deficiency, with hover detail per date, a source-coverage
panel, the per-site risk table, a changes-since-last-scan list, and a plain
table view. No external assets, no network calls — just open the file.

`--json` writes the full result including per-provider cells and the merged
per-date view. The terminal calendar prints by default.

### "Nothing found" and "couldn't look" are different colours

This is the distinction the whole tool rests on. An engine that was blocked,
errored, or simply didn't mention a date is recorded as **unknown**, never as
"no availability" — and the report tells you which engines read the whole range
and which didn't. Conflating the two is what makes an award scanner actively
harmful: you stop checking a date that was available all along.

Three places this shows up concretely:

- BA's empty days are annotated *"not seen here"* until you confirm BA can
  actually see Qatar space and set `coverage_verified: true`.
- AA's silent bot-block returns a well-formed empty month, so a month with no
  availability *and* no pricing at all is downgraded to `unknown`.
- Qantas **refuses to run logged out**, because Classic Reward space is
  invisible without a login and it would otherwise report 330 false negatives.

`qsuite scan` exits `0` when every engine read the whole range and `2` when one
didn't, so a cron job can tell "found nothing" from "couldn't look".

## Recurring scans and alerts

Every scan is stored in SQLite, and each run is diffed against the last. You are
alerted **only on what's new**:

- `newly opened` — the date was definitively empty last time, now it has space.
- `first seen` — no usable prior reading (never scanned, or last run was blocked
  on it). Real news, but weaker; it may have been open all along.
- `closed` / `improved` — shown in the report, never alerted.

A date already alerted on is not announced again, so a daily run doesn't repeat
itself when a seat stays open.

```bash
qsuite watch --days 330 --interval 24h --html out/calendar.html
```

Or use the scheduled GitHub Action in
[`.github/workflows/scan.yml`](.github/workflows/scan.yml), which caches the
database between runs (without it, every run looks like a first run and
re-announces the whole calendar).

Channels: console, Slack/Discord/raw webhook, and SMTP email. Filter the noise
with `alerts.qsuite_only`, `alerts.min_seats`, `alerts.max_miles`.

## Qsuite detection

Award engines almost never say "Qsuite" — equipment is the only signal most
expose. `qsuite/aircraft.py` maps equipment to the hard product and infers
**per leg**: a 777-300ER to Doha followed by a 787-8 onward is reported as
*not* Qsuite, because that's how it feels in the seat.

Unrecognised or unreported equipment yields `None`, never `False`. The tool
declines to guess rather than claim a legacy cabin it never saw. `Qsuite
confirmed` means "the equipment shown carries Qsuite" — sub-fleets vary,
retrofits are ongoing, and swaps happen after booking.

## Commands

```
qsuite scan       one full pass: scan, render, diff, alert
qsuite watch      the same on a loop, alerting only on new dates
qsuite plan       request budget per engine — sends nothing
qsuite risk       per-site bot-detection and calendar-view verdicts
qsuite history    what previous scans found (--prune N to trim)
```

Useful flags: `--provider`, `--start/--end/--days`, `--html`, `--json`,
`--capture DIR` (save raw responses for fixing parsers), `--no-headless`,
`--proxy`, `--no-save`, `-v`/`-vv`.

## Configuration

Copy [`qsuite.example.yml`](qsuite.example.yml) to `qsuite.yml`. Credentials read
from the environment (`QSUITE_BA_USERNAME`, `QSUITE_QANTAS_TOKEN`, …) or via
`env:VARNAME` indirection, so nothing secret goes in the committed file.

## When a site changes

These endpoints are undocumented and unsupported; they will break. The parsers
are shape-driven rather than path-driven — they walk the payload looking for
"a node with a date and an availability signal" — which survives most wrapper
renames. When that isn't enough, a parser **raises loudly** rather than
returning an empty month, because silence is the dangerous failure.

```bash
qsuite scan --days 30 --provider ba --capture captures/ -vv
```

Then fix the parser against the saved response; `tests/test_parsers.py` runs
against payloads with no HTTP involved.

## Terms of service

Every site here prohibits automated access in some form. This is built for
personal, low-volume use, and its defaults reflect that: conservative pacing,
low concurrency, per-site rate limits and jittered scheduling. That doesn't make
it permitted. The real risk isn't an IP block — it's a frequent-flyer account
flagged or closed, which can take its balance with it. Don't raise the
concurrency, don't remove the delays. Details in
[docs/SOURCES.md](docs/SOURCES.md).

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Availability moves minute to minute. Always confirm on the booking engine before
making plans.
