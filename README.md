# Retirement Planner — Secure (Server-Side) Edition

This is a working proof of a real architecture change: the calculation
logic now lives entirely on a Python backend. The HTML/JS the browser
receives contains **zero formulas** — only input collection, display
formatting, and a `fetch()` call. You can verify this yourself:
`grep -n "Math.pow\|excelPV\|growingAnnuity" index.html` returns nothing.

## Files

| File | What it is |
|---|---|
| `engine.py` | The calculation engine — a line-by-line port of the original JS `recalcAll()`, plus a server-side Goal Seek binary search. Pure functions, no web framework code. |
| `app.py` | Flask API server. Two endpoints: `POST /api/calculate` and `POST /api/goal-seek`. |
| `index.html` | The new frontend. Sends inputs, displays results. No logic. |
| `requirements.txt` | Python dependencies for deployment. |
| `DEPLOYMENT.md` | How to actually put this on the internet — Render/Railway/Fly.io, CORS lockdown, HTTPS. |

## Run it locally

```bash
pip install flask
python app.py
# serves on http://127.0.0.1:5001
```

Then open `index.html` in a browser (it's a static file, no build
step). It's pre-configured to call `http://127.0.0.1:5001`.

## Numerical parity — how it was verified

Before trusting this port with real financial numbers, I checked it against
the original JavaScript engine directly, not just spot-checked:

- **5 different scenarios** (default baseline, with active-phase
  contribution, different returns/inflation/life-span, large total corpus,
  and a sequence-of-returns shock) — every summary figure matched to
  sub-rupee precision.
- **The full 100-year illustration array**, all 9 fields per year (opening/
  closing balances, withdrawals, contributions, ROI for both corpuses) —
  909 individual values compared, **zero mismatches**.

This matters more than it might seem: a silent off-by-something in a
financial formula is a much worse outcome than the original IP-exposure
problem this whole rebuild exists to solve.

## What's ported, what's not (be aware of this before relying on it)

**Ported and verified:**
- Plan Basics, Monthly Income Requirements, Investment Planning tables
- Tax Impact (illustrative)
- The 100-year illustration engine + chart
- Depletion detection
- Stress Test (proves the "preview a what-if" pattern works over a
  network — the other what-if features use the same pattern and would
  port the same way)
- **Goal Seek** — all 4 variables (required contribution, required total
  corpus, max sustainable secondary/primary spending), including the
  "structural ceiling" detection for total corpus and every edge-case
  message (already achieved / not achievable). The full ~40-iteration
  binary search runs **server-side in one request** — this is the pattern
  the remaining what-if features should follow. Verified against the
  original JS implementation across all 4 variables and multiple targets;
  the only difference found was an intentional one (a section-number
  reference updated to match this app's own layout).

**Not yet ported:**
- Lumpsum goals (Section 2 in the original) — `purposes` is sent as an
  empty list in this pass
- Sensitivity Ranking, Sequence-of-Returns Risk, Scenario Comparison
- Undo, Save / Load Plan, PDF / Excel export

## Continuing this work

The original app's what-if features (Goal Seek especially) run a
calculation, read the result, and repeat — up to ~40 times in a tight loop
for a binary search — entirely synchronously, because the local JS engine
returns instantly. Over a network, each of those calls has real latency, so
naively porting them means either:

1. **Await each call properly** (correct, but a Goal Seek solve that took
   milliseconds locally might take 1-3 seconds over the network — still
   fine for a "click Solve" interaction, just needs a loading state), or
2. **Reduce the round-trips** — e.g. have the backend accept a batch of
   scenarios in one request, or run the binary search server-side and
   return just the final answer instead of 40 round-trips.

(2) is the better long-term answer and isn't much extra work once the
pattern from Stress Test is in place — it just means each what-if feature
gets its own small endpoint (e.g. `/api/goal-seek`) instead of the frontend
driving the loop itself. Happy to build these next if useful — just say
which one to prioritize.

## Update: Sensitivity Ranking ported (server-side)

All 6 levers tested in isolation, ranked by impact, one request
(`POST /api/sensitivity`) runs all 6 + the baseline server-side. Verified
against the original JS across 2 scenarios (default + healthy/contribution
plan) — every lever's delta matched to sub-rupee precision. Clickable bars
drill into Stress Test or Goal Seek, matching the original app's behavior.

**Still not ported:** lumpsum goals, Sequence-of-Returns Risk, Scenario
Comparison, Undo, Save/Load Plan, PDF/Excel export.

## Update: Sequence-of-Returns Risk ported (server-side)

Three scenarios (baseline, bad-years-at-the-start, same-shortfall-spread-
evenly) computed server-side in one request (`POST /api/sequence-risk`).

**A real precision bug was caught and fixed during parity testing:** the
original JS rounds the "spread evenly" adjusted return to 3 decimal places
before using it (an artifact of writing through a DOM input's `.toFixed(3)`).
My first port kept full floating-point precision instead, which produced
answers off by a few thousand rupees after compounding over 100 years —
small, but not the exact parity this project has held to throughout. Fixed
by replicating that same rounding step; re-verified across 5 scenarios
(including the "reversal" edge case and a 1-year grammar edge case) with
exact character-for-character message matches.

**Still not ported:** lumpsum goals, Scenario Comparison, Undo, Save/Load
Plan, PDF/Excel export.

## Note on local testing files

`index.local-test.html` and `app.local-test.py` are development-only
copies with permissive CORS / localhost API pointed at each other — used
to verify changes locally before they're ported into the real `index.html`
/ `app.py`. **Do not deploy the `.local-test` files** — they're deliberately
insecure (open CORS) and meant only for testing on your own machine
alongside a local `python app.py` run of the *real* backend copy... actually,
run `python app.local-test.py` instead when testing locally, since it's
pre-configured to talk to `index.local-test.html` without needing to edit
either file first.

## Update: Scenario Comparison ported (client-side, no new endpoint needed)

Save up to 3 scenarios locally (localStorage), compare all 10 driver
fields side by side, with rows that actually differ between saved
scenarios highlighted in gold — including derived/knock-on differences,
not just directly-edited fields. Includes the small comparison bar chart
from the original. This feature needed no new backend endpoint — it
reuses whatever `lastResult` the last `/api/calculate` call already
returned, plus the browser's own localStorage.

Verified: save/clear/persistence-across-reload, diff-highlighting
correctness (same fields flagged consistently across compared cards),
and a full regression of every other feature already built.

**Still not ported:** lumpsum goals, Undo, Save/Load Plan, PDF/Excel export.

## Update: Lumpsum Goals, Undo, Save/Load Plan, PDF/Excel export

**Correction to my own earlier status:** Lumpsum Goals (3 editable future-
purpose cards with their own investment sub-tables) turned out to already
be fully built from an earlier pass — I had mistakenly reported it as
"not yet ported" in a previous update. I verified it properly this time:
parity-checked `netCorpusForSelf` against the original JS (exact match)
and confirmed the cards render, edit, and update results correctly.

**Genuinely new this round:**
- **Undo** — wired into Goal Seek's existing Apply flow. Snapshots the
  full plan via `getPlanData()` before applying, restores it exactly via
  the new `applyPlanData()` on click.
- **Save/Load Plan** — download the current plan as JSON, or load one
  back in. Both use the same `applyPlanData()` Undo relies on.
- **PDF export** — uses the browser's native print-to-PDF via `@media
  print` CSS (hides buttons, keeps content) — no server involvement.
- **Excel export** — client-side via SheetJS (CDN), builds a Summary +
  100-year Illustration workbook from the last computed result.

**A real bug fixed along the way:** the header badge showing the API URL
was hardcoded static text ("127.0.0.1:5001"), never actually synced to
the real `API_BASE` — so it was quietly showing the wrong URL on your
live production site the whole time, even though the real calculations
were correctly hitting your real backend. Now set dynamically from
`API_BASE` on load.

**Testing limitation, disclosed honestly:** this sandbox has no internet
access, so I could not load the SheetJS CDN library here and verify the
Excel file it produces opens correctly in real Excel. I did verify my
code's *logic* is structurally sound — mocked the XLSX library and
confirmed it's called with correctly-shaped data (Summary: 8 rows;
Illustration: 102 rows = header + 101 years) — but the actual generated
`.xlsx` file itself is unverified by me. Please test the "Download Excel"
button once on your live site and let me know if the file doesn't open
cleanly.

**Now complete:** every item from the original punch-list — lumpsum
goals, Sensitivity Ranking, Goal Seek, Stress Test, Sequence-of-Returns
Risk, Scenario Comparison, Undo, Save/Load Plan, PDF export, Excel
export.
