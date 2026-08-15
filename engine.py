"""
engine.py — The core calculation engine for the Two-Need, Two-Phase
Post-Retirement Planner, ported from the original client-side JavaScript
(recalcAll() and its helper functions in retirement-planner.html).

This is a pure, side-effect-free port: every function takes plain data in
and returns plain data out (no DOM, no globals). Variable names are kept
identical to the JS source wherever possible, specifically to make manual
line-by-line comparison against the original easy — this matters a lot for
a financial calculator, where a silent porting bug is far worse than the
IP-exposure problem this whole exercise exists to solve.

Numerical parity with the original JS was verified by running matched
inputs through both engines and diffing every output field — see
test_parity.py.
"""

import math
import copy
from datetime import datetime, timedelta

YEARS = 100  # length of the year-by-year illustration, matches the JS constant


# ============================================================
# Helpers — direct ports of the small JS utility functions
# ============================================================

def safe_div(numerator, denom):
    if abs(denom) < 1e-9:
        denom = 1e-9 if denom >= 0 else -1e-9
    return numerator / denom


def excel_pv(rate, nper, pmt, fv=0, type_=0):
    """Excel-equivalent PV, annuity with optional type (0=end,1=begin)."""
    if abs(rate) < 1e-12:
        return -(fv + pmt * nper)
    pow_ = (1 + rate) ** nper
    return -(fv + pmt * (1 + rate * type_) * (pow_ - 1) / rate) / pow_


def growing_annuity_pv(P, r, g, n):
    """PV required to fund payment P for n years, growing at g, earning r (begin-of-period)."""
    denom = (r - g) * ((1 + r) ** n)
    bracket = (1 + r) ** (n + 1) - (1 + g) ** (n + 1)
    return safe_div(P, denom) * bracket


def growing_annuity_pmt(PV, r, g, n):
    """Payment obtainable from PV for n years, growing at g, earning r (begin-of-period)."""
    numer_val = PV * (r - g) * ((1 + r) ** n)
    denom = (1 + r) ** (n + 1) - (1 + g) ** (n + 1)
    return safe_div(numer_val, denom)


def parse_date(s):
    """Matches JS `new Date(v + 'T00:00:00')` for a 'YYYY-MM-DD' input string."""
    if not s:
        return datetime.now()
    return datetime.strptime(s, "%Y-%m-%d")


def add_days(date, days):
    return date + timedelta(days=round(days))


def days_between(a, b):
    return (a - b).total_seconds() / 86400.0


def fmt_date(d):
    return d.strftime("%d %b %Y")


# ============================================================
# The core engine — a faithful port of recalcAll()'s computation
# (everything EXCEPT direct DOM writes, which don't apply server-side)
# ============================================================

def compute_plan(data, sequence_shock=None):
    """
    data: dict matching the frontend's getPlanData() shape.
    sequence_shock: optional {"years": int, "delta": float} — same as the
        frontend's sequenceShockOverride, decimal delta (e.g. -0.04).
    Returns a dict of every figure the frontend needs to render — the
    100-year illustration array plus all derived summary figures.
    """
    def num(key, default=0):
        v = data.get(key, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    # ---- Basics ----
    plan_start = parse_date(data.get('planStartDate'))
    life_span_years = num('lifeSpanYears')
    active_phase_years = num('activePhaseYears')
    review_date = parse_date(data.get('reviewDate'))
    active_return = num('activeReturn') / 100
    restive_return = num('restiveReturn') / 100
    inflation = num('inflation') / 100
    adj_inflation = inflation - 0.000001  # matches JS "adjusted inflation to avoid #DIV/0"
    total_corpus = num('totalCorpus')

    plan_end_date = add_days(plan_start, life_span_years * 365)
    active_phase_end_date = add_days(plan_start, active_phase_years * 365)
    years_left_plan_end = round(days_between(plan_end_date, review_date) / 365)
    active_years_left = max(round(days_between(active_phase_end_date, review_date) / 365), 0)

    high_risk = (active_phase_years >= 10 and review_date < active_phase_end_date)

    # ---- Purposes (future lumpsum requirements) ----
    total_reserved = 0
    purposes_out = []
    for p in data.get('purposes', []):
        target = p['requirement'] * (1 + p['inflation'] / 100) ** p['years']
        inv_total = 0
        mat_total = 0
        investments_out = []
        for inv in p.get('investments', []):
            mat = inv['amount'] * (1 + inv['ret'] / 100) ** p['years']
            inv_total += inv['amount']
            mat_total += mat
            investments_out.append({'maturity': mat})
        surplus = mat_total - target
        total_reserved += inv_total
        purposes_out.append({
            'target': target, 'invTotal': inv_total, 'matTotal': mat_total,
            'surplus': surplus, 'investments': investments_out
        })
    net_corpus_for_self = total_corpus - total_reserved

    # ---- Primary table totals ----
    primary_table = data.get('primaryTable', [])
    primary_amount_total = sum(r['amount'] for r in primary_table)
    if primary_amount_total > 0:
        primary_weighted_return = sum(r['amount'] * (r['ret'] / 100) for r in primary_table) / primary_amount_total
    else:
        primary_weighted_return = 0

    # ---- Monthly income requirements ----
    primary_needs = num('primaryNeeds')
    non_investment_income = num('nonInvestmentIncome')
    net_primary_need = primary_needs - non_investment_income

    primary_annual_withdrawal = round(growing_annuity_pmt(primary_amount_total, primary_weighted_return, adj_inflation, years_left_plan_end))
    withdraw_primary_monthly = primary_annual_withdrawal / 12
    primary_surplus = withdraw_primary_monthly - net_primary_need

    secondary_needs = num('secondaryNeeds')
    surplus_from_primary = max(primary_surplus, 0)
    withdraw_secondary_monthly = max(secondary_needs - surplus_from_primary, 0)
    secondary_surplus = (withdraw_secondary_monthly + surplus_from_primary) - secondary_needs

    # ---- Secondary table ----
    target_return = active_return if high_risk else restive_return
    corpus_available = net_corpus_for_self
    corpus_for_secondary_cap = corpus_available - primary_amount_total
    secondary_table = data.get('secondaryTable', [])
    secondary_raw_total = sum(r['amount'] for r in secondary_table)
    secondary_invested_total = min(secondary_raw_total, corpus_for_secondary_cap)

    secondary_annual_withdrawal = excel_pv(active_return / 12, 12, -withdraw_secondary_monthly, 0, 1)

    # ---- Additional active-phase contributions ----
    active_contribution = num('activeContribution')
    active_contribution_annual = active_contribution * 12
    net_secondary_withdrawal_active = secondary_annual_withdrawal - active_contribution_annual

    # ---- Illustration data (100 years) — the single source of truth ----
    illus = []
    prior_pri_closing, prior_pri_withdraw = 0, primary_annual_withdrawal
    prior_sec_closing, prior_sec_withdraw, prior_sec_contribution = 0, secondary_annual_withdrawal, 0

    for y in range(0, YEARS + 1):
        pri_roi = primary_weighted_return
        sec_roi = active_return if y < active_years_left else restive_return
        if sequence_shock and y < sequence_shock.get('years', 0):
            sec_roi += sequence_shock.get('delta', 0)

        if y == 0:
            pri_open = primary_amount_total
            pri_withdraw = primary_annual_withdrawal
            sec_open = secondary_invested_total
            sec_withdraw = secondary_annual_withdrawal
            sec_contribution = active_contribution_annual if (0 < active_years_left) else 0
        else:
            pri_open = prior_pri_closing if (y <= years_left_plan_end) else 0
            pri_withdraw = prior_pri_withdraw * (1 + adj_inflation) if pri_open != 0 else 0
            sec_open = prior_sec_closing if (y <= years_left_plan_end) else 0
            sec_withdraw = prior_sec_withdraw * (1 + adj_inflation) if sec_open != 0 else 0
            sec_contribution = prior_sec_contribution * (1 + adj_inflation) if (y < active_years_left) else 0

        pri_balance = pri_open - pri_withdraw
        pri_closing = pri_balance * (1 + pri_roi)
        sec_balance = sec_open - sec_withdraw + sec_contribution
        sec_closing = sec_balance * (1 + sec_roi)

        illus.append({
            'y': y, 'priROI': pri_roi, 'priOpen': pri_open, 'priWithdraw': pri_withdraw, 'priClosing': pri_closing,
            'secROI': sec_roi, 'secOpen': sec_open, 'secWithdraw': sec_withdraw,
            'secContribution': sec_contribution, 'secClosing': sec_closing
        })

        prior_pri_closing, prior_pri_withdraw = pri_closing, pri_withdraw
        prior_sec_closing, prior_sec_withdraw, prior_sec_contribution = sec_closing, sec_withdraw, sec_contribution

    active_years_left_idx = max(min(round(active_years_left), YEARS), 0)
    years_left_plan_end_idx = max(min(round(years_left_plan_end), YEARS), 0)

    corpus_after_active = illus[active_years_left_idx]['secOpen']
    remaining_restive_years = years_left_plan_end - active_years_left
    equiv_annual_req_at_restive_start = secondary_annual_withdrawal * (1 + adj_inflation) ** active_years_left
    corpus_after_restive = round(illus[years_left_plan_end_idx]['secClosing'])

    min_primary_corpus = round(growing_annuity_pv(net_primary_need * 12, primary_weighted_return, adj_inflation, years_left_plan_end))

    # ---- Tax impact (illustrative) ----
    primary_tax_rate = num('primaryTaxRate') / 100
    secondary_tax_rate = num('secondaryTaxRate') / 100
    equity_exemption = num('equityExemption')

    primary_tax = max(primary_annual_withdrawal, 0) * primary_tax_rate
    primary_post_tax = primary_annual_withdrawal - primary_tax

    secondary_taxable_base = max(secondary_annual_withdrawal - equity_exemption, 0)
    secondary_tax = secondary_taxable_base * secondary_tax_rate
    secondary_post_tax = secondary_annual_withdrawal - secondary_tax

    combined_post_tax_monthly = (primary_post_tax + secondary_post_tax) / 12 + non_investment_income

    # ---- Summary rail ----
    monthly_net = (withdraw_primary_monthly - net_primary_need) + secondary_surplus
    withdrawal_rate = ((primary_needs + secondary_needs) * 12) / net_corpus_for_self if net_corpus_for_self > 0 else 0

    # ---- Depletion detection ----
    depletion_year = None
    for y in range(1, years_left_plan_end_idx + 1):
        if illus[y]['secClosing'] <= 0:
            depletion_year = y
            break

    return {
        'planEndDate': fmt_date(plan_end_date),
        'activePhaseEndDate': fmt_date(active_phase_end_date),
        'yearsLeftPlanEnd': years_left_plan_end,
        'activeYearsLeft': active_years_left,
        'highRisk': high_risk,
        'totalReserved': total_reserved,
        'netCorpusForSelf': net_corpus_for_self,
        'purposes': purposes_out,
        'primaryAmountTotal': primary_amount_total,
        'primaryWeightedReturn': primary_weighted_return,
        'netPrimaryNeed': net_primary_need,
        'primaryAnnualWithdrawal': primary_annual_withdrawal,
        'withdrawPrimaryMonthly': withdraw_primary_monthly,
        'primarySurplus': primary_surplus,
        'surplusFromPrimary': surplus_from_primary,
        'withdrawSecondaryMonthly': withdraw_secondary_monthly,
        'secondarySurplus': secondary_surplus,
        'targetReturn': target_return,
        'corpusForSecondaryCap': corpus_for_secondary_cap,
        'secondaryInvestedTotal': secondary_invested_total,
        'secondaryAnnualWithdrawal': secondary_annual_withdrawal,
        'netSecondaryWithdrawalActive': net_secondary_withdrawal_active,
        'corpusAfterActive': corpus_after_active,
        'remainingRestiveYears': remaining_restive_years,
        'equivAnnualReqAtRestiveStart': equiv_annual_req_at_restive_start,
        'corpusAfterRestive': corpus_after_restive,
        'minPrimaryCorpus': min_primary_corpus,
        'primaryTax': primary_tax,
        'primaryPostTax': primary_post_tax,
        'secondaryTax': secondary_tax,
        'secondaryPostTax': secondary_post_tax,
        'combinedPostTaxMonthly': combined_post_tax_monthly,
        'monthlyNet': monthly_net,
        'withdrawalRate': withdrawal_rate,
        'depletionYear': depletion_year,
        'activeYearsLeftIdx': active_years_left_idx,
        'yearsLeftPlanEndIdx': years_left_plan_end_idx,
        'illus': illus,
    }


# ============================================================
# GOAL SEEK — server-side binary search. This is a faithful port of the
# original frontend's GOAL_SEEK_CONFIG + runGoalSeek(), just running the
# ~40-iteration search loop on the server (one request) instead of the
# browser driving 40 separate network round-trips.
# ============================================================

GOAL_SEEK_CONFIG = {
    'activeContribution': {
        'label': 'additional monthly investment during the active phase',
        'unitSuffix': '/month', 'lo': 0, 'hi': 1000000, 'direction': 'increasing'
    },
    'totalCorpus': {
        'label': 'total corpus today',
        'unitSuffix': '', 'lo': 0, 'hi': 500000000, 'direction': 'increasing'
    },
    'secondaryNeeds': {
        'label': 'secondary (lifestyle) monthly spending',
        'unitSuffix': '/month', 'lo': 0, 'hi': 2000000, 'direction': 'decreasing'
    },
    'primaryNeeds': {
        'label': 'primary (essential) monthly spending',
        'unitSuffix': '/month', 'lo': 0, 'hi': 2000000, 'direction': 'decreasing'
    },
}


def fmt_inr(n):
    if n is None:
        return '—'
    neg = n < 0
    v = round(abs(n))
    s = '₹' + format(v, ',')
    # convert Western thousands separators to Indian lakh/crore grouping
    s_digits = str(v)
    if len(s_digits) > 3:
        last3 = s_digits[-3:]
        rest = s_digits[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        s = '₹' + ','.join(parts) + ',' + last3
    else:
        s = '₹' + s_digits
    return ('−' + s) if neg else s


def goal_seek(plan_data, variable, target):
    cfg = GOAL_SEEK_CONFIG.get(variable)
    if not cfg:
        raise ValueError(f'Unknown goal-seek variable: {variable}')

    def eval_at(v):
        trial = dict(plan_data)
        trial[variable] = v
        return compute_plan(trial)['corpusAfterRestive']

    label, suffix, lo0, hi0, direction = cfg['label'], cfg['unitSuffix'], cfg['lo'], cfg['hi'], cfg['direction']

    if direction == 'increasing':
        lo_result = eval_at(lo0)
        hi_result = eval_at(hi0)
        if lo_result >= target:
            return {
                'message': f"No change needed to your {label} — your plan already reaches {fmt_inr(lo_result)} at plan end, {fmt_inr(lo_result - target)} above your target of {fmt_inr(target)}.",
                'cls': 'pos', 'solvedValue': None
            }
        if hi_result < target:
            msg = f"Even raising your {label} to {fmt_inr(hi0)}{suffix} isn't enough to reach {fmt_inr(target)}."
            if variable == 'totalCorpus':
                mid_check = eval_at(lo0 + (hi0 - lo0) / 2)
                if abs(hi_result - mid_check) < 1:
                    msg += " In fact, beyond a certain point, raising your total corpus stops helping at all — your secondary corpus investment table (Section 3) caps how much of it actually gets invested for this need, so extra corpus above that cap isn't put to work here. Increase the amounts in that table directly, or lower withdrawals / extend the active phase instead."
                else:
                    msg += " Consider lowering withdrawals, extending the active phase, or revisiting the target."
            else:
                msg += " Consider lowering withdrawals, extending the active phase, or revisiting the target."
            return {'message': msg, 'cls': 'neg', 'solvedValue': None}

        lo, hi = lo0, hi0
        for _ in range(40):
            mid = (lo + hi) / 2
            if eval_at(mid) < target:
                lo = mid
            else:
                hi = mid
        solved = math.ceil(hi / 100) * 100
        solved_result = eval_at(solved)
        return {
            'message': f"Raising your {label} to about {fmt_inr(solved)}{suffix} should get you to about {fmt_inr(solved_result)} at plan end (target: {fmt_inr(target)}).",
            'cls': 'pos', 'solvedValue': solved
        }
    else:
        lo_result = eval_at(lo0)
        hi_result = eval_at(hi0)
        if lo_result < target:
            return {
                'message': f"Even reducing your {label} to ₹0 isn't enough to reach {fmt_inr(target)} — this target isn't achievable through spending alone with your current plan.",
                'cls': 'neg', 'solvedValue': None
            }
        if hi_result >= target:
            return {
                'message': f"Your plan comfortably supports a {label} as high as {fmt_inr(hi0)}{suffix} and still reaches {fmt_inr(target)} at plan end — you have significant headroom.",
                'cls': 'pos', 'solvedValue': hi0
            }
        lo, hi = lo0, hi0
        for _ in range(40):
            mid = (lo + hi) / 2
            if eval_at(mid) >= target:
                lo = mid
            else:
                hi = mid
        solved = math.floor(lo / 100) * 100
        solved_result = eval_at(solved)
        return {
            'message': f"Keeping your {label} at or below about {fmt_inr(solved)}{suffix} should let you reach about {fmt_inr(solved_result)} at plan end (target: {fmt_inr(target)}).",
            'cls': 'pos', 'solvedValue': solved
        }


# ============================================================
# SENSITIVITY RANKING — a faithful port of the original frontend's
# SENSITIVITY_LEVERS + runSensitivityAnalysis(). Each lever is tested in
# isolation against the true baseline (never stacked), same as before —
# just running compute_plan() 7 times server-side (1 baseline + 6 levers)
# in a single request instead of the browser driving 7 separate calls.
# ============================================================

SENSITIVITY_LEVERS = [
    {'key': 'returns_down', 'label': 'Returns 2% lower'},
    {'key': 'inflation_up', 'label': 'Inflation 2% higher'},
    {'key': 'live_longer', 'label': 'Living 5 years longer'},
    {'key': 'contribution_up', 'label': 'Contributing ₹20,000/month more'},
    {'key': 'secondary_down', 'label': 'Secondary spending 10% lower'},
    {'key': 'primary_down', 'label': 'Primary spending 10% lower'},
]


def _apply_lever(plan_data, key):
    trial = copy.deepcopy(plan_data)
    if key == 'returns_down':
        trial['activeReturn'] = trial.get('activeReturn', 0) - 2
        trial['restiveReturn'] = trial.get('restiveReturn', 0) - 2
        trial['primaryTable'] = [dict(r, ret=r['ret'] - 2) for r in trial.get('primaryTable', [])]
    elif key == 'inflation_up':
        trial['inflation'] = trial.get('inflation', 0) + 2
    elif key == 'live_longer':
        trial['lifeSpanYears'] = trial.get('lifeSpanYears', 0) + 5
    elif key == 'contribution_up':
        trial['activeContribution'] = trial.get('activeContribution', 0) + 20000
    elif key == 'secondary_down':
        trial['secondaryNeeds'] = round(trial.get('secondaryNeeds', 0) * 0.9)
    elif key == 'primary_down':
        trial['primaryNeeds'] = round(trial.get('primaryNeeds', 0) * 0.9)
    return trial


def sensitivity_ranking(plan_data):
    baseline_end = compute_plan(plan_data)['corpusAfterRestive']
    results = []
    for lever in SENSITIVITY_LEVERS:
        trial = _apply_lever(plan_data, lever['key'])
        stressed_end = compute_plan(trial)['corpusAfterRestive']
        results.append({
            'key': lever['key'],
            'label': lever['label'],
            'delta': stressed_end - baseline_end,
            'stressedEnd': stressed_end,
        })
    results.sort(key=lambda r: abs(r['delta']), reverse=True)
    return {'baselineEnd': baseline_end, 'results': results}


# ============================================================
# SEQUENCE-OF-RETURNS RISK — a faithful port of runSequenceRiskTest().
# Reuses the same sequence_shock parameter compute_plan() already
# supports. Three server-side calculations (baseline, front-loaded,
# spread-evenly) in one request.
# ============================================================

def sequence_risk_test(plan_data, shock_years, shock_delta_pct):
    shock_years = max(1, int(shock_years) if shock_years is not None else 5)
    shock_delta_pct = max(0, float(shock_delta_pct) if shock_delta_pct is not None else 4)

    baseline = compute_plan(plan_data)
    baseline_end = baseline['corpusAfterRestive']
    plan_years = max(baseline['yearsLeftPlanEnd'], 1)

    # Scenario A: bad years concentrated right at the start
    front_loaded = compute_plan(plan_data, sequence_shock={'years': shock_years, 'delta': -shock_delta_pct / 100})
    front_loaded_end = front_loaded['corpusAfterRestive']

    # Scenario B: the exact same total shortfall, spread evenly across the
    # whole plan instead — only touches secondary-corpus returns, same as
    # the shock above, so the comparison isolates the effect of TIMING.
    spread_delta_pct = (shock_years * shock_delta_pct) / plan_years
    spread_trial = copy.deepcopy(plan_data)
    # The original JS writes this adjusted return through a DOM input's
    # .toFixed(3) before reading it back — replicate that same rounding
    # here, or the two engines drift apart by a tiny amount that compounds
    # over a 100-year simulation into a real (if small) discrepancy.
    spread_trial['activeReturn'] = round(spread_trial.get('activeReturn', 0) - spread_delta_pct, 3)
    spread_trial['restiveReturn'] = round(spread_trial.get('restiveReturn', 0) - spread_delta_pct, 3)
    spread = compute_plan(spread_trial)
    spread_end = spread['corpusAfterRestive']

    timing_cost = spread_end - front_loaded_end
    year_word = 'year' if shock_years == 1 else 'years'

    if abs(timing_cost) < 1:
        message = f"In your plan, having {shock_years} bad {year_word} right at the start makes almost no difference compared to spreading the same shortfall evenly — both land at around {fmt_inr(front_loaded_end)}. That can happen when your secondary corpus contribution/withdrawal pattern is small relative to the corpus itself."
        cls = 'msg'
    elif timing_cost > 0:
        message = f"Same total shortfall, very different outcome: {shock_years} bad {year_word} right at the start leaves you with {fmt_inr(front_loaded_end)} — {fmt_inr(timing_cost)} worse than if the exact same dip had been spread evenly across your whole plan ({fmt_inr(spread_end)}). This is sequence-of-returns risk: bad years early, while you're still drawing down a large balance, hurt far more than the same bad years later."
        cls = 'neg'
    else:
        message = f"Interesting — in your specific plan, front-loading the bad years actually left you {fmt_inr(abs(timing_cost))} better off ({fmt_inr(front_loaded_end)}) than spreading the same shortfall evenly ({fmt_inr(spread_end)}). Sequence effects can cut both ways: this can happen when there's already a large shortfall and a depleted balance compounds more slowly at a lower rate, or when strong contributions are still flowing in during those early years, effectively \"buying in\" at a lower return before things recover. Either way, this isn't a sign your plan is wrong — just a reminder that timing effects are genuinely counter-intuitive."
        cls = 'pos'

    return {
        'baselineEnd': baseline_end,
        'frontLoadedEnd': front_loaded_end,
        'spreadEnd': spread_end,
        'message': message,
        'cls': cls,
    }
