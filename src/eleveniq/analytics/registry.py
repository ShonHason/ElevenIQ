"""
The metric registry: which season column is DNA, display or a signature trait in
which position group (plan 005 M4c, ADR 004 §10).

Every status is derived, never typed in. derive() applies the pre-registered rules
of ADR 004 §10.3 (thresholds in config.py) to the committed measurements
(measurements/statsbomb_27.json, written by spikes/m4_decision_measurements.py
--write-json). Hand decisions are explicit entries below, each with an ADR
reference: fixed-display outcomes, D17 collinearity choices, the D12 possession
allow-list and overrides. Any entry or measurement that breaks a rule raises
RegistryRuleError, and the module resolves REGISTRY at import, so a broken entry
fails the import.

Steps per metric x group (ADR 004 §10.3), first match wins; overrides last:
  1. fixed display (an ADR outcome);
  2. D17 drop;
  3. per-90s only: zero share > ZERO_HEAVY_SHARE -> signature if r >= DNA_RELIABILITY_MIN,
     else display;
  4. r < DNA_RELIABILITY_MIN -> display; a ratio also needs coverage >= RATIO_MIN_COVERAGE;
  5. possession trigger (per-90s and ratios) -> display + team_style_biased, unless an
     allow-list entry admits the cell. Either proxy triggers by the mean rule (|mean rho| >=
     POSSESSION_RHO_TRIGGER and the sign test) or the majority rule (ADR 004 §13.1: at least
     POSSESSION_MAJORITY_LEAGUES leagues at |rho| >= the trigger, all of one sign); the allow-list
     admits a majority-triggered cell as it admits a mean-triggered one (M4g2 O1);
  6. team_pass_share (D12b, DM-CM and CB only);
  7. otherwise DNA, with a lower-reliability note when r < LOW_RELIABILITY_BELOW.

Mover verdicts (ADR 004 §13.7, plan 005 M4g3b): one per metric, pooled over the groups, derived
from the measurements' movers block with the Rules mover thresholds. No cell status, note or
reason depends on them (Derivation.movers, mover()).

Possession and the Defending restore (ADR 004 §13.8, plan 005 M4g4d; measurements schema 3):
from the possession block's numbers and the Rules criteria, each Defending DNA cell's candidate
checks, its selection (asserted equal to the engine's), the M4g4c2 trust check and the column the
vector uses (Derivation.possession; an adjusted column raises: not implemented, none in
2015/16); the mover verdict per (metric, candidate) (candidate_movers; β = 0 reuses M4g3b); and
the D5 Defending score status per group (defending_status), which dna.withheld_for reads.

M4d (percentiles), M4f (DNA) and M6 (rarity) read REGISTRY through cell(),
dna_metrics(), display_metrics() and signature_traits(), and stamp REGISTRY_VERSION.
Everything here is computed within MEASUREMENT_SOURCE only (CLAUDE.md).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, replace
from importlib import resources
from typing import Any, Literal, Mapping, Sequence

from eleveniq import config
from eleveniq.metrics.positions import OUTFIELD_GROUPS

Status = Literal["dna", "display", "signature"]
Direction = Literal["higher_better", "lower_better", "style"]
Unit = Literal["per90", "ratio", "share"]
Family = Literal["Passing", "Progression", "Carrying", "Creation", "Goal threat",
                 "Defending", "Aerial", "Ball security"]

# Display order of the outfield groups. GK is never in a registry cell (ADR 004 D21).
GROUPS: tuple[str, ...] = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
STATUSES: frozenset[str] = frozenset({"dna", "display", "signature"})
POSSESSION_PROXIES: tuple[str, ...] = ("poss_dur_loo", "poss_pass_loo")
# D12b is pre-registered for these two groups only; every other group is display.
TEAM_PASS_SHARE_GROUPS: tuple[str, ...] = ("DM-CM", "CB")
# Plan 005: goals and assists are never DNA. ADR 004 §8: restart columns are never DNA.
GOALS_AND_ASSISTS: frozenset[str] = frozenset(
    {"goals_p90", "assists_p90", "op_assists_p90", "restart_assists_p90"})
RESTART_PREFIX = "restart_"

# Rule ids carried in Cell.reasons.
R_FIXED = "fixed_display"
R_D17 = "d17_dropped"
R_ZERO_HEAVY = "zero_heavy"
R_UNRELIABLE = "unreliable"
R_LOW_COVERAGE = "low_coverage"
R_NO_SIGNAL = "no_signal"
R_UNTESTABLE = "possession_untestable"
R_TEAM_STYLE = "team_style_biased"
R_ALLOW = "allow_listed"
R_MAJORITY = "majority_triggered"
R_D12B = "d12b"
R_D12B_NOT_REGISTERED = "d12b_not_preregistered"
R_DNA = "dna"
R_OVERRIDE = "override"

NOTE_TEAM_STYLE = "team-style influenced"
NOTE_LOWER_RELIABILITY = "lower reliability"


class RegistryRuleError(ValueError):
    """An entry or a measurement breaks a registry rule (ADR 004 §10.3)."""


# ── Static metadata ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RatioSpec:
    """numerator / (Σ denominator_plus − Σ denominator_minus), as the pipeline stores it."""
    numerator: str
    denominator_plus: tuple[str, ...]
    denominator_minus: tuple[str, ...] = ()

    @property
    def terms(self) -> tuple[str, ...]:
        return (self.numerator, *self.denominator_plus, *self.denominator_minus)


@dataclass(frozen=True)
class FixedDisplay:
    """A pre-registered ADR outcome that keeps a column out of DNA in every group."""
    reason: str
    adr: str


@dataclass(frozen=True)
class MetricSpec:
    column: str
    display_name: str
    family: Family
    direction: Direction
    unit: Unit
    count_column: str | None = None   # per-90s: the season count (zero share, trait minimum)
    integer_count: bool = False       # False for xG sums: no signature count minimum (§10.2)
    ratio: RatioSpec | None = None
    applies_to: tuple[str, ...] = GROUPS
    fixed_display: FixedDisplay | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AllowEntry:
    """D12: admits a possession-triggered cell whose max |mean ρ| is <= max_rho."""
    column: str
    group: str
    max_rho: float
    adr: str


@dataclass(frozen=True)
class CollinearChoice:
    """D17: in each group, `kept` stays and `dropped` is display."""
    kept: str
    dropped: str
    groups: tuple[str, ...]
    adr: str


@dataclass(frozen=True)
class Override:
    column: str
    group: str
    status: Status
    reason: str
    adr: str


@dataclass(frozen=True)
class Rules:
    """The ADR 004 §10.3 constants. Defaults come from config.py."""
    dna_reliability_min: float = config.DNA_RELIABILITY_MIN
    low_reliability_below: float = config.LOW_RELIABILITY_BELOW
    zero_heavy_share: float = config.ZERO_HEAVY_SHARE
    ratio_min_coverage: float = config.RATIO_MIN_COVERAGE
    possession_rho_trigger: float = config.POSSESSION_RHO_TRIGGER
    possession_sign_neutral: float = config.POSSESSION_SIGN_NEUTRAL
    possession_majority_leagues: int = config.POSSESSION_MAJORITY_LEAGUES
    possession_allow_max_rho: float = config.POSSESSION_ALLOW_MAX_RHO
    team_pass_share_max_rho: float = config.TEAM_PASS_SHARE_MAX_RHO
    collinear_rho: float = config.COLLINEAR_RHO
    global_ok_band: tuple[float, float] = config.GLOBAL_OK_BAND
    min_cohort_size: int = config.MIN_COHORT_SIZE
    signature_min_count_floor: int = config.SIGNATURE_TRAIT_MIN_COUNT_FLOOR
    boundary_margin: float = config.BOUNDARY_MARGIN
    d9_boundary_margin_pts: float = config.D9_BOUNDARY_MARGIN_PTS
    mover_f_threshold: float = config.MOVER_F_THRESHOLD
    mover_min_n: int = config.MOVER_MIN_N
    mover_low_power: float = config.MOVER_LOW_POWER
    possession_criterion_rho_max: float = config.POSSESSION_CRITERION_RHO_MAX
    possession_sign_dead_zone: float = config.POSSESSION_SIGN_DEAD_ZONE
    possession_inconsistent_rho: float = config.POSSESSION_INCONSISTENT_RHO
    possession_gap_max: float = config.POSSESSION_GAP_MAX
    trust_stability_min: float = config.TRUST_STABILITY_MIN
    trust_lolo_min_agree: int = config.TRUST_LOLO_MIN_AGREE
    trust_p_max: float = config.TRUST_P_MAX
    trust_d_min_count: int = config.TRUST_D_MIN_COUNT


# ── Derived table ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Cell:
    column: str
    group: str
    status: Status
    reasons: tuple[str, ...]
    team_style_biased: bool = False
    low_reliability: bool = False
    global_ok: bool | None = None          # D9, recomputed from the league means; None = not measured
    d9_margin: float | None = None         # D9: nearest league mean's signed distance to the band edge
    basis: Literal["global", "local"] = "local"   # D10: global only when global_ok
    reliability: float | None = None       # r_full (per-90), split-half r (ratio, share)
    min_attempts: int | None = None        # ratios (D19); None = not a ratio, or no signal
    signature_min_count: int | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoundaryCell:
    """A judged value within ±BOUNDARY_MARGIN of its threshold (ADR 004 §10.10)."""
    rule: str
    group: str
    column: str
    value: float
    threshold: float
    per_league: tuple[float, ...] | None   # ρ rows: the pool leagues, in provenance order
    status: Status = "display"             # the final cell outcome
    team_style_biased: bool = False
    low_reliability: bool = False


@dataclass(frozen=True)
class MajorityProxy:
    """One proxy's per-league view of a majority-triggered cell (stats-reviewer, M4g2 O1)."""
    proxy: str
    per_league: tuple[float, ...]          # the pool leagues, in provenance order
    leagues_at_trigger: int                # leagues with |rho| >= POSSESSION_RHO_TRIGGER
    max_single_league: float               # the largest single-league |rho|
    fires: bool                            # the majority rule fires on this proxy


@dataclass(frozen=True)
class MajorityAdmitted:
    """A cell the majority rule triggers and the allow-list admits (ADR 004 §13.1, M4g2 O1)."""
    column: str
    group: str
    proxies: tuple[MajorityProxy, ...]


MoverVerdictName = Literal["PASS", "FAIL", "inconclusive", "untestable"]


@dataclass(frozen=True)
class MoverVerdict:
    """A metric's mover verdict (ADR 004 §13.7), pooled over the groups. Report only.

    Untestable (n < Rules.mover_min_n): f_hat, ci, r_m, power and the three flags are None.
    f_hat and the CI bounds may be +inf (R_m ≤ 0: no stable between-player signal)."""
    column: str
    verdict: MoverVerdictName
    n: int
    f_hat: float | None = None
    ci: tuple[float, float] | None = None
    r_m: float | None = None
    power_at_f0: float | None = None
    power_se: float | None = None
    underpowered: bool | None = None          # "underpowered at n": P(PASS | f = 0) < mover_low_power
    borderline_seed: bool | None = None       # "borderline (seed-sensitive)": an extra seed's verdict differs
    below_model_range: bool | None = None     # "PASS (below model range)": PASS with CI upper bound < 0
    extra_seed_verdicts: tuple[MoverVerdictName, ...] = ()
    s2_rms_a: float | None = None             # D7 (ADR 004 §13.8): S² RMS on local percentiles


# ADR 004 §13.8 (plan 005 M4g4): the possession candidates in their fixed pre-registered order.
POSSESSION_CANDIDATES: tuple[str, ...] = ("beta_0", "beta_hat", "team_share", "beta_1")
RAW_CANDIDATE = "beta_0"

# Family score statuses (ADR 004 §13.8 D5): a Defending score is restored ("ok") or withheld with
# the first failed test along the restore rule, in this precedence.
SCORE_OK = "ok"
POSSESSION_CRITERIA_FAILED = "possession_criteria_failed"
MOVER_STABILITY_UNTESTABLE = "mover_stability_untestable"
MOVER_STABILITY_FAILED = "mover_stability_failed"
MOVER_STABILITY_INCONCLUSIVE = "mover_stability_inconclusive"
MOVER_SEED_SENSITIVE = "mover_seed_sensitive"
MOVER_BELOW_MODEL_RANGE = "mover_below_model_range"
D5_STATUSES: tuple[str, ...] = (POSSESSION_CRITERIA_FAILED, MOVER_STABILITY_FAILED,
                                MOVER_STABILITY_INCONCLUSIVE, MOVER_STABILITY_UNTESTABLE,
                                MOVER_SEED_SENSITIVE, MOVER_BELOW_MODEL_RANGE)
_D5_STAGE: dict[str, int] = {POSSESSION_CRITERIA_FAILED: 0, MOVER_STABILITY_UNTESTABLE: 1,
                             MOVER_STABILITY_FAILED: 1, MOVER_STABILITY_INCONCLUSIVE: 1,
                             MOVER_SEED_SENSITIVE: 2, MOVER_BELOW_MODEL_RANGE: 3, SCORE_OK: 4}


@dataclass(frozen=True)
class CandidateCheck:
    """One possession candidate of a Defending DNA cell against criteria (1)-(3) and the sign
    flip (ADR 004 §13.8). Means per proxy in POSSESSION_PROXIES order; None: β̂ fit failed."""
    candidate: str
    c1_mean_rho: tuple[float, float] | None
    c1_pass: bool
    c2_gap: tuple[float, float] | None
    c2_pass: bool
    c3_r_full: float | None
    c3_pass: bool
    sign_flip: bool
    inconsistent_across_leagues: bool      # report only
    passes: bool
    fit_failed: bool = False


@dataclass(frozen=True)
class TrustCheck:
    """The M4g4c2 trust check of a selection other than β = 0 (ADR 004 §13.8). (d) is None
    unless the selection is β̂; (c) judges each criterion β = 0 failed."""
    a_p_same: float
    a_pass: bool
    b_agree: int
    b_pass: bool
    c_judged: tuple[str, ...]
    c_p: tuple[float, float]               # (p_rho, p_gap)
    c_pass: bool
    d_pass: bool | None
    passes: bool


@dataclass(frozen=True)
class PossessionAdjustment:
    """A Defending DNA cell's possession record (ADR 004 §13.8; plan 005 M4g4d)."""
    column: str
    group: str
    directional: bool
    beta_hat: float | None
    beta_hat_ci: tuple[float, float] | None   # team bootstrap, report only
    candidates: tuple[CandidateCheck, ...]    # POSSESSION_CANDIDATES order
    selected: str | None                      # the first passing candidate; None: none passes
    trust: TrustCheck | None                  # a selection other than β = 0 only
    applied: str                              # the column the vector uses (β = 0: raw)
    flags: tuple[str, ...]                    # "rises with own possession", "|β̂| > 1"


@dataclass(frozen=True)
class CandidateMover:
    """A mover verdict per (metric, candidate). β = 0 reuses the metric's M4g3b verdict. A
    candidate no group restores from is report only, not used for any restore."""
    column: str
    candidate: str
    verdict: MoverVerdict
    used_for_restore_in: tuple[str, ...]


@dataclass(frozen=True)
class FamilyStatus:
    """A family's score status in a group (ADR 004 §13.8 D5): "ok" restores the score; any
    other status withholds it. `member` is the directional member the status is that of."""
    family: str
    group: str
    status: str
    member: str | None
    candidate: str | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Derivation:
    cells: Mapping[tuple[str, str], Cell]
    boundary: tuple[BoundaryCell, ...]
    source: str
    version: str
    majority_admitted: tuple[MajorityAdmitted, ...] = ()
    movers: Mapping[str, MoverVerdict] = field(default_factory=dict)
    possession: Mapping[tuple[str, str], PossessionAdjustment] = field(default_factory=dict)
    candidate_movers: Mapping[tuple[str, str], CandidateMover] = field(default_factory=dict)
    family_status: Mapping[tuple[str, str], FamilyStatus] = field(default_factory=dict)

    def cell(self, column: str, group: str) -> Cell:
        try:
            return self.cells[(column, group)]
        except KeyError:
            raise KeyError(f"no registry cell for {column} × {group}") from None

    def mover(self, column: str) -> MoverVerdict:
        try:
            return self.movers[column]
        except KeyError:
            raise KeyError(f"no mover verdict for {column}") from None

    def _with(self, group: str, status: str) -> tuple[str, ...]:
        if group not in GROUPS:
            raise KeyError(f"{group!r} is not an outfield group {GROUPS}")
        return tuple(sorted(c for (c, g), cell in self.cells.items()
                            if g == group and cell.status == status))

    def dna_metrics(self, group: str) -> tuple[str, ...]:
        return self._with(group, "dna")

    def defending_status(self, group: str) -> FamilyStatus:
        try:
            return self.family_status[("Defending", group)]
        except KeyError:
            raise KeyError(f"no Defending status for {group!r}") from None

    def display_metrics(self, group: str) -> tuple[str, ...]:
        return self._with(group, "display")

    def signature_traits(self, group: str) -> tuple[str, ...]:
        return self._with(group, "signature")


# ── The specs (ADR 004 §10.7 families and directions; §10.3 fixed display) ──────

_D15 = FixedDisplay("D15: penalties are removed from shot metrics; the np_ form replaces it",
                    "ADR 004 D15, §10.2, §10.3")
_D12_VOLUME = FixedDisplay("D12: raw volume", "ADR 004 D12, §10.3")
_D18 = FixedDisplay("D18: position-biased", "ADR 004 D18, §10.3")
_D19 = FixedDisplay("D19: success rate, display-only", "ADR 004 D19, §10.3")
_R14 = FixedDisplay("R14: the op_ form replaces the total (restart contamination, §8)",
                    "ADR 004 §8, §10.3")
_NEVER_DNA = FixedDisplay("goals and assists are never DNA; restart columns are never DNA",
                          "plan 005 M4c; ADR 004 §8, §10.3")

_NON_RESTART = "non-restart delivery"
_LONG_THROW_NOTE = ("restart deliveries include throw-ins (5.6% of restart key passes), "
                    "so long-throw specialists score slightly higher")
_DRIBBLED_PAST_NOTE = "rises with how often a player engages"
_INTERCEPTIONS_NOTE = "team-style influenced (league means differ in every group, D9)"
_XG_COUNTS = frozenset({"xg", "np_xg", "xg_assisted", "xg_assisted_direct",
                        "op_xg_assisted", "restart_xg_assisted"})


def _p90(base: str, name: str, family: Family, direction: Direction, *,
         fixed: FixedDisplay | None = None, notes: tuple[str, ...] = ()) -> MetricSpec:
    return MetricSpec(column=f"{base}_p90", display_name=name, family=family,
                      direction=direction, unit="per90", count_column=base,
                      integer_count=base not in _XG_COUNTS, fixed_display=fixed, notes=notes)


def _ratio(column: str, name: str, family: Family, ratio: RatioSpec, *,
           fixed: FixedDisplay | None = None) -> MetricSpec:
    return MetricSpec(column=column, display_name=name, family=family, direction="higher_better",
                      unit="ratio", ratio=ratio, fixed_display=fixed)


SPECS: tuple[MetricSpec, ...] = (
    # Passing
    _ratio("pass_completion_pct", "Pass completion", "Passing",
           RatioSpec("passes_completed", ("passes_attempted",),
                     ("passes_unknown_outcome", "passes_injury_clearance"))),
    _ratio("long_pass_completion_pct", "Long pass completion", "Passing",
           RatioSpec("long_passes_completed", ("long_passes_attempted",),
                     ("long_passes_unknown_outcome", "long_passes_injury_clearance"))),
    _p90("long_passes_attempted", "Long passes", "Passing", "style"),
    MetricSpec(column="team_pass_share", display_name="Share of team open-play passes",
               family="Passing", direction="style", unit="share",
               ratio=RatioSpec("open_play_passes_attempted", ("team_open_play_passes_while_on",))),
    _p90("passes_attempted", "Passes attempted", "Passing", "style", fixed=_D12_VOLUME),
    _p90("passes_completed", "Passes completed", "Passing", "style", fixed=_D12_VOLUME),
    _p90("switches", "Switches of play", "Passing", "style", fixed=_D18),
    # Progression (pass progression; carry progression is under Carrying)
    _p90("progressive_passes", "Progressive passes", "Progression", "higher_better"),
    _p90("final_third_entries_pass", "Final-third entries (pass)", "Progression", "higher_better"),
    _p90("box_entries_pass", "Box entries (pass)", "Progression", "higher_better"),
    # Carrying
    _p90("progressive_carries", "Progressive carries", "Carrying", "higher_better"),
    _p90("final_third_entries_carry", "Final-third entries (carry)", "Carrying", "higher_better"),
    _p90("box_entries_carry", "Box entries (carry)", "Carrying", "higher_better"),
    _p90("dribbles_attempted", "Dribbles attempted", "Carrying", "style"),
    _p90("dribbles_completed", "Dribbles completed", "Carrying", "higher_better"),
    _ratio("dribble_success_pct", "Dribble success", "Carrying",
           RatioSpec("dribbles_completed", ("dribbles_attempted",)), fixed=_D19),
    _p90("carries", "Carries", "Carrying", "style", fixed=_D12_VOLUME),
    _p90("fouls_won", "Fouls won", "Carrying", "style"),
    # Creation
    _p90("op_key_passes", f"Key passes ({_NON_RESTART})", "Creation", "higher_better"),
    _p90("op_xg_assisted", f"xG assisted ({_NON_RESTART})", "Creation", "higher_better"),
    _p90("key_passes", "Key passes (all)", "Creation", "higher_better", fixed=_R14),
    _p90("xg_assisted", "xG assisted (all)", "Creation", "higher_better", fixed=_R14),
    _p90("xg_assisted_direct", "xG assisted (direct)", "Creation", "higher_better",
         fixed=FixedDisplay("D17: ~ xg_assisted 0.90–0.97", "ADR 004 D17, §10.3")),
    _p90("crosses_attempted", "Crosses", "Creation", "style",
         fixed=FixedDisplay("includes corner and free-kick deliveries (D14), the restart "
                            "contamination R14/§8 removed from creation; no op_ form yet",
                            "ADR 004 D14, §8, §10.3")),
    _ratio("cross_completion_pct", "Cross completion", "Creation",
           RatioSpec("crosses_completed", ("crosses_attempted",), ("crosses_unknown_outcome",)),
           fixed=_D19),
    _p90("op_assists", f"Assists ({_NON_RESTART})", "Creation", "higher_better", fixed=_NEVER_DNA),
    _p90("assists", "Assists", "Creation", "higher_better", fixed=_NEVER_DNA),
    _p90("restart_key_passes", "Set-piece key passes", "Creation", "higher_better",
         notes=(_LONG_THROW_NOTE,)),
    _p90("restart_xg_assisted", "Set-piece xG assisted", "Creation", "higher_better",
         fixed=FixedDisplay("U2: ~ restart_key_passes ρ 0.94–1.00", "ADR 004 §10.2 U2, §10.3"),
         notes=(_LONG_THROW_NOTE,)),
    _p90("restart_assists", "Set-piece assists", "Creation", "higher_better", fixed=_NEVER_DNA,
         notes=(_LONG_THROW_NOTE,)),
    # Goal threat
    _p90("np_shots", "Shots (non-penalty)", "Goal threat", "higher_better"),
    _p90("np_xg", "xG (non-penalty)", "Goal threat", "higher_better"),
    _p90("np_shots_on_target", "Shots on target (non-penalty)", "Goal threat", "higher_better"),
    _p90("shots_on_target", "Shots on target", "Goal threat", "higher_better", fixed=_D15),
    _ratio("np_xg_per_shot", "xG per shot (non-penalty)", "Goal threat",
           RatioSpec("np_xg", ("np_shots",))),
    _p90("shots", "Shots", "Goal threat", "higher_better", fixed=_D15),
    _p90("xg", "xG", "Goal threat", "higher_better", fixed=_D15),
    _ratio("xg_per_shot", "xG per shot", "Goal threat", RatioSpec("xg", ("shots",)), fixed=_D15),
    _p90("goals", "Goals", "Goal threat", "higher_better", fixed=_NEVER_DNA),
    # Defending
    _p90("tackles_attempted", "Tackles attempted", "Defending", "style"),
    _p90("tackles_won", "Tackles won", "Defending", "higher_better"),
    _ratio("tackle_success_pct", "Tackle success", "Defending",
           RatioSpec("tackles_won", ("tackles_attempted",)), fixed=_D19),
    # ADR 004 §13.2 (M4g2): a lost tackle and a Dribbled Past are both failures; the rules decide.
    _ratio("defensive_duel_success_pct", "Defensive duel success", "Defending",
           RatioSpec("tackles_won", ("tackles_attempted", "dribbled_past"))),
    # style, not higher_better (ADR 004 §12, user 2026-10-06): fails D9 in every group and
    # tracks team style (Barcelona 9.1 per match vs 13–18), like its Defending siblings.
    _p90("interceptions", "Interceptions", "Defending", "style",
         notes=(_INTERCEPTIONS_NOTE,)),
    _p90("ball_recoveries", "Ball recoveries", "Defending", "higher_better"),
    _p90("pressures", "Pressures", "Defending", "style"),
    _p90("clearances", "Clearances", "Defending", "style"),
    _p90("blocks", "Blocks", "Defending", "style"),
    # style, not lower_better (ADR 004 §13.2, M4g2): it rises with how often a player engages
    # (ρ 0.48–0.55 with tackles_attempted_p90 by group).
    _p90("dribbled_past", "Dribbled past", "Defending", "style",
         notes=(_DRIBBLED_PAST_NOTE,)),
    _p90("fouls_committed", "Fouls committed", "Defending", "style"),
    # Aerial
    _p90("aerials_won", "Aerials won", "Aerial", "higher_better"),
    _ratio("aerial_win_pct", "Aerial win rate", "Aerial",
           RatioSpec("aerials_won", ("aerials_won", "aerials_lost"))),
    _p90("aerials_lost", "Aerials lost", "Aerial", "style"),
    # Ball security
    _p90("dispossessed", "Dispossessed", "Ball security", "lower_better", fixed=_D18),
    _p90("miscontrols", "Miscontrols", "Ball security", "lower_better", fixed=_D18),
)

_D12_ADR = "ADR 004 D12, §10.3"
POSSESSION_ALLOW_LIST: tuple[AllowEntry, ...] = (
    *(AllowEntry(col, grp, config.POSSESSION_ALLOW_MAX_RHO, _D12_ADR)
      for col in ("progressive_passes_p90", "progressive_carries_p90") for grp in GROUPS),
    *(AllowEntry("long_pass_completion_pct", grp, config.POSSESSION_ALLOW_MAX_RHO,
                 "ADR 004 §10.2 (possession trigger on ratios), §10.3")
      for grp in ("CB", "FB-WB", "DM-CM")),
)

_D17_ADR = "ADR 004 D17, §10.3"
COLLINEAR_CHOICES: tuple[CollinearChoice, ...] = (
    CollinearChoice("tackles_attempted_p90", "tackles_won_p90", GROUPS, _D17_ADR),
    CollinearChoice("dribbles_attempted_p90", "dribbles_completed_p90", GROUPS, _D17_ADR),
    CollinearChoice("progressive_carries_p90", "final_third_entries_carry_p90",
                    ("FB-WB", "AM-Winger", "ST"), _D17_ADR),
    CollinearChoice("progressive_passes_p90", "final_third_entries_pass_p90", ("DM-CM",), _D17_ADR),
)

OVERRIDES: tuple[Override, ...] = (
    Override("restart_key_passes_p90", "AM-Winger", "signature",
             "restart columns are never DNA; AM-Winger restart_key_passes is not zero-heavy, "
             "so the rules alone would make it DNA",
             "ADR 004 §8, §10.2 U2"),
)


# ── Measurements ────────────────────────────────────────────────────────────────

MEASUREMENTS_FILE = "measurements/statsbomb_27.json"


def _reject_constant(name: str) -> Any:
    # NaN or Infinity would make every threshold comparison silently False.
    raise RegistryRuleError(f"measurements hold the non-standard JSON constant {name}")


def load_measurements(name: str = MEASUREMENTS_FILE) -> dict[str, Any]:
    """The committed measurement file, a package resource of eleveniq.analytics."""
    with resources.files("eleveniq.analytics").joinpath(name).open(encoding="utf-8") as f:
        return json.load(f, parse_constant=_reject_constant)


# ── derive() ────────────────────────────────────────────────────────────────────

def _blank(entry: str) -> bool:
    return not entry or not entry.strip()


def _validate_specs(specs: Sequence[MetricSpec], meas: Mapping[str, Any]) -> None:
    schema = set(meas["provenance"]["season_columns"])
    seen: set[str] = set()
    for s in specs:
        if s.column in seen:
            raise RegistryRuleError(f"{s.column}: registered twice")
        seen.add(s.column)
        bad_groups = [g for g in s.applies_to if g not in OUTFIELD_GROUPS]
        if bad_groups or not s.applies_to:
            raise RegistryRuleError(
                f"{s.column}: applies_to {s.applies_to} must be outfield groups only "
                f"(never GK, ADR 004 D21)")
        cols = [s.column, *([s.count_column] if s.count_column else []),
                *(s.ratio.terms if s.ratio else ())]
        missing = [c for c in cols if c not in schema]
        if missing:
            raise RegistryRuleError(f"{s.column}: {missing} not in the recorded season schema")
        if s.fixed_display and (_blank(s.fixed_display.reason) or _blank(s.fixed_display.adr)):
            raise RegistryRuleError(f"{s.column}: fixed display needs a reason and an ADR reference")
        if s.unit == "per90" and s.count_column is None:
            raise RegistryRuleError(f"{s.column}: a per-90 needs its count column")
        if s.unit == "ratio" and s.ratio is None:
            raise RegistryRuleError(f"{s.column}: a ratio needs its RatioSpec")

    by_col = {s.column: s for s in specs}
    for section, unit in (("metrics", "per90"), ("ratios", "ratio")):
        for col, m in meas[section].items():
            s = by_col.get(col)
            if s is None or s.unit != unit:
                raise RegistryRuleError(f"measured {section[:-1]} {col} has no {unit} registry spec")
            if unit == "per90" and m["count_column"] != s.count_column:
                raise RegistryRuleError(f"{col}: count column {s.count_column} ≠ measured "
                                        f"{m['count_column']}")
            if unit == "ratio" and (
                    m["numerator"] != s.ratio.numerator
                    or tuple(m["denominator_plus"]) != s.ratio.denominator_plus
                    or tuple(m["denominator_minus"]) != s.ratio.denominator_minus):
                raise RegistryRuleError(f"{col}: ratio terms differ from the measured definition")

    tps = meas.get("team_pass_share")
    if not isinstance(tps, Mapping) or set(tps) != set(TEAM_PASS_SHARE_GROUPS):
        raise RegistryRuleError(
            f"measurements must hold team_pass_share (D12b) for exactly {TEAM_PASS_SHARE_GROUPS}; "
            f"got {None if tps is None else sorted(tps)}")
    for s in specs:
        if s.fixed_display:
            continue
        section = {"per90": "metrics", "ratio": "ratios"}.get(s.unit)
        if section is None:
            continue
        groups = meas[section].get(s.column, {}).get("groups", {})
        absent = [g for g in s.applies_to if g not in groups]
        if absent:
            raise RegistryRuleError(f"{s.column}: no measurements for {absent}")


def _validate_entries(specs: Sequence[MetricSpec], allow: Sequence[AllowEntry],
                      collinear: Sequence[CollinearChoice], overrides: Sequence[Override],
                      rules: Rules) -> None:
    applies = {s.column: s.applies_to for s in specs}

    def known(col: str, grp: str, what: str) -> None:
        if col not in applies:
            raise RegistryRuleError(f"{what}: {col} is not a registry column")
        if grp not in applies[col]:
            raise RegistryRuleError(f"{what}: {col} does not apply to {grp}")

    seen_allow: set[tuple[str, str]] = set()
    for a in allow:
        known(a.column, a.group, "allow-list")
        if (a.column, a.group) in seen_allow:
            raise RegistryRuleError(f"allow-list: {a.column} × {a.group} listed twice")
        seen_allow.add((a.column, a.group))
        if _blank(a.adr):
            raise RegistryRuleError(f"allow-list {a.column} × {a.group}: no ADR reference")
        if a.max_rho > rules.possession_allow_max_rho:
            raise RegistryRuleError(f"allow-list {a.column} × {a.group}: bound {a.max_rho} above "
                                    f"the D12 maximum {rules.possession_allow_max_rho}")
    for c in collinear:
        if _blank(c.adr):
            raise RegistryRuleError(f"D17 choice {c.kept} over {c.dropped}: no ADR reference")
        for grp in c.groups:
            known(c.kept, grp, "D17 choice")
            known(c.dropped, grp, "D17 choice")
    seen_override: set[tuple[str, str]] = set()
    for o in overrides:
        where = f"override {o.column} × {o.group}"
        known(o.column, o.group, where)
        if (o.column, o.group) in seen_override:
            raise RegistryRuleError(f"{where}: listed twice")
        seen_override.add((o.column, o.group))
        if _blank(o.reason):
            raise RegistryRuleError(f"{where}: no reason")
        if _blank(o.adr):
            raise RegistryRuleError(f"{where}: no ADR reference")
        if o.status not in STATUSES:
            raise RegistryRuleError(f"{where}: unknown status {o.status!r}")
        if o.status == "dna" and o.column in GOALS_AND_ASSISTS:
            raise RegistryRuleError(f"{where}: goals and assists are never DNA (plan 005)")


def _league_means(cell: Mapping[str, Any] | None, pool: Sequence[str]) -> tuple[float, ...] | None:
    """The pool leagues' mean global percentiles in provenance order; None when D9 is undefined."""
    if cell is None or "league_mean_global_pct" not in cell:
        return None
    means = cell["league_mean_global_pct"]
    if any(means.get(c) is None for c in pool):
        return None
    return tuple(means[c] for c in pool)


def _d9_edge(mean: float, band: tuple[float, float]) -> tuple[float, float]:
    """(signed distance to the nearer band edge, that edge); positive inside the band."""
    lo, hi = band
    return min((mean - lo, lo), (hi - mean, hi))


def _d9(cell: Mapping[str, Any] | None, pool: Sequence[str], band: tuple[float, float],
        ref_n_local: Mapping[str, int] | None, min_n: int) -> dict[str, Any]:
    """D9, recomputed from the league means (the stored flag is not trusted, §10.1).
    d9_margin is the smallest signed distance of a league mean to the band edge: positive
    inside (global_ok), negative outside; None when D9 is undefined. D9 is undefined when a
    pool league's reference population for the cell is below min_n (§11.1: an untestable
    check is not a pass)."""
    means = _league_means(cell, pool)
    if means is None or ref_n_local is None or any(ref_n_local.get(c, 0) < min_n for c in pool):
        return {"global_ok": None, "d9_margin": None}
    margin = min(_d9_edge(v, band)[0] for v in means)
    return {"global_ok": margin >= 0, "d9_margin": margin}


def _sign_ok(values: Sequence[float], neutral: float) -> bool:
    """§10.3: leagues with |ρ| < neutral have no sign; the rest must share one."""
    signed = [v for v in values if abs(v) >= neutral]
    return bool(signed) and (all(v > 0 for v in signed) or all(v < 0 for v in signed))


def _majority(values: Sequence[float], trigger: float, leagues: int) -> bool:
    """ADR 004 §13.1: at least `leagues` leagues at |ρ| >= trigger, and those leagues share one
    sign (a league at >= trigger with the other sign blocks it; leagues below are not tested)."""
    hit = [v for v in values if abs(v) >= trigger]
    return len(hit) >= leagues and (all(v > 0 for v in hit) or all(v < 0 for v in hit))


def _majority_margin(values: Sequence[float], neutral: float, leagues: int
                     ) -> tuple[float | None, float | None]:
    """M4g2 O3: (V, opposite). V = the max over the two signs of the `leagues`-th largest |ρ|
    among leagues of that sign (|ρ| >= neutral); opposite = the largest |ρ| of the other sign for
    the sign giving V. (None, None) when neither sign has `leagues` leagues."""
    best: tuple[float | None, float | None] = (None, None)
    for sign in (1, -1):
        same = sorted((abs(v) for v in values if abs(v) >= neutral and v * sign > 0), reverse=True)
        if len(same) >= leagues and (best[0] is None or same[leagues - 1] > best[0]):
            other = [abs(v) for v in values if abs(v) >= neutral and v * sign < 0]
            best = (same[leagues - 1], max(other) if other else None)
    return best


class _Judge:
    """One derive() run: judges each cell and collects the boundary values."""

    def __init__(self, meas: Mapping[str, Any], rules: Rules, allow: Sequence[AllowEntry],
                 dropped: set[tuple[str, str]]) -> None:
        self.meas = meas
        self.rules = rules
        self.pool = [str(c) for c in meas["provenance"]["pool_competition_ids"]]
        self.allow = {(a.column, a.group): a for a in allow}
        self.dropped = dropped
        self.boundary: list[BoundaryCell] = []
        self.majority_admitted: list[MajorityAdmitted] = []

    def d9(self, m: Mapping[str, Any] | None, ref_n_local: Mapping[str, int] | None
           ) -> dict[str, Any]:
        """Per-90s and team_pass_share are ranked against the gated group populations
        (cohorts.ref_n_local); a ratio against its own min_attempts populations."""
        return _d9(m, self.pool, self.rules.global_ok_band, ref_n_local, self.rules.min_cohort_size)

    def near(self, rule: str, col: str, grp: str, value: float, threshold: float,
             per_league: tuple[float, ...] | None = None) -> None:
        if abs(value - threshold) <= self.rules.boundary_margin:
            self.boundary.append(BoundaryCell(rule, grp, col, value, threshold, per_league))

    def possession(self, col: str, grp: str, block: Mapping[str, Any] | None
                   ) -> tuple[Status | None, bool, tuple[str, ...]]:
        """Step 5. Returns (display or None to continue, team_style_biased, reasons)."""
        if block is None:
            raise RegistryRuleError(f"{col} × {grp}: no possession measurement")
        r = self.rules
        means: dict[str, float] = {}
        pers: dict[str, tuple[float, ...]] = {}
        triggered = majority = False
        for proxy in POSSESSION_PROXIES:
            p = block[proxy]
            per = [p["by_league"].get(c) for c in self.pool]
            if p["rho_within_mean"] is None or any(v is None for v in per):
                return "display", False, (R_UNTESTABLE,)
            means[proxy] = p["rho_within_mean"]
            pers[proxy] = tuple(per)
            self.near(f"rho_trigger:{proxy}", col, grp, abs(means[proxy]),
                      r.possession_rho_trigger, tuple(per))
            if abs(means[proxy]) >= r.possession_rho_trigger and _sign_ok(per, r.possession_sign_neutral):
                triggered = True
            # ADR 004 §13.1 (M4g2): the majority rule, an OR with the mean rule, and its two
            # boundary rows (O3, stats-reviewer conditions).
            if _majority(per, r.possession_rho_trigger, r.possession_majority_leagues):
                triggered = majority = True
            v, opposite = _majority_margin(per, r.possession_sign_neutral, r.possession_majority_leagues)
            if v is not None:
                self.near(f"majority_trigger:{proxy}", col, grp, v, r.possession_rho_trigger, tuple(per))
                if v >= r.possession_rho_trigger - r.boundary_margin and opposite is not None:
                    self.near(f"majority_sign:{proxy}", col, grp, opposite, r.possession_rho_trigger,
                              tuple(per))
        if not triggered:
            return None, False, ()
        tag = (R_MAJORITY,) if majority else ()
        entry = self.allow.get((col, grp))
        if entry is None:
            return "display", True, (*tag, R_TEAM_STYLE)
        top = max(abs(v) for v in means.values())
        per = tuple(block[max(POSSESSION_PROXIES, key=lambda p: abs(means[p]))]["by_league"][c]
                    for c in self.pool)
        self.near("allow_bound", col, grp, top, entry.max_rho, per)
        if top > entry.max_rho:   # also checked for every entry in _check_allow_bounds
            raise RegistryRuleError(f"allow-list {col} × {grp}: measured max |ρ| {top:.6f} > "
                                    f"its bound {entry.max_rho}")
        if majority:
            self.majority_admitted.append(MajorityAdmitted(col, grp, tuple(
                MajorityProxy(proxy, pers[proxy],
                              sum(abs(v) >= r.possession_rho_trigger for v in pers[proxy]),
                              max(abs(v) for v in pers[proxy]),
                              _majority(pers[proxy], r.possession_rho_trigger,
                                        r.possession_majority_leagues))
                for proxy in POSSESSION_PROXIES)))
        return None, True, (*tag, R_ALLOW)

    def dna(self, spec: MetricSpec, grp: str, r: float, tsb: bool, reasons: tuple[str, ...],
            **kw: Any) -> Cell:
        return Cell(spec.column, grp, "dna", (*reasons, R_DNA), team_style_biased=tsb,
                    low_reliability=r < self.rules.low_reliability_below, reliability=r, **kw)

    def per90(self, spec: MetricSpec, grp: str) -> Cell:
        m = self.meas["metrics"].get(spec.column, {}).get("groups", {}).get(grp)
        d9 = self.d9(m, self.meas["cohorts"][grp]["ref_n_local"])
        common = dict(**d9, reliability=None if m is None else m["r_full"])
        if spec.fixed_display:
            return Cell(spec.column, grp, "display", (R_FIXED,), **common)
        if (spec.column, grp) in self.dropped:
            return Cell(spec.column, grp, "display", (R_D17,), **common)
        r, zero = m["r_full"], m["zero_share"]
        rules = self.rules
        self.near("zero_share", spec.column, grp, zero, rules.zero_heavy_share)
        if r is not None:
            self.near("r_full", spec.column, grp, r, rules.dna_reliability_min)
        reliable = r is not None and r >= rules.dna_reliability_min
        if zero > rules.zero_heavy_share:
            if reliable:
                return Cell(spec.column, grp, "signature", (R_ZERO_HEAVY,), **common)
            return Cell(spec.column, grp, "display", (R_ZERO_HEAVY, R_UNRELIABLE), **common)
        if not reliable:
            return Cell(spec.column, grp, "display", (R_UNRELIABLE,), **common)
        status, tsb, why = self.possession(spec.column, grp, m["possession"])
        if status:
            return Cell(spec.column, grp, status, why, team_style_biased=tsb, **common)
        return self.dna(spec, grp, r, tsb, why, **d9)

    def ratio(self, spec: MetricSpec, grp: str) -> Cell:
        m = self.meas["ratios"].get(spec.column, {}).get("groups", {}).get(grp)
        d9 = self.d9(m, None if m is None else m["ref_n_local"])
        common = dict(**d9, reliability=None if m is None else m["split_half_r_full"],
                      min_attempts=None if m is None else m["min_attempts"])
        signal = () if m is None or not m["no_signal"] else (R_NO_SIGNAL,)
        if spec.fixed_display:
            return Cell(spec.column, grp, "display", (R_FIXED, *signal), **common)
        if (spec.column, grp) in self.dropped:
            return Cell(spec.column, grp, "display", (R_D17, *signal), **common)
        if signal:
            return Cell(spec.column, grp, "display", signal, **common)
        rules = self.rules
        r, cov = m["split_half_r_full"], m["coverage"]
        if r is not None:
            self.near("ratio_r", spec.column, grp, r, rules.dna_reliability_min)
        self.near("coverage", spec.column, grp, cov, rules.ratio_min_coverage)
        why = ((R_UNRELIABLE,) if r is None or r < rules.dna_reliability_min else ()) + \
              ((R_LOW_COVERAGE,) if cov < rules.ratio_min_coverage else ())
        if why:
            return Cell(spec.column, grp, "display", why, **common)
        status, tsb, why = self.possession(spec.column, grp, m.get("possession"))
        if status:
            return Cell(spec.column, grp, status, why, team_style_biased=tsb, **common)
        return self.dna(spec, grp, r, tsb, why, **d9, min_attempts=m["min_attempts"])

    def share(self, spec: MetricSpec, grp: str) -> Cell:
        """Step 6, team_pass_share (D12b)."""
        m = self.meas["team_pass_share"].get(grp)
        d9 = self.d9(m, self.meas["cohorts"][grp]["ref_n_local"])
        common = dict(**d9, reliability=None if m is None else m["split_half_r_full"])
        if spec.fixed_display:
            return Cell(spec.column, grp, "display", (R_FIXED,), **common)
        if (spec.column, grp) in self.dropped:
            return Cell(spec.column, grp, "display", (R_D17,), **common)
        if m is None:
            return Cell(spec.column, grp, "display", (R_D12B_NOT_REGISTERED,), **common)
        rules = self.rules
        r = m["split_half_r_full"]
        low_rho = True
        for proxy in POSSESSION_PROXIES:
            p = m["possession"][proxy]
            per = [p["by_league"].get(c) for c in self.pool]
            if p["rho_within_mean"] is None or any(v is None for v in per):
                return Cell(spec.column, grp, "display", (R_D12B, R_UNTESTABLE), **common)
            rho = abs(p["rho_within_mean"])
            self.near(f"d12b_rho:{proxy}", spec.column, grp, rho, rules.team_pass_share_max_rho,
                      tuple(per))
            low_rho = low_rho and rho < rules.team_pass_share_max_rho
        if r is not None:
            self.near("d12b_r", spec.column, grp, r, rules.dna_reliability_min)
        if low_rho and r is not None and r >= rules.dna_reliability_min:
            return self.dna(spec, grp, r, False, (R_D12B,), **d9)
        return Cell(spec.column, grp, "display", (R_D12B,), **common)


def _pairs_at(meas: Mapping[str, Any], rho_min: float) -> set[tuple[frozenset[str], str]]:
    return {(frozenset((p["a"], p["b"])), p["group"])
            for p in meas["collinear_pairs"] if abs(p["rho"]) >= rho_min}


def _check_allow_bounds(meas: Mapping[str, Any], specs: Sequence[MetricSpec],
                        allow: Sequence[AllowEntry]) -> None:
    """§10.3: every allow-list entry's measured max |mean ρ| is within its bound."""
    unit = {s.column: s.unit for s in specs}
    for a in allow:
        section = {"per90": "metrics", "ratio": "ratios"}.get(unit[a.column])
        if section is None:
            raise RegistryRuleError(f"allow-list {a.column}: D12 applies to per-90s and ratios")
        block = meas[section].get(a.column, {}).get("groups", {}).get(a.group, {}).get("possession")
        if block is None:
            raise RegistryRuleError(f"allow-list {a.column} × {a.group}: no possession measurement")
        means = [block[p]["rho_within_mean"] for p in POSSESSION_PROXIES]
        if any(v is None for v in means):
            continue   # untestable cells are display at step 5; the entry admits nothing
        top = max(abs(v) for v in means)
        if top > a.max_rho:
            raise RegistryRuleError(f"allow-list {a.column} × {a.group}: measured max |ρ| "
                                    f"{top:.6f} > its bound {a.max_rho}")


def _measurement(meas: Mapping[str, Any], spec: MetricSpec, grp: str) -> Mapping[str, Any] | None:
    if spec.unit == "share":
        return meas["team_pass_share"].get(grp)
    section = "metrics" if spec.unit == "per90" else "ratios"
    return meas[section].get(spec.column, {}).get("groups", {}).get(grp)


def _mover_key(record: Mapping[str, Any], key: str, where: str) -> Any:
    """record[key], or a RegistryRuleError naming where the key is missing."""
    if not isinstance(record, Mapping) or key not in record:
        raise RegistryRuleError(f"movers {where}: missing {key!r}")
    return record[key]


def _at(record: Any, where: str, *keys: str) -> Any:
    """record[k1][k2]…, or a RegistryRuleError naming the cell and the missing path (a stale or
    malformed possession block fails with its location, never a bare KeyError)."""
    for i, key in enumerate(keys):
        if not isinstance(record, Mapping) or key not in record:
            raise RegistryRuleError(f"{where}: missing {'.'.join(keys[:i + 1])!r}")
        record = record[key]
    return record


def _mover_number(record: Mapping[str, Any], key: str, where: str, inf_ok: bool) -> float:
    """A movers-block number. "+inf" is allowed where inf_ok (f̂ and its CI bounds)."""
    x = _mover_key(record, key, where)
    if inf_ok and x == "+inf":
        return math.inf
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise RegistryRuleError(f"movers {where}.{key}: {x!r} is not a finite number")
    return float(x)


def _mover_verdict(lo: float, hi: float, rules: Rules) -> MoverVerdictName:
    """A testable metric's verdict from its CI bounds (ADR 004 §13.7)."""
    if hi < rules.mover_f_threshold:
        return "PASS"
    if lo > rules.mover_f_threshold:
        return "FAIL"
    return "inconclusive"


def _movers(meas: Mapping[str, Any], specs: Sequence[MetricSpec], rules: Rules,
            cells: Mapping[tuple[str, str], Cell]) -> dict[str, MoverVerdict]:
    """ADR 004 §13.7 / plan 005 M4g3b: the verdict and flags of every metric in the movers
    block, from its numbers and the Rules thresholds. Raises RegistryRuleError on a missing
    key, a block written with another minimum n than Rules.mover_min_n, a block that does not
    cover its own metric list, a metric without a spec, a DNA metric without a verdict, or a
    non-finite number."""
    block = meas.get("movers")
    if not isinstance(block, Mapping):
        raise RegistryRuleError("measurements must hold the movers block (plan 005 M4g3b)")
    min_n = _mover_key(block, "min_movers", "block")
    if min_n != rules.mover_min_n:
        raise RegistryRuleError(f"movers block written with min_movers {min_n!r}, but "
                                f"Rules.mover_min_n is {rules.mover_min_n}: re-run the measurements")
    listed = list(_mover_key(block, "metric_list", "block"))
    measured = _mover_key(block, "metrics", "block")
    if sorted(listed) != sorted(measured):
        raise RegistryRuleError(f"movers block: metric_list and metrics differ: "
                                f"{sorted(set(listed) ^ set(measured))}")
    known = {s.column for s in specs}
    unknown = sorted(set(listed) - known)
    if unknown:
        raise RegistryRuleError(f"movers block: {unknown} have no registry spec")
    dna = sorted({col for (col, _), c in cells.items() if c.status == "dna"} - set(listed))
    if dna:
        raise RegistryRuleError(f"movers block: DNA metrics without a verdict: {dna}")
    n_extra = len(_mover_key(_mover_key(block, "stream_keys", "block"), "extra", "block.stream_keys"))
    return {col: _mover_entry(col, measured[col], n_extra, rules) for col in sorted(listed)}


def _mover_entry(col: str, m: Mapping[str, Any], n_extra: int, rules: Rules) -> MoverVerdict:
    """One movers-block entry's verdict and flags (ADR 004 §13.7): a metric's (M4g3b) or an
    adjusted candidate's (M4g4d, the possession block's movers)."""
    n = _mover_key(m, "n", col)
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise RegistryRuleError(f"movers {col}.n: {n!r} is not a count")
    seeds = _mover_key(m, "extra_seeds", col)
    if len(seeds) != n_extra or any(_mover_key(e, "n", f"{col}.extra_seeds") != n for e in seeds):
        raise RegistryRuleError(f"movers {col}: {len(seeds)} extra seeds (expected {n_extra}), "
                                f"each with the primary n {n}")
    if n < rules.mover_min_n:
        if "f_hat" in m or any("ci_hi" in e for e in seeds):
            raise RegistryRuleError(f"movers {col}: untestable (n {n}) but carries f̂")
        return MoverVerdict(col, "untestable", n, extra_seed_verdicts=("untestable",) * n_extra)
    lo = _mover_number(m, "ci_lo", col, True)
    hi = _mover_number(m, "ci_hi", col, True)
    verdict = _mover_verdict(lo, hi, rules)
    extra = tuple(_mover_verdict(_mover_number(e, "ci_lo", f"{col}.extra_seeds[{e.get('k')}]", True),
                                 _mover_number(e, "ci_hi", f"{col}.extra_seeds[{e.get('k')}]", True),
                                 rules)
                  for e in seeds)
    power = _mover_number(m, "power_at_f0", col, False)
    return MoverVerdict(
        col, verdict, n,
        f_hat=_mover_number(m, "f_hat", col, True), ci=(lo, hi),
        r_m=_mover_number(m, "r_m", col, False),
        power_at_f0=power, power_se=_mover_number(m, "power_se", col, False),
        underpowered=power < rules.mover_low_power,
        borderline_seed=any(v != verdict for v in extra),
        below_model_range=verdict == "PASS" and hi < 0,
        extra_seed_verdicts=extra,
        s2_rms_a=_mover_number(m, "s2_rms_a", col, False))   # D7: required on a testable entry


# ── Possession adjustment and the Defending restore (ADR 004 §13.8; plan 005 M4g4d) ─────────

def _check_engine_rules(block: Mapping[str, Any], rules: Rules) -> None:
    """The engines' rule copies equal Rules (the M4g4 STOP rule item): the stored selections
    under resampling ((a), (b), (d)) were made with them and cannot be re-derived here."""
    want = {("rules", "rho_max"): rules.possession_criterion_rho_max,
            ("rules", "sign_dead_zone"): rules.possession_sign_dead_zone,
            ("rules", "inconsistent"): rules.possession_inconsistent_rho,
            ("rules", "gap_max"): rules.possession_gap_max,
            ("rules", "r_full_min"): rules.dna_reliability_min,
            ("trust_rules", "stab_min"): rules.trust_stability_min,
            ("trust_rules", "lolo_min"): rules.trust_lolo_min_agree,
            ("trust_rules", "p_max"): rules.trust_p_max,
            ("trust_rules", "d_min"): rules.trust_d_min_count}
    for (sec, key), v in want.items():
        got = _mover_key(_mover_key(block, sec, "possession"), key, f"possession.{sec}")
        if got != v:
            raise RegistryRuleError(f"possession.{sec}.{key} is {got!r}, but Rules has {v!r}: "
                                    f"re-run the M4g4c engines with the current rules")


def _candidate_check(name: str, rec: Mapping[str, Any], ref: tuple[float, float] | None,
                     rules: Rules, where: str) -> CandidateCheck:
    if rec.get("fit_failed"):
        if name != "beta_hat":
            raise RegistryRuleError(f"{where} {name}: only β̂ can fail its fit")
        return CandidateCheck(name, None, False, None, False, None, False, False, False, False,
                              fit_failed=True)
    w = f"{where} {name}"
    c1 = tuple(float(_at(rec, w, "c1_rho", p, "mean")) for p in POSSESSION_PROXIES)
    c2 = tuple(float(_at(rec, w, "c2_gap", p, "gap")) for p in POSSESSION_PROXIES)
    r3 = float(_at(rec, w, "c3_r_full"))
    c1_pass = all(abs(v) < rules.possession_criterion_rho_max for v in c1)
    c2_pass = all(v <= rules.possession_gap_max for v in c2)
    c3_pass = r3 >= rules.dna_reliability_min
    dz = rules.possession_sign_dead_zone
    ref = c1 if ref is None else ref        # β = 0 is its own sign reference
    flip = any(abs(r) >= dz and abs(v) >= dz and math.copysign(1, v) != math.copysign(1, r)
               for v, r in zip(c1, ref))
    incons = any(abs(float(v)) >= rules.possession_inconsistent_rho
                 for p in POSSESSION_PROXIES for v in _at(rec, w, "c1_rho", p, "by_league").values())
    return CandidateCheck(name, c1, c1_pass, c2, c2_pass, r3, c3_pass, flip, incons,  # type: ignore[arg-type]
                          c1_pass and c2_pass and c3_pass and not flip)


def _trust_check(t: Mapping[str, Any], selected: str, beta0: CandidateCheck, rules: Rules,
                 where: str) -> TrustCheck:
    w = f"{where} trust"
    a = float(_at(t, w, "a", "p_same"))
    agree = sum(v == selected for v in _at(t, w, "b", "selected_without").values())
    judged = tuple(k for k, failed in (("rho", not beta0.c1_pass), ("gap", not beta0.c2_pass))
                   if failed)
    p = (float(_at(t, w, "c", "p_rho")), float(_at(t, w, "c", "p_gap")))
    c_pass = all(dict(zip(("rho", "gap"), p))[k] < rules.trust_p_max for k in judged)
    if selected == "beta_hat":
        if _at(t, w, "d") is None:
            raise RegistryRuleError(f"{w}: (d) is required for a β̂ selection")
        d_pass: bool | None = _at(t, w, "d", str(rules.trust_d_min_count), "selected") == "beta_hat"
    else:
        d_pass = None
    a_pass, b_pass = a >= rules.trust_stability_min, agree >= rules.trust_lolo_min_agree
    return TrustCheck(a, a_pass, agree, b_pass, judged, p, c_pass, d_pass,
                      a_pass and b_pass and c_pass and d_pass is not False)


def _possession(meas: Mapping[str, Any], specs: Sequence[MetricSpec], rules: Rules,
                cells: Mapping[tuple[str, str], Cell]) -> dict[tuple[str, str], PossessionAdjustment]:
    """ADR 004 §13.8: every Defending DNA cell's candidates, selection, trust check and the
    column the vector uses. Raises RegistryRuleError when the block is missing, covers other
    cells, was made with other rules, or selects differently from the rules here, and when a
    cell would enter the vector adjusted (not implemented: M4g4c2 left none)."""
    block = meas.get("possession")
    if not isinstance(block, Mapping):
        raise RegistryRuleError("measurements must hold the possession block (schema 3, plan 005 M4g4d)")
    _check_engine_rules(block, rules)
    if tuple(_mover_key(block, "candidates", "possession")) != POSSESSION_CANDIDATES:
        raise RegistryRuleError(f"possession candidates {block['candidates']} are not "
                                f"{POSSESSION_CANDIDATES}")
    by_col = {s.column: s for s in specs}
    want = {(c, g) for (c, g), cell in cells.items()
            if cell.status == "dna" and by_col[c].family == "Defending"}
    stored = _mover_key(block, "cells", "possession")
    got = {(c, g) for c, gs in stored.items() for g in gs}
    if got != want:
        raise RegistryRuleError(f"possession cells differ from the Defending DNA cells: "
                                f"{sorted(got ^ want)}")
    trust = _mover_key(block, "trust", "possession")
    out: dict[tuple[str, str], PossessionAdjustment] = {}
    for col, grp in sorted(want):
        rec = stored[col][grp]
        where = f"possession {col} × {grp}"
        directional = by_col[col].direction != "style"
        if _at(rec, where, "directional") != directional:
            raise RegistryRuleError(f"{where}: directional {rec['directional']} ≠ the spec's")
        beta0 = _candidate_check(RAW_CANDIDATE, _at(rec, where, "candidates", RAW_CANDIDATE), None,
                                 rules, where)
        checks = (beta0, *(_candidate_check(k, _at(rec, where, "candidates", k), beta0.c1_mean_rho,
                                            rules, where)
                           for k in POSSESSION_CANDIDATES[1:]))
        if _at(rec, where, "zero_teams"):
            checks = tuple(replace(c, passes=False) if c.candidate == "beta_hat" else c
                           for c in checks)
        selected = next((c.candidate for c in checks if c.passes), None)
        if selected != _at(rec, where, "spike_selected"):
            raise RegistryRuleError(f"{where}: the rules select {selected!r}, the engine "
                                    f"{rec['spike_selected']!r}")
        t = trust.get(col, {}).get(grp)
        if selected in (None, RAW_CANDIDATE):
            if t is not None:
                raise RegistryRuleError(f"{where}: a trust record for selection {selected!r}")
            tc = None
            applied = RAW_CANDIDATE
        else:
            if t is None:
                raise RegistryRuleError(f"{where}: selection {selected!r} has no trust record")
            tc = _trust_check(t, selected, beta0, rules, where)
            applied = selected if tc.passes else RAW_CANDIDATE
        if applied != RAW_CANDIDATE:
            raise RegistryRuleError(f"{where}: enters the vector adjusted ({applied}); adjusted "
                                    f"columns are not implemented (M4g4c2 left none)")
        bh = _at(rec, where, "beta_hat")
        flags = (*(("rises with own possession",) if bh is not None and bh < 0 else ()),
                 *(("|β̂| > 1",) if bh is not None and abs(bh) > 1 else ()))
        ci = rec.get("beta_hat_ci")
        out[(col, grp)] = PossessionAdjustment(
            col, grp, directional, bh, None if ci is None else (float(ci[0]), float(ci[1])),
            checks, selected, tc, applied, flags)
    return out


def _restore_candidate(p: PossessionAdjustment) -> str | None:
    """The candidate a directional member's restore is judged on: its selection if that is β = 0
    or passed the trust check; None when no candidate is trusted (possession criteria failed)."""
    if p.selected is None:
        return None
    if p.selected == RAW_CANDIDATE or (p.trust is not None and p.trust.passes):
        return p.selected
    return None


def _member_status(v: MoverVerdict) -> str:
    """D5 for a member with a trusted candidate: the mover verdict, then seed sensitivity
    (D6), then below model range (D7: √s2_rms_a > 1)."""
    if v.verdict == "untestable":
        return MOVER_STABILITY_UNTESTABLE
    if v.verdict == "FAIL":
        return MOVER_STABILITY_FAILED
    if v.verdict == "inconclusive":
        return MOVER_STABILITY_INCONCLUSIVE
    if v.borderline_seed:
        return MOVER_SEED_SENSITIVE
    if v.below_model_range:
        if v.s2_rms_a is None:
            raise RegistryRuleError(f"{v.column}: PASS below model range without s2_rms_a (D7)")
        if math.sqrt(v.s2_rms_a) > 1:
            return MOVER_BELOW_MODEL_RANGE
    return SCORE_OK


def _candidate_movers(meas: Mapping[str, Any], rules: Rules, movers: Mapping[str, MoverVerdict],
                      possession: Mapping[tuple[str, str], PossessionAdjustment]
                      ) -> dict[tuple[str, str], CandidateMover]:
    """Mover verdicts per (metric, candidate): β = 0 reuses the M4g3b verdict (not re-run);
    the adjusted candidates come from the possession block's movers (D3).

    The block holds the runs of one metric (2015/16: ball_recoveries_p90, the only directional
    Defending DNA member in any group, ADR 004 §13.8). A second directional member needs its own
    runs, so it is refused here rather than left without a verdict."""
    block = _at(meas, "possession", "possession", "movers")
    col = _mover_key(block, "metric", "possession.movers")
    others = sorted({c for (c, _), p in possession.items() if p.directional and c != col})
    if others:
        raise RegistryRuleError(f"possession.movers holds {col} only, but {others} are directional "
                                f"Defending members: their candidate mover runs are missing")
    n_extra = len(_mover_key(_mover_key(block, "stream_keys", "possession.movers"), "extra",
                             "possession.movers.stream_keys"))
    if n_extra != len(meas["movers"]["stream_keys"]["extra"]):
        raise RegistryRuleError("possession.movers: extra streams differ from the movers block's")
    runs = _mover_key(block, "candidates", "possession.movers")
    if set(runs) != set(POSSESSION_CANDIDATES[1:]):
        raise RegistryRuleError(f"possession.movers candidates {sorted(runs)}")
    if col not in movers:
        raise RegistryRuleError(f"possession.movers {col}: no M4g3b verdict to reuse for β = 0")
    verdicts = {RAW_CANDIDATE: movers[col],
                **{k: _mover_entry(f"{col}[{k}]", runs[k], n_extra, rules)
                   for k in POSSESSION_CANDIDATES[1:]}}
    used: dict[str, list[str]] = {k: [] for k in POSSESSION_CANDIDATES}
    for (c, g), p in possession.items():
        cand = _restore_candidate(p)
        if c == col and p.directional and cand is not None:
            used[cand].append(g)
    return {(col, k): CandidateMover(col, k, verdicts[k],
                                     tuple(sorted(used[k], key=GROUPS.index)))
            for k in POSSESSION_CANDIDATES}


def _defending_status(possession: Mapping[tuple[str, str], PossessionAdjustment],
                      cand_movers: Mapping[tuple[str, str], CandidateMover]
                      ) -> dict[tuple[str, str], FamilyStatus]:
    """ADR 004 §13.8 D5, per group, over its directional Defending members: restored ("ok") if
    any member passes; otherwise the status of the member that got furthest along the
    precedence (possession → mover verdict → seed sensitivity → below model range), ties to
    the first metric name. "underpowered at n" is a note. A group without a directional
    member has no status to restore from: "ok" (the dimension score is no_directional_metric)."""
    out: dict[tuple[str, str], FamilyStatus] = {}
    for grp in GROUPS:
        members = sorted(c for (c, g), p in possession.items() if g == grp and p.directional)
        best: FamilyStatus | None = None
        for col in members:
            cand = _restore_candidate(possession[(col, grp)])
            if cand is None:
                fs = FamilyStatus("Defending", grp, POSSESSION_CRITERIA_FAILED, col, None)
            else:
                cm = cand_movers.get((col, cand))
                if cm is None:
                    raise RegistryRuleError(f"Defending {grp}: {col} under {cand} has no mover verdict")
                v = cm.verdict
                notes = (f"underpowered at {v.n}",) if v.underpowered else ()
                fs = FamilyStatus("Defending", grp, _member_status(v), col, cand, notes)
            if best is None or _D5_STAGE[fs.status] > _D5_STAGE[best.status]:
                best = fs
        out[("Defending", grp)] = best or FamilyStatus("Defending", grp, SCORE_OK, None, None)
    return out


def _inf_as_text(x: Any) -> Any:
    """+inf / -inf as "+inf" / "-inf", the movers block's spelling (also in tools/registry_snapshot)."""
    if isinstance(x, float) and math.isinf(x):
        return "+inf" if x > 0 else "-inf"
    if isinstance(x, (list, tuple)):
        return [_inf_as_text(v) for v in x]
    if isinstance(x, dict):
        return {k: _inf_as_text(v) for k, v in x.items()}
    return x


def _version(cells: Mapping[tuple[str, str], Cell], meas: Mapping[str, Any], rules: Rules,
             specs: Sequence[MetricSpec], movers: Mapping[str, MoverVerdict],
             possession: Mapping[tuple[str, str], PossessionAdjustment] | None = None,
             cand_movers: Mapping[tuple[str, str], CandidateMover] | None = None,
             family_status: Mapping[tuple[str, str], FamilyStatus] | None = None) -> str:
    """sha256 of the measurements, the rules, every spec field and the resolved cells. The
    spike rounds every stored float to 9 decimals, so reruns on the same data write identical
    bytes and the version changes only when a measurement, a rule, a spec (e.g. a direction or
    a ratio term, ADR 004 §12.4) or an entry does."""
    canonical = {
        "measurements": meas,
        "rules": asdict(rules),
        "specs": [asdict(s) for s in sorted(specs, key=lambda s: s.column)],
        "cells": [asdict(cells[k]) for k in sorted(cells)],
        "movers": [_inf_as_text(asdict(movers[c])) for c in sorted(movers)],
        "possession": [asdict(v) for _, v in sorted((possession or {}).items())],
        "candidate_movers": [_inf_as_text(asdict(v)) for _, v in sorted((cand_movers or {}).items())],
        "family_status": [asdict(v) for _, v in sorted((family_status or {}).items())],
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def derive(specs: Sequence[MetricSpec] = SPECS,
           measurements: Mapping[str, Any] | None = None,
           rules: Rules = Rules(),
           allow: Sequence[AllowEntry] = POSSESSION_ALLOW_LIST,
           collinear: Sequence[CollinearChoice] = COLLINEAR_CHOICES,
           overrides: Sequence[Override] = OVERRIDES) -> Derivation:
    """Every metric × group cell and the §10.10 boundary report. Raises RegistryRuleError."""
    meas = load_measurements() if measurements is None else measurements
    source = meas["provenance"]["source"]
    if not isinstance(source, str) or not source:
        raise RegistryRuleError("measurements must name exactly one source")
    _validate_specs(specs, meas)
    _validate_entries(specs, allow, collinear, overrides, rules)
    _check_allow_bounds(meas, specs, allow)

    measured_pairs = _pairs_at(meas, rules.collinear_rho)
    for c in collinear:
        for grp in c.groups:
            if (frozenset((c.kept, c.dropped)), grp) not in measured_pairs:
                raise RegistryRuleError(f"D17 choice {c.kept} over {c.dropped} in {grp}: no measured "
                                        f"pair at |ρ| ≥ {rules.collinear_rho}")
    dropped = {(c.dropped, grp) for c in collinear for grp in c.groups}

    judge = _Judge(meas, rules, allow, dropped)
    by_unit = {"per90": judge.per90, "ratio": judge.ratio, "share": judge.share}
    cells: dict[tuple[str, str], Cell] = {}
    for spec in specs:
        for grp in spec.applies_to:
            cells[(spec.column, grp)] = by_unit[spec.unit](spec, grp)

    for o in overrides:
        derived = cells[(o.column, o.group)]
        if derived.status == o.status:
            raise RegistryRuleError(f"override {o.column} × {o.group}: no-op (derived status is "
                                    f"already {o.status})")
        # The override replaces the derived reasons with its status and ADR reference (M4e shows
        # reasons), not the rule outcome it bypasses.
        cells[(o.column, o.group)] = replace(
            derived, status=o.status, reasons=(o.status, f"{R_OVERRIDE}: {o.adr}"),
            low_reliability=derived.low_reliability and o.status == "dna")

    specs_by_col = {s.column: s for s in specs}
    for (col, grp), c in cells.items():
        spec = specs_by_col[col]
        if c.status == "dna" and (col in GOALS_AND_ASSISTS or col.startswith(RESTART_PREFIX)):
            raise RegistryRuleError(f"{col} × {grp} is DNA: goals, assists and restart columns "
                                    f"never are (plan 005; ADR 004 §8)")
        notes = (*spec.notes,
                 *((NOTE_TEAM_STYLE,) if c.team_style_biased and c.status == "dna" else ()),
                 *((NOTE_LOWER_RELIABILITY,) if c.low_reliability else ()))
        min_count = None
        if c.status == "signature":
            if not spec.integer_count:
                raise RegistryRuleError(f"{col} × {grp}: a signature trait needs an integer count "
                                        f"column ({spec.count_column} is an xG sum, §10.2)")
            p90 = meas["metrics"][col]["groups"][grp]["count_p90"]
            min_count = max(rules.signature_min_count_floor, p90)
        cells[(col, grp)] = replace(c, notes=notes, signature_min_count=min_count,
                                    basis="global" if c.global_ok else "local")

    for c in collinear:
        for grp in c.groups:
            if cells[(c.kept, grp)].status != "dna":
                raise RegistryRuleError(f"D17 choice keeps {c.kept} in {grp}, but it is "
                                        f"{cells[(c.kept, grp)].status}: the drop would remove both")
    for pair, grp in measured_pairs:
        a, b = sorted(pair)
        if (a, grp) in cells and (b, grp) in cells and \
                cells[(a, grp)].status == cells[(b, grp)].status == "dna":
            raise RegistryRuleError(f"{a} ~ {b} in {grp} at |ρ| ≥ {rules.collinear_rho}: both DNA "
                                    f"without a D17 choice")

    boundary = list(judge.boundary)
    for (col, grp), c in cells.items():
        if c.status != "dna":
            continue
        if c.reliability is not None and \
                abs(c.reliability - rules.low_reliability_below) <= rules.boundary_margin:
            boundary.append(BoundaryCell("lower_reliability", grp, col, c.reliability,
                                         rules.low_reliability_below, None))
        # D9 (user, 2026-10-05): DNA cells whose basis is within sampling noise of flipping.
        if c.d9_margin is not None and abs(c.d9_margin) <= rules.d9_boundary_margin_pts:
            means = _league_means(_measurement(meas, specs_by_col[col], grp), judge.pool)
            nearest = min(means, key=lambda v: _d9_edge(v, rules.global_ok_band)[0])
            boundary.append(BoundaryCell("d9_band", grp, col, nearest,
                                         _d9_edge(nearest, rules.global_ok_band)[1], means))
    resolved = tuple(sorted(
        (replace(b, status=cells[(b.column, b.group)].status,
                 team_style_biased=cells[(b.column, b.group)].team_style_biased,
                 low_reliability=cells[(b.column, b.group)].low_reliability)
         for b in boundary),
        key=lambda b: (b.rule, GROUPS.index(b.group), b.column)))
    admitted = tuple(sorted((a for a in judge.majority_admitted
                             if cells[(a.column, a.group)].status == "dna"),
                            key=lambda a: (GROUPS.index(a.group), a.column)))
    movers = _movers(meas, specs, rules, cells)
    possession = _possession(meas, specs, rules, cells)
    cand_movers = _candidate_movers(meas, rules, movers, possession)
    family_status = _defending_status(possession, cand_movers)
    return Derivation(cells=cells, boundary=resolved, source=source,
                      version=_version(cells, meas, rules, specs, movers, possession, cand_movers,
                                       family_status),
                      majority_admitted=admitted, movers=movers, possession=possession,
                      candidate_movers=cand_movers, family_status=family_status)


# ── Resolved at import ──────────────────────────────────────────────────────────

REGISTRY: Derivation = derive()
REGISTRY_VERSION: str = REGISTRY.version
MEASUREMENT_SOURCE: str = REGISTRY.source


def cell(column: str, group: str) -> Cell:
    return REGISTRY.cell(column, group)


def dna_metrics(group: str) -> tuple[str, ...]:
    return REGISTRY.dna_metrics(group)


def display_metrics(group: str) -> tuple[str, ...]:
    return REGISTRY.display_metrics(group)


def signature_traits(group: str) -> tuple[str, ...]:
    return REGISTRY.signature_traits(group)


def is_signature_holder(cell: Cell, season_count: float | None, row_status: str) -> bool | None:
    """ADR 004 §1: does a row hold the signature trait `cell`?

    A gated row (row_status "scored": pool, outfield, gate) holds it when its season count
    of the metric is >= the cell's minimum (max(5, the group's P90), §10.2); the D19
    reliability bar is already in the cell's status. Any other row is not assessed: None.
    A null count on a gated row raises, as does a cell that is not a signature trait.
    """
    if cell.status != "signature" or cell.signature_min_count is None:
        raise ValueError(f"{cell.column} × {cell.group} is not a signature trait ({cell.status})")
    if row_status != "scored":
        return None
    if season_count is None:
        raise ValueError(f"{cell.column} × {cell.group}: a gated row has no season count")
    return season_count >= cell.signature_min_count
