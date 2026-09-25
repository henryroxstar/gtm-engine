from __future__ import annotations

from ..prospects_state import ACCOUNT_ID_FIELD
from ..signal_record import JUDGE_COLUMNS, RECORD_COLUMNS, SIGNAL_COLUMN

# --- schema -----------------------------------------------------------------

MASTER_COLS = [
    "first",
    "last",
    "email",
    "title",
    "company",
    "company_domain",
    "city",
    "country",
    "segment",
    "tier",
    "score",
    "conf",
    "email_status",
    "why_now",
    "case_study",
    "src",
    "conf_tier",
    # Buyer-intent / ICP-cohort signal, carried through from the hubspot CSV so it
    # survives consolidation instead of being dropped. Appended (not inserted) to
    # preserve column-position compatibility with anything reading by index.
    "heat",
    "top_intent_score",
    "intent_topics",
    "cohort",
    "qualification_path",
    # The research record (:mod:`gtm_core.signal_record`) — provenance for the Why Now
    # clause plus the send/re-angle/drop verdict. Appended for the same reason as the
    # intent block above: the projection at `_read_master` is a hard `{c: r.get(c)}`
    # over this list, so a column absent from it is silently dropped on the next sweep.
    # The clause used to be the row's entire claim to being researched, which made a
    # renamed company, a fact about the wrong entity, and a number that had drifted
    # from its source all undetectable by construction.
    *RECORD_COLUMNS,
    # The matrix SIGNAL this row's own why-now attests (:data:`gtm_core.signal_record.
    # SIGNAL_COLUMN`) -- appended for the identical reason as the record block above: the
    # `_load_master` projection is a hard `{c: r.get(c)}` over this list, so a column absent
    # from it is silently dropped on the next sweep. Not derived here -- `gtm_core.
    # hook_coverage.derive_row_cell` is the one place a row's persona x segment x signal
    # becomes a cell, and it reads this column plus `title`/`segment`, both already above.
    SIGNAL_COLUMN,
    # The suppression cache (:mod:`gtm_core.suppression`). Absent from this list until
    # 2026-08-27, which is precisely why every sweep destroyed it: `suppression apply`
    # wrote the two columns, the next `_load_master` projection dropped them, and the
    # exclusions survived only in the ledger — where nothing in the build was reading
    # them. That made "re-run apply after every consolidate" a step a human had to
    # remember, and on 2026-08-11 nobody did: 55 exclusions were wiped inside an hour.
    # Carrying them here plus stamping from the ledger below makes it structural.
    "suppression",
    "suppression_date",
    # The judge's own verdict columns (:data:`gtm_core.signal_record.JUDGE_COLUMNS`),
    # appended for the same projection reason as every block above. Without them the
    # judged CSV was an orphan: `email-quality judge` wrote verdicts into its own output
    # file while the enrollment gate read `ready-to-load.csv`, which consolidate
    # regenerates — so nothing carried a judgment from where it was made to where it was
    # enforced. Kept separate from `verdict`, which is the researcher's.
    *JUDGE_COLUMNS,
    # The lane this row was routed into (:mod:`gtm_core.lanes`), appended for the same
    # projection reason as every block above — `_load_master` is a hard `{c: r.get(c)}`
    # over this list, so a column absent from it is dropped on the next sweep.
    #
    # This one was absent, and the cost was that the routing decision could not be seen
    # where it is enforced. `lanes route` wrote its assignment to `evals/lanes-state.jsonl`
    # while `account_integrity --lane` reads a `lane` COLUMN on the CSV and refuses a list
    # whose column disagrees with the lane it is being enrolled into. On 2026-09-04 the
    # pool had 592 rows routed — 363 of them `generic`, admissible on `send`/`re-angle`/
    # blank — and `ready-to-load.csv` carried no `lane` column at all, so a run reading
    # only the load file concluded the pipeline had two sendable rows.
    #
    # `lane` is NOT a second `tier`: tier is the rubric score band (A >= 8, B >= 6) and
    # stays comparable across segments; lane is which BODY the row can carry. A high-fit
    # account with no usable clause is `tier: A, lane: generic` — the router's own most
    # common trigger for it is `tier-a-generic`.
    "lane",
    "lane_reason",
    # Stamped identity, appended last. `pool_row_id` names this person-row; `account_id`
    # is
    # the account's id from latest.json, the ledger of record. Six mutually
    # non-derivable identity derivations existed across this pipeline, so joining two
    # files meant re-deriving a key from fields one of them might not carry — which is
    # how a prospect's status, suppression, and research ended up under keys that could
    # not find each other. The derivations still ASSIGN identity; these carry it.
    #
    # Named `pool_row_id`, NOT `row_id`: `gtm_core.adjudication.Adjudication.row_id` is
    # already an eval-sheet identity at a different grain — sha256(spec|csv|email|touch),
    # one per TOUCH. Two different facts under one name is the defect class this whole
    # change is about, so the pool's row identity gets its own name.
    "pool_row_id",
    ACCOUNT_ID_FIELD,
    # The account's industry classification, carried from `latest.json` (2026-09-24). A
    # firmographic fact, never free text: `premise-vocab.toml` may name `industry_terms` a
    # premise is attested by on this column alone — a commercial bank is a regulated entity
    # whether or not a sentence of research says so — and the generic lane has no event to
    # attest from. Appended, for the projection reason every block above records.
    "industry",
    # The date research set the verdict beside it (2026-09-25). Carried from the account
    # WITH its verdict and never on its own, so it always dates the verdict the row holds;
    # `consolidate` lifts a row's `re-angle` to the account's `send` only when the account's
    # stamp is newer than this one. Not in `RECORD_COLUMNS`, for the reason `signal_column`
    # is not: that tuple decides whether a list carries the record at all.
    "verdict_on",
]

# Canonical field -> header variants seen across hubspot exports + the flat
# master-list schema. The GTM_* prefix varies by which pack generated the run.
_ALIASES = {
    "first": ("First Name", "first"),
    "last": ("Last Name", "last"),
    "name": ("Contact Name",),  # split into first/last when those are absent
    "email": ("Email", "Contact Email", "email"),
    "title": ("Job Title", "Contact Title", "title"),
    "company": ("Company Name", "Company", "company"),
    "company_domain": ("Company Domain Name", "company_domain"),
    "city": ("City", "HQ City", "city"),
    "country": ("Country/Region", "Market", "country"),
    "segment": ("GTM_Segment", "Segment", "segment"),
    "tier": ("GTM_Tier", "Tier", "tier"),
    "score": ("GTM_Score", "Score", "Lead Score", "score"),
    "conf": ("conf",),
    "email_status": ("Email Status", "email_status"),
    "why_now": ("GTM_Why_Now", "Why Now", "why_now"),
    "case_study": ("GTM_Case_Study", "Case Study", "case_study"),
    "heat": ("GTM_Heat", "Heat", "heat"),
    "top_intent_score": ("GTM_Top_Intent_Score", "top_intent_score"),
    "intent_topics": ("GTM_Intent_Topics", "Intent Topics", "intent_topics"),
    "cohort": ("GTM_Persona_Tier", "Cohort", "cohort"),
    "qualification_path": ("GTM_Qualification_Path", "Qualification Path", "qualification_path"),
    "industry": ("GTM_Industry", "Industry", "industry"),
    # The research record + the researcher's verdict. Absent from this map until
    # 2026-08-27, which made the documented handoff impossible: `prospect` is told to
    # write the record into its export, `_get` returns "" for any field with no alias,
    # and the row mapping therefore produced 22 of the 38 master columns. The record
    # could only ever reach the pool by being written straight into master-list.csv, so a
    # run that followed the skill exactly still produced the "list predates the record
    # columns" file-level ERROR at the enrollment gate.
    "signal_source_url": ("GTM_Signal_Source_URL", "Signal Source URL", "signal_source_url"),
    "signal_observed": ("GTM_Signal_Observed", "Signal Observed", "signal_observed"),
    "signal_evidence": ("GTM_Signal_Evidence", "Signal Evidence", "signal_evidence"),
    "signal_subject": ("GTM_Signal_Subject", "Signal Subject", "signal_subject"),
    "signal_agent_kind": ("GTM_Signal_Agent_Kind", "Signal Agent Kind", "signal_agent_kind"),
    "category_relation": ("GTM_Category_Relation", "Category Relation", "category_relation"),
    "verdict": ("GTM_Verdict", "Verdict", "verdict"),
    "verdict_reason": ("GTM_Verdict_Reason", "Verdict Reason", "verdict_reason"),
    SIGNAL_COLUMN: ("GTM_Signal_Column", "Signal Column", SIGNAL_COLUMN),
}


def column_value(row: dict, field: str) -> str:
    """The first non-empty header variant of ``field`` present in ``row``, or ``""``.

    The one reader of :data:`_ALIASES`. It lived as a private ``_get`` in
    :mod:`gtm_core.prospects_consolidate.confidence`, which meant every other module that
    needed a header-agnostic read either imported a private symbol across subpackages or
    hardcoded one spelling. ``email_campaign_dashboard.roster`` did the latter — it reads
    ``GTM_Tier``/``GTM_Score``/``GTM_Why_Now`` directly, so an export written under an
    earlier column prefix renders a whole campaign's roster as blank tier, blank score
    and no signal,
    **silently**, and the "carry a dated why-now" tile reports 0 for a campaign that has them.

    Fail-quiet by design: a field with no alias returns ``""`` rather than raising, the same
    contract the callers already depend on.
    """
    for key in _ALIASES.get(field, ()):
        v = (row.get(key) or "").strip()
        if v:
            return v
    return _renamed_prefix(row, _ALIASES.get(field, ()))


def _renamed_prefix(row: dict, aliases: tuple[str, ...]) -> str:
    """The same column under a DIFFERENT prefix — an export written before a prefix rename.

    Matched by SHAPE rather than enumerated, and that is a tenant-boundary decision rather
    than a convenience. A column prefix is one tenant's spelling of its own export history;
    writing the old one into the engine as a literal puts tenant data into company-agnostic
    code, which the release carve refuses outright — ``scripts/oss-export.sh`` sweeps for
    exactly that shape and ``tests/lint/carve_surface_check.py`` fails CI on it. Enumerating
    prefixes would also have to be redone at the next rename, in a file nobody would think
    to look in.

    So a header matches when its suffix matches a canonical alias's suffix:
    ``<anything>_Why_Now`` reads as ``why_now``. Reached only after every explicit alias came
    back empty, so a row spelling the column the current way never touches this path.
    """
    wanted = {a.split("_", 1)[1].lower() for a in aliases if "_" in a}
    if not wanted:
        return ""
    for key, val in row.items():
        k = str(key or "").strip()
        if "_" in k and k.split("_", 1)[1].lower() in wanted:
            v = str(val or "").strip()
            if v:
                return v
    return ""


#: Columns a source export cannot set, with the reason. Everything else in
#: :data:`MASTER_COLS` is importable and appears in the generated CSV map.
_ASSIGNED_COLUMNS = {
    "src": "set from the export's own filename",
    "conf_tier": "derived from email_status/conf by `classify_confidence`",
    "suppression": "re-derived from the suppression ledger on every sweep",
    "suppression_date": "re-derived from the suppression ledger on every sweep",
    "judge_verdict": "written by the judge; an input claiming to be judged is not",
    "judge_verdict_reason": "written by the judge",
    "judge_calibrated": "written by the judge, from the profile's sealed holdouts",
    "judge_defect_class": "written by the judge — the normalised defect class (routing key)",
    "pool_row_id": "stamped once by consolidate; never supplied",
    ACCOUNT_ID_FIELD: "stamped by latest.json, joined here; never supplied",
    "verdict_on": "the account's research date for its verdict, carried with it; never supplied",
}

#: One-line meaning per column, for the generated map. A column with no entry still
#: renders — the sync test below fails on a MISSING column, never on a missing note,
#: because a schema that silently omits a gate-critical column is the defect this
#: generator exists to prevent.
_COLUMN_NOTES = {
    "first": "given name; a blank one is held back by the merge-field gate",
    "last": "family name",
    "email": "the dedup key for the export; blank if unverified, never guessed",
    "title": "job title, as the source gave it",
    "company": "company name, cleaned of research-note artifacts",
    "company_domain": "the company's own domain, e.g. `example.com`",
    "city": "HQ city — cross-examined against `country` by the compliance gate",
    "country": "HQ country, full name",
    "segment": (
        "one of `gtm_core.merge_hygiene.SEGMENTS`, lowercase (`clean_segment` runs on every "
        "load since 2026-09-24) — read the tuple, never a list restated here (it gained "
        "`builder` on 2026-09-04 and this note did not)"
    ),
    "tier": (
        "`A` / `B` only, never a third letter. Tier is the effort allocation — Tier-A earns "
        "a 1:1 pack, Tier-B the merge sequence — and BOTH are sent, so a `C` is not a "
        "lower tier, it is a row nobody will pick up. One appeared on 78 rows of the "
        "2026-07-24 bulk run, describing the bottom of a `score` column that was itself on "
        "the wrong scale"
    ),
    "score": (
        "the per-account QUALIFICATION verdict from the profile's `icp-personas.md` rubric "
        "— an integer 0-`QUALIFICATION_SCORE_MAX`, heat capped AT the ceiling rather than "
        "added above it. NOT a spend ranking: the enrichment queue's `icp_backlog_score` is "
        "a different scorer on a 0-53 scale and must never land here. `merge_hygiene."
        "check_row` warns `score-out-of-range` outside that band. This note read 'numeric, "
        "no denominator' until 2026-09-04, which is precisely why 749 published rows carry "
        "a spend ranking in a verdict column"
    ),
    "conf": "source-reported confidence, if any",
    "email_status": "deliverability signal (RocketReach grade, `verified`, ...)",
    "why_now": "the research note behind the opener",
    "case_study": "the matched reference customer",
    "heat": "intent heat, 0-3",
    "top_intent_score": "raw 0-100 topic-intent score behind `heat`",
    "intent_topics": "`topic:score` pairs, highest first, semicolon-joined",
    "cohort": "ICP cohort this account was scored under",
    "qualification_path": "set only when a gate was relaxed for the run",
    "industry": (
        "the account's industry classification from `latest.json`, a firmographic fact "
        "`premise-vocab.toml` may attest a premise from (`industry_terms`); blank when the "
        "ledger has none"
    ),
    "signal_source_url": "primary source for the why-now — https, re-fetchable",
    "signal_observed": "ISO date the source showed it (freshness lives here, not in the clause)",
    "signal_evidence": "verbatim span of the source the clause reduces",
    "signal_subject": "the entity the fact is ABOUT; not this company means the row is wrong",
    "signal_agent_kind": "`ai` | `human` | `none` | `unclear` — whose 'agents' the clause means",
    "category_relation": "`prospect` | `competitor` | `partner` | `adjacent` | `regulator`",
    "verdict": "`send` | `re-angle` | `drop` — the RESEARCHER's, never machine-overwritten",
    "verdict_reason": "required for any non-send verdict",
    SIGNAL_COLUMN: "the hook-matrix signal this row's own why-now attests",
    "lane": (
        "one of `personalised` / `repair` / `generic` / `hold` / `excluded` — which BODY this "
        "row can carry. Read by the enrollment gate (`account_integrity --lane`), which refuses "
        "a list whose column disagrees. Stamped by `gtm_core.lanes route`, never by hand: "
        "pooled CSVs are rebuilt, so a hand-written value is discarded on the next sweep"
    ),
    "lane_reason": "why this lane; required for anything but `personalised`",
}


def csv_map_markdown() -> str:
    """Render the export column map from the schema the code actually reads.

    Hand-maintained, this document omitted all nine record and verdict columns that the
    enrollment gate blocks on — so a run that followed it exactly produced a list the
    gate rejected, and the doc looked complete while doing it. Generated from
    :data:`_ALIASES` and :data:`MASTER_COLS`, it cannot omit a column that exists.
    """
    lines = [
        "# Prospect — export column map",
        "",
        "<!-- GENERATED by `python -m gtm_core.prospects schema-doc`. Do not hand-edit:",
        "     re-run the command instead. Held in sync by",
        "     tests/contracts/test_hubspot_csv_map_sync.py. -->",
        "",
        "One row per **contact**, not per account: a Tier-A account with 2 enriched",
        "contacts is 2 rows. Written as `prospects-YYYYMMDD-hubspot.csv`, and folded into",
        "the pool by `python -m gtm_core.prospects consolidate`.",
        "",
        "The **CSV Column** below is the header to write. Any listed alias is accepted, so",
        "an older export keeps importing; the first spelling is the one to write today.",
        "",
        "## Columns a run writes",
        "",
        "| CSV Column | Also accepted | Meaning |",
        "|---|---|---|",
    ]
    for col in MASTER_COLS:
        if col in _ASSIGNED_COLUMNS:
            continue
        aliases = _ALIASES.get(col, (col,))
        primary, rest = aliases[0], aliases[1:]
        also = ", ".join(f"`{a}`" for a in rest) or "—"
        lines.append(f"| `{primary}` | {also} | {_COLUMN_NOTES.get(col, '')} |")

    lines += [
        "",
        "## Columns the pipeline assigns",
        "",
        "Do not write these — a value supplied here is overwritten, and for the judge",
        "columns an input that claimed to be judged would be believed by nothing.",
        "",
        "| Column | Assigned by |",
        "|---|---|",
    ]
    lines += [f"| `{col}` | {why} |" for col, why in _ASSIGNED_COLUMNS.items()]
    lines += [
        "",
        "## The record is not optional",
        "",
        "`signal_source_url`, `signal_observed`, `signal_evidence`, `signal_subject`,",
        "`signal_agent_kind`, `category_relation`, `verdict` and `verdict_reason` are what",
        "make a row checkable. Without them a renamed company, a fact about the wrong",
        "entity, and a number that drifted from its source are undetectable by",
        "construction — see `gtm_core/signal_record.py`. The enrollment gate blocks a list",
        "that lacks them.",
        "",
        "Resolve any path this file mentions with:",
        "",
        "```bash",
        "uv run python -m gtm_core.prospects paths --profile <active>",
        "```",
        "",
    ]
    return "\n".join(lines)
