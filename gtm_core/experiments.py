"""Targeting overlays — a temporary, run-scoped ICP/messaging experiment.

An **overlay** is a directory of tenant knowledge files that wins over the profile's own for
the length of one run::

    profiles/<tenant>/experiments/<slug>/
        EXPERIMENT.toml          # required: who asked, what for, and when it expires
        icp-personas.md          # only the files that DIFFER
        icp-scoring.toml
        hook-matrix.md

It exists so a strategy team can run a different ICP for a week without mutating the live
one, and without the obvious alternative — cloning the profile — which is the thing this
module exists to make unnecessary.

**Why a clone is the wrong answer, and the one invariant that follows.** Forking
``profiles/<t>/`` forks ``content/<t>/`` with it, and ``content/`` is where the suppression
ledger, ``latest.json`` and the already-enrolled set live (``gtm_core.lanes.context``). Two
content roots means two exclusion sets, which means the same human receives a second,
unrelated arc from what is functionally a second sender. That is a deliverability and
compliance failure, not an analytics one. **An overlay therefore lives entirely under
``profiles/`` and never addresses ``content/`` at all** — there is no code path here that
can resolve a content root, which is the property to preserve above all others in this file.

**Everything here is a refusal.** This module adds no egress, no tool, no gate and no network
call. What it adds is a set of things the system must decline to do, and each one fails
CLOSED, at admission, before any model call:

* the kill switch is shut
* the experiment has expired
* the manifest is missing, unparseable, or disagrees with its own directory name
* the directory holds a file the overlay may not override

**None of them falls back to the base profile.** A silent fallback would run the base ICP
under an experiment's label and write overlay-tagged results containing base-ICP data —
confidently wrong data, which is worse than a stopped run.

CLI::

    python -m gtm_core.experiments --profile P --list
    python -m gtm_core.experiments --profile P --overlay SLUG        # admit + describe
    python -m gtm_core.experiments --profile P --overlay SLUG --check # exit 2 if refused
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .paths import EXPERIMENTS_DIRNAME, _safe_segment, clean_env_var, resolve_profiles_root

#: The manifest every overlay must carry.
MANIFEST_NAME = "EXPERIMENT.toml"

#: Keys that cannot be overridden by an overlay, even if the file is OVERLAYABLE.
GATE_KEYS: frozenset[str] = frozenset(
    {
        "level",
        "wedge_seats",
        "on_topic_by_terms",
        "min_distinct",
        "attests_boundary",
    }
)


#: Environment flag that enables overlays at all. **Closed by default**, matching
#: ``HERMES_SCHEDULE_ENABLED``: a capability that can redirect real spend and real recipients
#: should be opened deliberately on a box, not inherited by every checkout.
ENABLED_ENV = "GTM_EXPERIMENT_OVERLAY_ENABLED"

#: The files an overlay MAY override. Anything else present in the directory is a load
#: error naming the file — never a silently ignored extra.
#:
#: The direction of that choice is deliberate. A silently ignored ``voice-bans.txt`` in an
#: overlay reads to the operator as "this experiment relaxed the ban list", and they would be
#: wrong in the dangerous direction. Refusing names the mistake at the moment it is cheapest
#: to fix.
OVERLAYABLE: frozenset[str] = frozenset(
    {
        "icp-personas.md",  # who we target
        "icp-scoring.toml",  # how we rank them for spend
        "scorecard.toml",  # the 0-100 account rubric: axes, weights and the sufficiency gate
        "hook-matrix.md",  # the persona x signal vocabulary
        "hooks.toml",  # the feed hook bank
        "market-scan-config.md",  # which phrases the sweep searches
        "case-studies.md",  # which proof maps to which cohort
        "premise-vocab.toml",  # the premise vocabulary
        # The persona/seat vocabulary (Phase 0b) — and, since 2026-09-24, per-seat COPY as
        # well: `lead_pain`, `gain`, `forbidden_pains` and `register` moved here from
        # `voice.md`'s seat table so the fact registry has one home for slot 3. That makes the
        # sentence a seat opens on overlayable, which it was not while the fields lived in a
        # REFUSED file. Deliberate — an experiment testing a new ICP needs pains written for
        # the cohort it is testing, and `voice.md` stays REFUSED so tone itself cannot be
        # varied — but it is the reason this entry is no longer "just a cue list".
        "role-vocabulary.toml",
    }
)

#: Subdirectories an overlay may carry. ``industry/`` holds one file per vertical and is read
#: as a directory by the skills that use it, so it cannot be enumerated in :data:`OVERLAYABLE`.
OVERLAYABLE_DIRS: frozenset[str] = frozenset({"industry"})

#: Files that are REFUSED with a reason, rather than merely absent from the allowlist. Listing
#: them explicitly turns "why can't I override this?" from a guess into an answer, and keeps
#: the reasoning next to the rule instead of in a design doc nobody reads at 9am.
REFUSED: dict[str, str] = {
    "PROFILE.md": (
        "carries `target_markets`, which is a legal decision taken per country with counsel — "
        "not an experiment knob. An overlay may re-weight within the allowed markets; it can "
        "never open one"
    ),
    "lane-policy.toml": (
        "owns the `suppress` refusal and the protective hold triggers. An experiment that "
        "could automate a hold could silently un-hold a protected account"
    ),
    "competitors.toml": "drives the competitor-direct exclusion",
    "domain-aliases.toml": "drives competitor/partner matching, so it is part of that exclusion",
    "voice-bans.txt": "a ban list is safety, not style",
    "outreach-banned-stems.txt": "a ban list is safety, not style",
    "shared-phrases.txt": "a ban list is safety, not style",
    "voice.md": (
        "voice is tenant identity, not a weekly variable. A tone experiment belongs one layer "
        "down, as a hook-matrix cell or a spec variant — `hooks.toml` already carries a "
        "per-hook `tone`"
    ),
    "BRAND.toml": "identity and the render surface, not targeting",
    "packs.toml": "the capability surface: which skills are reachable is not a targeting fact",
    "funnel-yields.toml": "a measured record of what happened, not a policy input",
}


class OverlayError(ValueError):
    """An overlay that may not be used, with the reason it was refused.

    Raised at admission and never later. Every construction site in this module names the
    offending file, field or date — a refusal the operator cannot act on is a refusal that
    gets worked around.
    """


@dataclass(frozen=True)
class Experiment:
    """An admitted overlay: validated, unexpired, and safe to resolve against."""

    slug: str
    owner: str
    question: str
    created: str
    expires: _dt.date
    root: Path
    #: Overlayable files actually present, sorted. What this experiment changes, and nothing else.
    files: tuple[str, ...] = ()

    def describe(self) -> str:
        return (
            f"{self.slug} (owner {self.owner}, expires {self.expires.isoformat()}) "
            f"overrides {len(self.files)} file(s): {', '.join(self.files) or 'none'}"
        )


def overlay_enabled() -> bool:
    """Whether overlays are switched on at all. Closed by default."""
    raw = (clean_env_var(ENABLED_ENV) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def experiments_dir(profile: str, profiles_root: Path | None = None) -> Path:
    root = profiles_root or resolve_profiles_root()
    return root / _safe_segment(profile, "profile") / EXPERIMENTS_DIRNAME


def overlay_dir(profile: str, slug: str, profiles_root: Path | None = None) -> Path:
    return experiments_dir(profile, profiles_root) / _safe_segment(slug, "overlay")


def list_overlays(profile: str, profiles_root: Path | None = None) -> list[str]:
    """Slugs present on disk, admitted or not. A listing is not an admission."""
    base = experiments_dir(profile, profiles_root)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))


def _parse_date(value: object, field: str, source: Path) -> _dt.date:
    if not isinstance(value, _dt.date) and not isinstance(value, str):
        raise OverlayError(
            f"{source}: `{field}` must be a date (YYYY-MM-DD), got {type(value).__name__}"
        )
    if isinstance(value, _dt.datetime):  # tomllib yields datetime for a datetime literal
        return value.date()
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise OverlayError(f"{source}: `{field}` is not a YYYY-MM-DD date: {value!r}") from exc


def _extract_gate_keys(data: object, path: str = "") -> dict[str, object]:
    found = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if k in GATE_KEYS:
                found[f"{path}{k}"] = v
            else:
                found.update(_extract_gate_keys(v, f"{path}{k}."))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            found.update(_extract_gate_keys(item, f"{path}[{i}]."))
    return found


def _check_contents(root: Path, profile: str, profiles_root: Path | None) -> tuple[str, ...]:
    """Every file in the directory must be one the overlay may override.

    Walks the tree rather than the top level: a refused file hidden one directory down is
    still a refused file, and an overlay is small enough that a full walk costs nothing.
    """
    base_knowledge = (
        (profiles_root or resolve_profiles_root()) / _safe_segment(profile, "profile") / "knowledge"
    )

    present: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        name = rel.name
        if name == MANIFEST_NAME and rel.parent == Path():
            continue
        if name.startswith("."):  # editor droppings, .gitkeep
            continue
        if rel.parent != Path():
            top = rel.parts[0]
            if top in OVERLAYABLE_DIRS:
                present.append(rel.as_posix())
            else:
                raise OverlayError(
                    f"{path}: an overlay may not carry the directory `{top}/`. Overlayable "
                    f"directories: {', '.join(sorted(OVERLAYABLE_DIRS))}"
                )
        else:
            if name in REFUSED:
                raise OverlayError(
                    f"{path}: `{name}` may not be overridden — {REFUSED[name]}. Remove it from the "
                    f"overlay; an experiment changes what we argue, never what we are not allowed to do"
                )
            if name not in OVERLAYABLE:
                raise OverlayError(
                    f"{path}: `{name}` is not an overridable file. Allowed: "
                    f"{', '.join(sorted(OVERLAYABLE))} (plus the {', '.join(sorted(OVERLAYABLE_DIRS))}/ "
                    f"directory). A file here that nothing reads is worse than absent — it reads as "
                    f"a change that is not happening"
                )
            present.append(name)

        if path.suffix == ".toml":
            try:
                overlay_data = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception:
                overlay_data = {}

            base_file = base_knowledge / rel
            base_data = {}
            if base_file.is_file():
                try:
                    base_data = tomllib.loads(base_file.read_text(encoding="utf-8"))
                except Exception:
                    base_data = {}

            overlay_keys = _extract_gate_keys(overlay_data)
            base_keys = _extract_gate_keys(base_data)

            all_keys = set(overlay_keys) | set(base_keys)
            for k in all_keys:
                if overlay_keys.get(k) != base_keys.get(k):
                    key_name = k.split(".")[-1]
                    raise OverlayError(
                        f"{path}: overlay changes gate key `{key_name}` (at `{k}`). An overlay may not set or change gate keys."
                    )

    return tuple(present)


def admit(
    profile: str,
    slug: str,
    profiles_root: Path | None = None,
    today: _dt.date | None = None,
) -> Experiment:
    """Validate an overlay and return it, or raise :class:`OverlayError` saying why not.

    Called ONCE, at the start of a run, before any model call. Five refusals, in the order
    an operator would want to hear them — switch, existence, manifest, identity, expiry —
    then the contents check.

    ``today`` is injectable so the expiry boundary can be tested on both sides. It is not a
    way to run an expired experiment: nothing in the CLI or the skills passes it.
    """
    if not overlay_enabled():
        raise OverlayError(
            f"overlays are disabled: set {ENABLED_ENV}=1 to enable them. The run was NOT "
            f"started — it did not silently fall back to the base profile, because that would "
            f"have written {slug!r}-labelled results containing base-profile data"
        )

    root = overlay_dir(profile, slug, profiles_root)
    if not root.is_dir():
        known = list_overlays(profile, profiles_root)
        raise OverlayError(f"{root}: no such experiment. Known: {', '.join(known) or '(none)'}")

    manifest = root / MANIFEST_NAME
    if not manifest.is_file():
        raise OverlayError(
            f"{manifest}: every overlay needs a manifest naming its owner, its question and "
            f"its expiry. An experiment nobody owns and that never expires is not an "
            f"experiment; it is an un-decided change to the live ICP"
        )
    try:
        raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise OverlayError(f"{manifest}: not valid TOML — {exc}") from exc

    declared = str(raw.get("slug") or "").strip()
    if declared != slug:
        raise OverlayError(
            f"{manifest}: `slug` is {declared!r} but the directory is {slug!r}. The two are the "
            f"same identity and a mismatch means one of them is attributing results elsewhere"
        )
    for field in ("owner", "question"):
        if not str(raw.get(field) or "").strip():
            raise OverlayError(
                f"{manifest}: `{field}` is required — it is what makes a stale experiment "
                f"answerable six weeks from now"
            )
    if "expires" not in raw:
        raise OverlayError(
            f"{manifest}: `expires` is required. The guarded failure is a 'temporary' ICP that "
            f"works well enough that nobody revisits it, until it is the tenant's ICP with no "
            f"one who decided that"
        )

    expires = _parse_date(raw["expires"], "expires", manifest)
    now = today or _dt.date.today()
    if expires < now:
        raise OverlayError(
            f"{manifest}: experiment {slug!r} expired on {expires.isoformat()} (today is "
            f"{now.isoformat()}); owner: {str(raw.get('owner') or '').strip()}. Renew it by "
            f"editing `expires`, or retire the directory — the run was NOT started"
        )

    files = _check_contents(root, profile, profiles_root)
    if not files:
        raise OverlayError(
            f"{root}: the overlay overrides nothing — it has a manifest and no knowledge files. "
            f"A run under it would be the base profile wearing an experiment's label"
        )

    return Experiment(
        slug=slug,
        owner=str(raw["owner"]).strip(),
        question=str(raw["question"]).strip(),
        created=str(raw.get("created") or "").strip(),
        expires=expires,
        root=root,
        files=files,
    )


def parse_overlay_arg(value: str | None) -> str | None:
    """Normalise a ``--overlay`` argument, refusing a list.

    One overlay per run is a property, not a convention: a run carrying two overlays is two
    budget checks collapsed into one, two attribution paths for one reply, and two arms a
    single person could land in. When that changes it will be a deliberate design step with
    its own guards, not a comma someone typed.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if "," in raw or " " in raw:
        raise OverlayError(
            f"--overlay takes ONE slug, got {value!r}. Run each experiment as its own run: "
            f"one overlay means one budget check, one attribution path, and one arm per person"
        )
    return _safe_segment(raw, "overlay")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI plumbing
    ap = argparse.ArgumentParser(
        prog="python -m gtm_core.experiments",
        description="List, admit and validate targeting overlays.",
    )
    ap.add_argument("--profile", required=True)
    ap.add_argument("--overlay", default=None, help="the experiment slug to admit")
    ap.add_argument("--profiles-root", default=None)
    ap.add_argument("--list", action="store_true", help="list overlays present on disk")
    ap.add_argument("--check", action="store_true", help="validate only; exit 2 if refused")
    args = ap.parse_args(argv)

    root = Path(args.profiles_root) if args.profiles_root else None
    if args.list or not args.overlay:
        slugs = list_overlays(args.profile, root)
        print(f"{ENABLED_ENV}={'on' if overlay_enabled() else 'OFF (overlays are refused)'}")
        for slug in slugs:
            print(f"  {slug}")
        if not slugs:
            print("  (none)")
        return 0

    try:
        exp = admit(args.profile, parse_overlay_arg(args.overlay) or "", root)
    except OverlayError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(exp.describe())
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI plumbing
    raise SystemExit(main())
