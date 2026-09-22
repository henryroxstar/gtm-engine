"""The retention sweep's campaign gate: is every customer campaign FINISHED?

The product owner's rule (2026-09-21) is *"I do not want to purge anything if we haven't finished
the customer email campaign"*. :func:`campaign_gate` is that sentence as code, and
:mod:`gtm_core.retention_sweep` refuses ``--apply`` whenever it :attr:`~CampaignGate.blocks_apply`.

**Deliberately STRICTER than the status page's "open".** ``email_campaign_dashboard``'s
``--scope open`` means *status is active/live/running*, which is the right question for "what is
sending right now" and the wrong one here: the first cut of this gate reused it and so failed
OPEN. Campaigns here sit in ``draft`` or paused for weeks between waves; neither word is
"active", so a purge was permitted in the middle of a campaign. "Not running" is not "finished".
This gate therefore asks the opposite question — a campaign is finished only when its manifest
says so in a word from :data:`FINISHED_STATUSES` — and everything else blocks: ``draft``,
``active``, ``paused``, a blank or missing status, a word nobody has seen before, and a manifest
that cannot be read at all.

**No manifest is not an all-clear either.** A profile with no ``*.campaign.toml`` but with
exports under ``prospects/sequences/`` has campaign work and no statement about it; that blocks
too. Only a profile with neither — nothing campaign-shaped exists — is permitted.

**Nor is somebody else's manifest.** The exports rule used to be consulted only when there were
ZERO manifests, so one old ``closed`` manifest switched it off for the whole profile: last
season's finished campaign permitted a purge in the middle of this season's, whose lists sat in
``prospects/sequences/`` with no manifest of their own. A finished manifest speaks only for the
files it CLAIMS — its ``roster_globs``, globbed relative to ``prospects/`` exactly as the status
page globs them. Every ``prospects/sequences/*.csv`` no finished manifest claims is unfinished
campaign work and blocks. That deliberately includes ``ready-to-load.csv`` and the per-list
``ready-to-load-*.csv`` working files every tenant has: a load file is a campaign about to
happen, and the rule is "never purge while a campaign may be unfinished".

There is no override flag, here or in the CLI (docs/RULES.md §R13: a permanent ban lives in
code). Closing the campaign IS the override.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from gtm_core.prospect_paths import prospects_dir, sequences_dir

#: The ONLY manifest ``status`` values that mean a campaign is over. A closed list on purpose:
#: the manifest vocabulary in use today is ``draft`` -> ``active``, and ``closed`` is the word an
#: operator sets by hand when the send is finished — the others are the spellings of the same
#: decision someone will reasonably type instead. An open-ended rule ("anything not active")
#: is what failed; with a closed list an unknown or misspelt word blocks, which is the safe way
#: to be wrong. Every word added here is a state in which prospect data may be purged.
FINISHED_STATUSES: frozenset[str] = frozenset(
    {"closed", "completed", "finished", "done", "archived"}
)


@dataclass(frozen=True)
class CampaignVerdict:
    """One manifest, as the gate sees it. ``slug`` is the filename when it cannot be read."""

    slug: str
    status: str
    finished: bool

    @property
    def label(self) -> str:
        return f"{self.slug} (status: {self.status})"


@dataclass(frozen=True)
class CampaignGate:
    campaigns: tuple[CampaignVerdict, ...] = ()
    #: Every ``prospects/sequences/*.csv`` (or the folder itself, when it is a link).
    sequence_exports: tuple[Path, ...] = ()
    #: The subset no FINISHED manifest's ``roster_globs`` claims. With no manifest, all of them.
    unclaimed_exports: tuple[Path, ...] = ()

    @property
    def unfinished(self) -> tuple[CampaignVerdict, ...]:
        return tuple(c for c in self.campaigns if not c.finished)

    @property
    def blocks_apply(self) -> bool:
        return bool(self.unfinished) or bool(self.unclaimed_exports)

    def _unclaimed_names(self, limit: int = 8) -> str:
        names = [p.name for p in self.unclaimed_exports]
        more = f" (+{len(names) - limit} more)" if len(names) > limit else ""
        return ", ".join(names[:limit]) + more

    def report_lines(self) -> list[str]:
        """The verdict a plan opens with: every campaign, then whether ``--apply`` may run."""
        lines = [
            f"    - {c.label} — {'finished' if c.finished else 'UNFINISHED'}"
            for c in self.campaigns
        ]
        found = len(self.sequence_exports)
        if not self.campaigns:
            lines = [
                f"    - no campaign manifest, {found} sequence export(s) in prospects/sequences/"
            ]
        elif found:
            lines.append(
                f"    - prospects/sequences/: {found} export(s), {len(self.unclaimed_exports)} "
                "not claimed by a finished campaign's roster_globs"
            )
        if self.unclaimed_exports:
            lines.append(f"      unclaimed: {self._unclaimed_names()}")
        if self.unfinished:
            verdict = f"REFUSED — {len(self.unfinished)} campaign(s) not finished"
        elif not self.campaigns and self.blocks_apply:
            verdict = "REFUSED — campaign work exists and nothing says it is finished"
        elif self.blocks_apply:
            verdict = (
                f"REFUSED — {len(self.unclaimed_exports)} sequence export(s) not claimed by a "
                "finished campaign"
            )
        else:
            verdict = "permitted — no campaign is unfinished"
        return ["  Campaigns:", *lines, f"  --apply would be {verdict}"]

    def _unclaimed_clause(self, manifest: str) -> str:
        """How to proceed when files under ``sequences/`` belong to no finished campaign."""
        return (
            f"{len(self.unclaimed_exports)} file(s) under prospects/sequences/ are not claimed by "
            f"any FINISHED campaign: {self._unclaimed_names()}. They are campaign work (a "
            "ready-to-load file is a campaign about to happen) and nothing says it is finished. "
            "Either add them to the finished campaign they belonged to — `roster_globs` in "
            f'{manifest}, relative to prospects/, e.g. `roster_globs = ["sequences/<file>.csv"]` '
            "(a claimed roster is then kept as that campaign's record, never archived) — or "
            "close the campaign they belong to"
        )

    def refusal(self, profile: str) -> str:
        """The operator-facing reason, naming each blocking campaign WITH its current status."""
        manifest = f"content/{profile}/plans/campaigns/<slug>.campaign.toml"
        tail = "then run this again. There is no override flag. Run without --apply for the plan."
        if self.unfinished:
            also = f" Also: {self._unclaimed_clause(manifest)}." if self.unclaimed_exports else ""
            return (
                f"REFUSED — campaign(s) not finished for '{profile}': "
                f"{', '.join(c.label for c in self.unfinished)}. Nothing is purged until those "
                f'campaigns are closed: when a campaign is over, set `status = "closed"` in '
                f"{manifest}, {tail} (Only {', '.join(sorted(FINISHED_STATUSES))} count as "
                f"finished; draft, paused, blank or any other word does not.){also}"
            )
        if self.campaigns:
            return f"REFUSED — for '{profile}', {self._unclaimed_clause(manifest)}, {tail}"
        return (
            f"REFUSED — '{profile}' has no campaign manifest, but prospects/sequences/ holds "
            f"{len(self.sequence_exports)} export(s) ({self._unclaimed_names()}): campaign work "
            "exists and nothing says it is finished. Nothing is purged until something does: once "
            f'the send is over, add {manifest} with `slug = "<slug>"`, `status = "closed"` and '
            f'`roster_globs = ["sequences/*.csv"]`, {tail}'
        )


class UnfinishedCampaignRefusal(RuntimeError):
    """``apply`` was requested while the gate blocks. Nothing has been written."""

    def __init__(self, profile: str, gate: CampaignGate) -> None:
        self.gate = gate
        self.slugs = tuple(c.slug for c in gate.unfinished)
        super().__init__(gate.refusal(profile))


def _status_of(manifest: dict) -> tuple[str, bool]:
    """``(status as shown, finished?)`` — compared case- and whitespace-insensitively."""
    raw = str(manifest.get("status") or "").strip()
    return raw or "no status declared", raw.lower() in FINISHED_STATUSES


def _sequence_exports(profile: str, content_root: Path | None) -> tuple[Path, ...]:
    """Every ``*.csv`` directly under ``prospects/sequences/``, judged WITHOUT following a link.

    A linked CSV counts by its name alone — evidence of campaign work does not need opening —
    and a ``sequences/`` that is itself a link is returned as the one entry: it is never
    followed, and "could not look" must not read as "nothing there".
    """
    folder = sequences_dir(profile, content_root)
    if folder.is_symlink():
        return (folder,)
    return tuple(p for p in sorted(folder.glob("*.csv")) if p.is_symlink() or p.is_file())


def _claimed_by_finished(
    profile: str, content_root: Path | None, finished: list[dict]
) -> set[Path]:
    """Every path a FINISHED manifest's ``roster_globs`` names, globbed relative to
    ``prospects/`` exactly as ``retention_sweep._roster_files`` and the status page glob them.

    Paths are compared as globbed, never resolved: an export is claimed only under the name it
    has in ``prospects/sequences/``. A pattern ``Path.glob`` refuses (absolute, empty) or a
    ``roster_globs`` that is not a list claims nothing — which blocks, the safe way to be wrong.
    Only ``*.csv`` names are claimable, so a ``sequences/`` that is itself a link — reported as the
    one entry nobody could look inside — can never be claimed away by a glob that names the folder.
    """
    base = prospects_dir(profile, content_root)
    claimed: set[Path] = set()
    for manifest in finished:
        globs = manifest.get("roster_globs")
        for pattern in globs if isinstance(globs, list) else []:
            try:
                claimed.update(p for p in base.glob(str(pattern)) if p.suffix == ".csv")
            except (ValueError, NotImplementedError):
                continue
    return claimed


def campaign_gate(profile: str, content_root: Path | None = None) -> CampaignGate:
    """Read every campaign manifest and decide whether a purge may run.

    Readable manifests come from the dashboard's own loader, so "which campaigns exist" has one
    answer. That loader SKIPS a file it cannot parse or that has no ``slug`` — for a page that
    costs a tile; here it would hide the one running campaign behind a typo — so a second pass
    names every skipped file as an unfinished campaign until someone fixes it.

    Then the exports: every ``prospects/sequences/*.csv`` must be claimed by a FINISHED
    manifest's ``roster_globs``. An unreadable manifest claims nothing (and blocks on its own).
    """
    from gtm_core.campaigns_dashboard import _campaigns_dir, _load_manifests

    manifests = _load_manifests(profile, content_root)
    verdicts = [CampaignVerdict(str(m["slug"]), *_status_of(m)) for m in manifests]
    finished_manifests = [m for m in manifests if _status_of(m)[1]]
    for path in sorted(_campaigns_dir(profile, content_root).glob("*.campaign.toml")):
        try:
            readable = bool(tomllib.loads(path.read_text(encoding="utf-8")).get("slug"))
        except (OSError, ValueError):  # TOMLDecodeError and a bad byte are both ValueErrors
            readable = False
        if not readable:
            verdicts.append(CampaignVerdict(path.name, "unreadable manifest", False))
    exports = _sequence_exports(profile, content_root)
    claimed = _claimed_by_finished(profile, content_root, finished_manifests)
    unclaimed = tuple(p for p in exports if p not in claimed)
    return CampaignGate(tuple(verdicts), exports, unclaimed)
