# Source analysis: which engines can realistically be bulk-scanned

This is the research behind the provider list. It answers two questions per
site — *does it have a calendar view we can drive in one request*, and *what
will it do to us if we try* — because those two together decide whether a
330-day scan costs twelve requests or three hundred and thirty.

> **Verify before you trust.** These verdicts reflect how the engines looked
> when this was written. Airlines change bot-protection vendors and redesign
> booking flows without notice, and none of the endpoints here are documented or
> supported. If a provider starts returning `blocked`, or its parser starts
> raising "the schema has probably changed", the entry below is out of date —
> re-run with `--capture` and fix the parser against the saved response.

## Summary

| Engine | Calendar view | Requests / 330d | Block risk | CAPTCHA | Account risk | Login |
|---|---|---|---|---|---|---|
| **Alaska** Mileage Plan | native month | ~12 | medium | medium | **low** | no |
| **BA** Reward Flight Finder | native month | ~12 | medium | low | medium | yes |
| **Qantas** Classic Rewards | native month | ~12 | medium | low | low | yes |
| **AA** AAdvantage | native month | ~12 | high | high | medium | no |
| **Qatar** Privilege Club | strip only (±3 days) | ~48 | **severe** | high | **high** | yes |

Day-by-day on all five would be 1,650 requests. Calendar-first is 96 — and if
you drop Qatar to a confirmation pass over a handful of flagged dates, ~40.

## The routing this is scanning

Qatar has no nonstop YUL–SIN. The itinerary is YUL–DOH–SIN, and both legs need
to be Qsuite for the trip to feel like a Qsuite trip. That is why the tool
infers Qsuite per *leg* and downgrades the whole itinerary if any leg is a
legacy cabin (`qsuite/aircraft.py`) — a 777-300ER to Doha followed by a 787-8
onward is not what you were shopping for.

Equipment is a heuristic and the scanner says so. Most engines never report it
at all, sub-fleets vary, retrofits are ongoing, and a swap after booking is
always possible. `Qsuite confirmed` means "the equipment we were shown carries
Qsuite", not a guarantee.

---

## Alaska Airlines — best first target

**Calendar:** a flexible-date award calendar is a first-class part of the UI and
returns a month at a time.

**Why it leads the default sweep:** it is the only engine in the set that shows
award calendars *without a login*. No login means no frequent-flyer account to
flag, which removes the only risk here that can cost you a miles balance
outright. It is also the fastest to pace politely.

**Caveats:** Alaska's partner award display can lag Qatar's live inventory, and
it prices some partner space on its own chart. Treat an Alaska hit as "go look",
not "go book".

## British Airways Reward Flight Finder — the densest calendar

**Calendar:** the Reward Flight Finder is *built around* a month grid. This is
the single best calendar-per-request ratio available.

**Caveats, in order of how badly they bite:**

1. **Partner coverage.** BA has restricted which partners RFF exposes at various
   times. An engine that cannot see Qatar space at all returns empty months that
   are byte-for-byte identical to genuine unavailability. The provider therefore
   treats unverified BA emptiness as *"not seen here"* and annotates every empty
   cell, until you set `providers.ba.coverage_verified: true` — which you should
   only do after confirming with your own eyes that QR space shows up.
2. **Session binding.** The month endpoint expects the cookie jar a real visit
   leaves behind, so the provider warms a session once and reuses it. This is
   also why its concurrency is capped at 2: a dozen parallel calls on one
   freshly-minted session is precisely the pattern Akamai scores as automated.
3. **Endpoint drift.** The request shape changes between BA releases, so the URL
   template lives in config rather than in code.

## Qantas Classic Rewards — the reliable middle

**Calendar:** the Classic Rewards flow has a month display returning per-day
lowest-points cells. Qantas surfaces Qatar space as a oneworld partner.

**Caveat:** Classic Reward inventory is **invisible when logged out**. Scanning
anonymously would return a clean, confident, entirely wrong "nothing available"
for all 330 days. The provider refuses to run without a token rather than do
that — override with `providers.qantas.allow_anonymous: true` only if you have
consciously accepted it.

## American AAdvantage — has a calendar, don't trust it alone

**Calendar:** yes, a month per request.

**Why it is not in the default sweep:** AA's failure mode is *silent*. When it
decides you are automated it frequently returns a well-formed, entirely empty
result rather than an error — so a blocked scan and an empty month look
identical. The provider guards against this: a month that comes back with no
availability *and* no pricing of any kind is recorded as `unknown`, never as
`none`. A degraded engine must not get to vote "nothing here" against engines
that can actually see.

Also confirm AA can see Qatar space at all before reading anything into its
results; partner visibility has come and gone over the years.

## Qatar Airways — authoritative, and the one that bites

**Calendar:** none worth the name. The flexible-date display is a short strip
around the searched date, not a month grid. At a 7-day strip the range costs ~48
requests even when everything works.

**Protection:** Akamai Bot Manager with device and TLS fingerprinting plus a
sensor payload plain HTTP cannot reproduce. This is the one provider that drives
a real browser, at concurrency 1, with an 8-second gap between requests and
deliberate `slow_mo`.

**The risk that actually matters:** Qatar has form for locking Privilege Club
accounts it believes are being scraped, and a locked account can mean a frozen
Avios balance. That is a materially worse outcome than an IP block. Prefer
running logged out where the flow permits it, or against an account you would
not mind losing.

**So use it as a confirmation engine.** Sweep the range with the month-view
sites; point Qatar at the handful of dates they flagged:

```bash
qsuite scan --provider alaska,ba,qantas --days 330 --html out/calendar.html
qsuite scan --provider qatar --start 2027-02-10 --end 2027-02-20
```

That is an order of magnitude fewer requests, and correspondingly less risk,
than scanning 330 days on Qatar directly.

---

## Terms of service, plainly

Every site here prohibits automated access in its terms, in some form. This tool
is built for personal, low-volume use — checking award space you intend to book
yourself — and its defaults reflect that: conservative pacing, low concurrency,
per-site rate limits, jittered scheduling, and a design that prefers twelve
month-requests over three hundred day-requests.

None of that makes automated access permitted. What you are risking, concretely:

- **IP blocks** — annoying, usually temporary.
- **Rate limiting and CAPTCHAs** — the scanner backs off and reports `blocked`
  rather than hammering through.
- **Account flags or closure** — the real one. A frequent-flyer account closed
  for scraping can take its balance with it.

Do not raise the concurrency limits, do not remove the delays, and do not point
this at a route you have no intention of flying. If you want this running daily
and unattended, Alaska (no login) is by far the safest engine to leave on.
