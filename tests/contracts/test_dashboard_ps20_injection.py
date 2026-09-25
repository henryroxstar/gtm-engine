"""PS20 Task 13 Steps 3-4 — an injection fixture (TP §3.B) and a ledger-bytes check (TP §3.E).

**Step 3.** A manifest hypothesis's ``claim`` and a sending-tool snapshot's own sequence
``name`` are untrusted-shaped text (CLAUDE.md §R5) that this page has always rendered
straight from disk. This is a regression fixture, not new behaviour: ``<script>...</script>``
must render escaped and inert, and ``⟦GATE:publish⟧`` — which contains no character HTML
escaping touches — must land only as ordinary element text, never inside an attribute value
and never inside a real ``<script>`` block.

**Step 4.** The page is read-only against the two audit ledgers: ``history.jsonl`` and
``costs.jsonl`` must be byte-identical before and after a render.
"""

from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from tests.contracts.test_dashboard_ps20_trust import _ago, _stats
from tests.test_email_campaign_dashboard import _seed

SCRIPT_PAYLOAD = "<script>alert(1)</script>"
#: The exact substrings TP §3.B names. ``RAW_SCRIPT`` deliberately stops before the closing
#: tag: the page also carries its OWN real ``<script>`` blocks, so the check that matters is
#: this substring never appearing unescaped — not merely that the full, closed payload string
#: is absent, which a truncated leak could dodge.
RAW_SCRIPT = "<script>alert(1)"
ESCAPED_SCRIPT = "&lt;script&gt;alert(1)"
GATE = "⟦GATE:publish⟧"
PAYLOAD = f"{SCRIPT_PAYLOAD} {GATE}"


def _inject_fixture(tmp_path, profile="acme"):
    """One campaign whose experiment hypothesis `claim` AND whose live sequence `name` both
    carry the same script tag and gate marker — the two sites TP §3.B names. `_seed` already
    writes a `c1` manifest with `sequences = ["S1"]`; this overwrites it with an
    `[[experiment.hypotheses]]` entry added, and points the snapshot's own S1 row at the same
    payload so both the rollup and a campaign-scoped page render it (PS20 P1.7: campaigns
    carry no engine code, so the experiment block is only as safe as this fixture proves).
    """
    profile = _seed(tmp_path, profile)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n\n'
        "[targets]\nemails = 9\n\n"
        "[experiment]\n"
        'approach = "one generic lane"\n\n'
        "[[experiment.hypotheses]]\n"
        'id = "H1"\n'
        f"claim = {json.dumps(PAYLOAD)}\n"
        'status = "open"\n'
        'verdict = "unresolved"\n'
        'needs = "replies"\n',
        encoding="utf-8",
    )
    _stats(
        tmp_path,
        profile,
        {"fetched": _ago(0), "sequences": [{"id": "S1", "name": PAYLOAD, "sent": 0}]},
    )
    return profile


def _render_scope(tmp_path, profile, slug=None):
    m = gd.build_model(profile, tmp_path)
    if slug:
        m = gd.scope_to_campaign(m, slug)
    return gd.render_html(m)


class _GateWalker(HTMLParser):
    """Where every occurrence of GATE lands: plain element text, an attribute value, or a
    real ``<script>`` block's own content — the three-way split TP §3.B asks for, since GATE
    has no character HTML escaping would change, so "escaped or not" cannot tell them apart."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.text_hits = 0
        self.attr_hits = 0
        self.script_hits = 0

    def _scan_attrs(self, attrs) -> None:
        for _name, value in attrs:
            if value and GATE in value:
                self.attr_hits += value.count(GATE)

    def handle_starttag(self, tag, attrs) -> None:
        self._scan_attrs(attrs)
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs) -> None:
        self._scan_attrs(attrs)

    def handle_endtag(self, tag) -> None:
        while self.stack and self.stack.pop() != tag:
            pass

    def handle_data(self, data) -> None:
        if GATE not in data:
            return
        if self.stack and self.stack[-1] == "script":
            self.script_hits += data.count(GATE)
        else:
            self.text_hits += data.count(GATE)


def _gate_placement(page: str) -> dict[str, int]:
    walker = _GateWalker()
    walker.feed(page)
    return {"text": walker.text_hits, "attr": walker.attr_hits, "script": walker.script_hits}


# --- Step 3: the injection fixture ---------------------------------------------------------


def test_script_and_gate_marker_render_escaped_and_inert(tmp_path):
    """TP §3.B. The page has real ``<script>`` tags of its own (the tab-switcher, unconditional
    in `render_html`), so the check is never "no `<script>` at all" — it is that the raw
    payload never appears, and that the marker never lands anywhere but ordinary text."""
    profile = _inject_fixture(tmp_path)
    for page in (
        _render_scope(tmp_path, profile),  # the rollup
        _render_scope(tmp_path, profile, "c1"),  # the campaign-scoped page
    ):
        assert "<script>" in page  # sanity: the page's OWN scripts still render
        assert ESCAPED_SCRIPT in page
        assert RAW_SCRIPT not in page
        placement = _gate_placement(page)
        assert placement["text"] >= 2, "the claim span and the sequence-name cell"
        assert placement["attr"] == 0
        assert placement["script"] == 0


def test_the_script_check_catches_unescaped_output(tmp_path):
    """Red evidence for the ``<script>`` half. String surgery on the real, clean page
    simulates what it would look like if a renderer stopped calling ``html.escape`` — never a
    change to the renderer itself — and flips the assertion the test above relies on, proving
    it is not vacuous."""
    page = _render_scope(tmp_path, _inject_fixture(tmp_path))
    assert ESCAPED_SCRIPT in page and RAW_SCRIPT not in page  # the real, escaped page

    poisoned = page.replace(ESCAPED_SCRIPT, RAW_SCRIPT, 1)
    assert RAW_SCRIPT in poisoned  # would fail "never appears raw" above


def test_the_gate_marker_check_catches_attribute_and_script_leaks(tmp_path):
    """Red evidence for the GATE half. Two synthetic leaks — into an attribute value, and
    into a real ``<script>`` block — are exactly the shapes ``_gate_placement`` exists to
    catch; the real page has neither, so this proves the checker discriminates rather than
    reporting zero unconditionally."""
    page = _render_scope(tmp_path, _inject_fixture(tmp_path))
    clean = _gate_placement(page)
    assert clean["attr"] == 0 and clean["script"] == 0 and clean["text"] >= 2

    into_attr = page.replace("</body>", f'<div data-x="{GATE}"></div></body>')
    assert _gate_placement(into_attr)["attr"] >= 1

    into_script = page.replace("<script>", f"<script>{GATE}", 1)
    assert _gate_placement(into_script)["script"] >= 1


# --- Step 4: ledger bytes --------------------------------------------------------------


def _seed_ledgers(base):
    history = base / "history.jsonl"
    costs = base / "costs.jsonl"
    history.write_text(
        json.dumps(
            {
                "event": "capability_asserted",
                "provider": "saleshandy",
                "sequence_id": "S1",
                "status": "PASS",
                "ts": "2026-09-20T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    costs.write_text(
        json.dumps({"ts": "2026-09-20T09:00:00Z", "cost_usd": 0.01, "call": "test"}) + "\n",
        encoding="utf-8",
    )
    return history, costs


def _sha(*paths):
    return tuple(hashlib.sha256(p.read_bytes()).hexdigest() for p in paths)


def test_render_dashboard_leaves_the_ledgers_byte_identical(tmp_path):
    """TP §3.E. The page writes only its own page, inventory and redirect stubs — never the
    audit ledgers it reads figures from (`health.inbound_health` reads `history.jsonl` for
    the capability/DNC cards)."""
    profile = _seed(tmp_path)
    base = pc._prospects_dir(profile, tmp_path).parent
    history, costs = _seed_ledgers(base)
    before = _sha(history, costs)

    gd.render_dashboard(profile, tmp_path)

    assert _sha(history, costs) == before


def test_the_ledger_check_catches_a_write(tmp_path):
    """Red evidence: appending one line to `history.jsonl` after the 'before' hash changes
    the digest, proving the byte-identical comparison above is a real check and not a
    tautology that would pass no matter what `render_dashboard` did."""
    profile = _seed(tmp_path)
    base = pc._prospects_dir(profile, tmp_path).parent
    history, costs = _seed_ledgers(base)
    before = _sha(history, costs)

    with history.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "prospect_run", "ts": "2026-09-21T00:00:00Z"}) + "\n")

    assert _sha(history, costs) != before  # would fail "byte-identical" above
