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
