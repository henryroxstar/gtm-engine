"""Prove each deck-lint rule fires on the defect it was written for, and stays quiet otherwise.

Every fixture below is a paraphrase of something that actually shipped into review on the
an SI partner deck in August 2026 and cost a round. The false-negative tests matter as much
as the positive ones: a rule that flags a slide which renders perfectly teaches authors to
route around the gate, and a gate people route around is worse than no gate — it looks like
coverage.

Samples name no real person or company (§R9): the buyer is `Acme`, the platform `Initech`.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import deck_lint as dl

HEAD = """---
theme: ../../.engine/deck-theme
title: Fixture
canvasWidth: 980
---

<Eyebrow>Cover</Eyebrow>

<HeroTitle :lines="['One', 'Two']" />

<!--
Presenter note for the cover, long enough to satisfy the notes schema rule.
-->
"""


def deck(*slides: str) -> str:
    return HEAD + "".join(slides)


def slide(
    frontmatter: str, body: str, notes: str = "A presenter note long enough to pass D5."
) -> str:
    return f"\n---\n{frontmatter}\n---\n\n{body}\n\n<!--\n{notes}\n-->\n"


def rules(text: str, **kw) -> list[str]:
    return [f"{f.tier} {f.rule}" for f in dl.lint(text, **kw)]


def tiers(text: str, **kw) -> list[str]:
    return [f.tier for f in dl.lint(text, **kw)]


def rules_on(text: str, slide_no: int, **kw) -> list[str]:
    """Findings for ONE slide.

    `rules()` flattens the whole deck, and the HEAD fixture's own cover slide trips some
    per-slide rules — so a whole-deck assertion can pass (or fail) on a slide the test was
    never about.
    """
    return [f"{f.tier} {f.rule}" for f in dl.lint(text, **kw) if f.slide == slide_no]


# ── parsing ───────────────────────────────────────────────────────────────────────────


def test_parses_slides_frontmatter_and_notes():
    text = deck(
        slide('layout: cover\nclicks: 2\ntag: "Chapter 01"', "<h2>HELLO</h2>", "Note one."),
        slide("layout: two-pane", "<p>Body</p>", "Note two."),
    )
    slides = dl.parse_slides(text)
    assert len(slides) == 3
    assert slides[1].layout == "cover"
    assert slides[1].clicks == 2
    assert slides[1].tag == "Chapter 01"
    assert "Note two." in slides[2].notes


def test_separator_inside_a_fenced_block_is_not_a_slide_break():
    text = deck(slide("layout: cover", "<div>\n\n```yaml\nfoo: 1\n---\nbar: 2\n```\n\n</div>"))
    assert len(dl.parse_slides(text)) == 2


# ── D1 · fit ──────────────────────────────────────────────────────────────────────────


def test_d1_flags_a_second_banner():
    """Two banners on one slide is what clipped slide 12 the first time."""
    body = (
        "<h2>TITLE</h2>\n"
        '<Banner kind="punch">First banner.</Banner>\n'
        '<Banner v-click="1" kind="punch">Second banner.</Banner>'
    )
    assert "D1 banner count" in rules(deck(slide("layout: cover", body)))


def test_d1_quiet_on_one_banner():
    body = '<h2>TITLE</h2>\n<Banner kind="punch">Only one banner here.</Banner>'
    assert "D1 banner count" not in rules(deck(slide("layout: cover", body)))


def test_d1_flags_an_overlong_banner():
    long = "word " * 45
    assert "D1 banner length" in rules(
        deck(slide("layout: cover", f'<Banner kind="punch">{long}</Banner>'))
    )


def test_d1_flags_a_slide_over_the_line_budget():
    para = "<p>" + ("Sentence about the client's architecture. " * 12) + "</p>"
    body = "<h2>A HEADLINE</h2>" + para * 3
    assert "D1 over budget" in rules(deck(slide("layout: two-pane", body)))


def test_d1_quiet_on_a_slide_that_measured_clean():
    """Slide 9 of the shipped deck: three dense cards and a question, 69px of headroom."""
    body = (
        "<h2>THE BAR SPANS THE GROUP.<br/>CONTROLS SPAN ONE ORG.</h2>"
        + '<GlassCard pad="md"><p>Identity has to be verifiable by the other entity.</p></GlassCard>'
        * 3
        + '<div class="askbox"><span class="ql">A question</span>'
        "<p>What do you put in front of a security team today?</p></div>"
    )
    assert "D1 over budget" not in rules(deck(slide("layout: cover", body)))


# ── D2 · questions ────────────────────────────────────────────────────────────────────

BANK = ["Of the agent work you have shipped, how many are in production versus pilot?"]


def _question(text: str) -> str:
    return f'<div class="askbox"><span class="ql">A question for you</span><p>{text}</p></div>'


def test_d2_flags_questions_stacked_on_the_cta():
    """The ask slide carried three questions and read as an interrogation."""
    body = "<h2>THE ASK</h2>" + _question(BANK[0])
    found = rules(deck(slide('layout: statement\ntag: "The Ask"', body)), bank=BANK)
    assert "D2 questions on the CTA" in found


def test_d2_flags_two_questions_on_one_slide():
    body = _question(BANK[0]) + _question("And how soon would that land?")
    assert "D2 stacked questions" in rules(deck(slide("layout: cover", body)), bank=BANK)


def test_d2_flags_a_question_that_is_in_no_bank():
    body = _question("What is your favourite colour, and why does it matter to the board?")
    assert "D2 unsourced question" in rules(deck(slide("layout: cover", body)), bank=BANK)


def test_d2_accepts_a_question_from_the_bank():
    found = rules(deck(*[slide("layout: cover", _question(BANK[0]))] * 3), bank=BANK)
    assert "D2 unsourced question" not in found
    assert "D2 too few questions" not in found


def test_d2_flags_a_deck_that_never_asks_anything():
    assert "D2 too few questions" in rules(deck(slide("layout: cover", "<h2>HI</h2>")), bank=BANK)


def test_question_slides_reports_where_the_questions_actually_are():
    """The one thing in a deck that reliably goes stale is a slide NUMBER.

    Reordering two slides silently invalidates every "questions are on 04, 07, 10" reference
    in the frontmatter, the presenter notes and the approved-questions register — and no rule
    can catch it, because each of those is prose. It happened twice on the same deck. So the
    numbers are printed on every run instead of spending a warning on a healthy deck.
    """
    text = deck(
        slide("layout: cover", "<h2>NO QUESTION HERE</h2>"),
        slide("layout: cover", _question(BANK[0])),
        slide("layout: cover", "<h2>NOR HERE</h2>"),
        slide("layout: cover", _question("And how soon would that land?")),
    )
    assert dl.question_slides(dl.parse_slides(text)) == [3, 5]


# ── D3 · guardrails ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "<p>We are SOC 2 Type II audited.</p>",
        "<p>One single pane of glass across the estate.</p>",
        "<p>The regulator requires this by year end.</p>",
        "<p>Certified against the framework.</p>",
        "<p>Built on decentralized identity.</p>",
    ],
)
def test_d3_universal_bans_fire(body):
    assert "D3" in tiers(deck(slide("layout: cover", body.replace("regulator", "MAS"))))


def test_d3_compiles_a_dossier_dont_into_a_ban(tmp_path):
    spec = tmp_path / "dossier-spec.json"
    spec.write_text(
        json.dumps(
            {
                "blocks": [
                    {
                        "type": "two_col",
                        "leftTitle": "DO",
                        "rightTitle": "DON'T",
                        "left": [],
                        "right": ["Don't congratulate them on the Initech award — a rival won it"],
                    }
                ]
            }
        )
    )
    rails = dl.guardrails_from(spec)
    body = "<p>Congratulate the team on the Initech award.</p>"
    assert "D3 guardrail" in rules(deck(slide("layout: cover", body)), guardrails=rails)


def test_d3_can_be_suppressed_with_a_reason():
    body = "<p>We are SOC 2 Type II audited.</p>\n<!-- lint-ok D3: quoting their own RFP -->"
    assert "D3" not in tiers(deck(slide("layout: cover", body)))


# ── D4 · claims ───────────────────────────────────────────────────────────────────────


def test_d4_flags_an_outcome_claim_with_no_precedent():
    """ "Shorter security review and more margin" — asserted, never evidenced."""
    body = "<p>The result is a shorter security review and more margin on every project.</p>"
    assert "D4 outcome claim" in rules(deck(slide("layout: cover", body)))


def test_d4_accepts_an_outcome_claim_with_a_precedent_in_the_notes():
    body = "<p>The result is a shorter review cycle.</p>"
    notes = "PRECEDENT: two named engagements measured this; see the account folder."
    assert "D4 outcome claim" not in rules(deck(slide("layout: cover", body, notes)))


def test_d4_flags_review_killing_the_agent():
    body = "<p>Security review kills the project outright.</p>"
    assert "D4 overstated outcome" in rules(deck(slide("layout: cover", body)))


def test_d4_flags_a_regulator_with_no_binding_status_in_the_notes():
    body = "<p>MAS SAFR describes a runtime control set.</p>"
    assert "D4 regulatory status" in rules(deck(slide("layout: cover", body)))


def test_d4_accepts_a_regulator_when_the_notes_carry_the_status():
    body = "<p>MAS SAFR describes a runtime control set.</p>"
    notes = "SAFR is voluntary and non-binding — never sell it as a mandate."
    assert "D4 regulatory status" not in rules(deck(slide("layout: cover", body, notes)))


def test_d4_flags_a_borrowed_term_with_no_attribution():
    body = "<p>That is the lethal trifecta, wired in permanently.</p>"
    assert "D4 unattributed term" in rules(deck(slide("layout: cover", body)))


def test_d4_accepts_the_borrowed_term_when_credited():
    body = "<p>Simon Willison's lethal trifecta, wired in permanently.</p>"
    assert "D4 unattributed term" not in rules(deck(slide("layout: cover", body)))


def test_d4_flags_a_quote_that_is_not_in_the_sources():
    """A buyer quote nobody said, and two regulator phrases that were not in the source."""
    body = '<p>"the demo was never the hard part for any of our teams"</p>'
    corpus = "Nothing in this corpus resembles that sentence."
    assert "D4 quote not in sources" in rules(deck(slide("layout: cover", body)), corpus=corpus)


def test_d4_accepts_a_quote_present_verbatim_in_the_sources():
    quote = "an agent cannot extend the scope of a mandate through its own reasoning"
    body = f'<p>"{quote}"</p>'
    corpus = f"The paper states that {quote}, which is the rule we enforce."
    assert "D4 quote not in sources" not in rules(deck(slide("layout: cover", body)), corpus=corpus)


# ── D5 · notes ────────────────────────────────────────────────────────────────────────


def test_d5_flags_a_slide_with_no_presenter_note():
    text = deck("\n---\nlayout: cover\n---\n\n<h2>NO NOTES HERE</h2>\n")
    assert "D5 no presenter note" in rules(text)


def test_d5_skips_chapter_dividers():
    text = deck("\n---\nlayout: chapter\n---\n\n<h1>CHAPTER ONE</h1>\n")
    assert "D5 no presenter note" not in rules(text)


def test_verify_manifest_collects_the_warnings():
    notes = "⚠️ VERIFY the packageability claim before presenting — it is the deck's spine."
    manifest = dl.verify_manifest(dl.parse_slides(deck(slide("layout: cover", "<p>x</p>", notes))))
    assert manifest and "VERIFY" in manifest[0][1]


# ── D6 · budget ───────────────────────────────────────────────────────────────────────


def test_d6_flags_a_deck_over_the_meeting_budget():
    text = deck(*[slide("layout: cover", "<p>Body</p>")] * 20)
    assert "D6 over slide budget" in rules(text, minutes=60)


def test_d6_quiet_at_fifteen_slides_in_an_hour():
    text = deck(*[slide("layout: cover", "<p>Body</p>")] * 14)
    assert "D6 over slide budget" not in rules(text, minutes=60)


def test_d6_does_not_charge_chapter_dividers_against_the_clock():
    """The budget is derived from minutes, and a divider costs about five seconds of them.

    Charging chrome against a time budget pushed decks to drop the structural beats that
    make an hour navigable — which is the opposite of what the rule is for.
    """
    text = deck(
        *(
            [slide("layout: cover", "<p>Body</p>")] * 14
            + [slide("layout: chapter", "<h1>Ch</h1>")] * 3
        )
    )
    assert "D6 over slide budget" not in rules(text, minutes=60)


def test_d6_exemption_caps_so_chrome_cannot_become_padding():
    text = deck(
        *(
            [slide("layout: cover", "<p>Body</p>")] * 14
            + [slide("layout: chapter", "<h1>Ch</h1>")] * 6
        )
    )
    assert "D6 over slide budget" in rules(text, minutes=60)


def test_d6_flags_an_appendix():
    text = deck(slide('layout: cover\ntag: "Appendix · Framework mapping"', "<p>Body</p>"))
    assert "D6 appendix" in rules(text)
    assert "D6 appendix" not in rules(text, allow_appendix=True)


# ── D7 · visuals ──────────────────────────────────────────────────────────────────────


def test_d7_flags_a_wall_of_prose_on_a_partner_deck():
    body = "<h2>WHAT WE BUILT</h2>" + ("<p>" + "A paragraph of product prose. " * 10 + "</p>") * 3
    found = dl.lint(deck(slide("layout: cover", body)), template="A4")
    assert any(f.rule == "wall of prose" and f.severity == dl.ERROR for f in found)


def test_d7_quiet_when_the_slide_carries_a_component():
    body = "<h2>WHAT WE BUILT</h2><FlowTrack /><p>" + ("Short line. " * 6) + "</p>"
    assert "D7 wall of prose" not in rules(deck(slide("layout: cover", body)), template="A4")


def test_d7_counts_a_frontmatter_image_as_a_visual():
    body = "<h2>IN PRACTICE</h2><p>" + ("Story line. " * 20) + "</p>"
    text = deck(slide("layout: two-pane\nimage: https://example.test/x.png", body))
    assert "D7 wall of prose" not in rules(text, template="A4")


# ── D8 · render integrity ─────────────────────────────────────────────────────────────


def test_d8_flags_a_class_with_no_rule():
    body = '<p class="nowhere-defined">Text</p>'
    assert "D8 class with no rule" in rules(deck(slide("layout: cover", body)))


def test_d8_quiet_on_a_class_the_slide_defines():
    body = (
        '<p class="case-row">Text</p>\n<style scoped>\n.case-row { font-size: 0.8rem; }\n</style>'
    )
    assert "D8 class with no rule" not in rules(deck(slide("layout: cover", body)))


def test_d8_quiet_on_theme_classes():
    body = '<p class="lede">Text</p>'
    assert "D8 class with no rule" not in rules(deck(slide("layout: cover", body)))


def test_d8_flags_an_unused_rule():
    body = "<p>Text</p>\n<style scoped>\n.leftover { color: red; }\n</style>"
    assert "D8 unused rule" in rules(deck(slide("layout: cover", body)))


def test_d8_flags_a_component_that_does_not_exist():
    assert "D8 unknown component" in rules(deck(slide("layout: cover", "<FancyChart />")))


def test_d8_flags_an_unknown_layout():
    assert "D8 unknown layout" in rules(deck(slide("layout: three-pane", "<p>x</p>")))


def test_d8_accepts_the_new_components():
    body = '<AskBox question="From the bank?" /><BreakTests :tests="[]" />'
    found = rules(deck(slide("layout: cover", body)))
    assert "D8 unknown component" not in found


# ── D10 · word budget ───────────────────────────────────────────────────────────────────


def _words(n: int) -> str:
    return " ".join(f"word{i}" for i in range(n))


def test_d10_flags_a_slide_over_the_hard_cap():
    body = f"<p>{_words(95)}</p>"
    assert "D10 wall of words" in rules(deck(slide("layout: cover", body)))


def test_d10_warns_between_the_budget_and_the_hard_cap():
    found = rules(deck(slide("layout: cover", f"<p>{_words(70)}</p>")))
    assert "D10 over word budget" in found
    assert "D10 wall of words" not in found


def test_d10_quiet_on_a_slide_inside_the_budget():
    found = rules(deck(slide("layout: cover", f"<p>{_words(30)}</p>")))
    assert not any(f.startswith("D10 over") or f.startswith("D10 wall") for f in found)


def test_d10_skips_chapter_dividers_and_the_end_card():
    """A chapter divider is a title; it is not competing with the speaker for attention."""
    found = rules(deck(slide("layout: chapter", f"<p>{_words(95)}</p>")))
    assert not any(f.startswith("D10 wall") for f in found)


def test_d10_counts_text_inside_component_props():
    """The regression that shipped: `_text_of` strips whole tags, so a slide whose content
    lives in `:tests` / `question=` props counted as ~0 words — exactly the component-led
    slides this rule exists to police."""
    body = (
        '<BreakTests :tests="['
        "{ k: 'WHO', claim: '" + _words(50) + "', implication: '" + _words(50) + "' },"
        ']" />'
    )
    assert "D10 wall of words" in rules(deck(slide("layout: cover", body)))


def test_d10_counts_an_askbox_question():
    body = f'<AskBox question="{_words(95)}" />'
    assert "D10 wall of words" in rules(deck(slide("layout: cover", body)))


def test_d10_ignores_short_machine_values_in_props():
    """Keys and icon names ('WHO', 'PROOF') are labels, not prose — counting them would
    punish the compact components the word budget is trying to encourage."""
    body = (
        '<BreakTests :tests="['
        "{ k: 'WHO', claim: 'Short claim here.' },"
        "{ k: 'PROOF', claim: 'Another short one.' },"
        ']" />'
    )
    found = rules(deck(slide("layout: cover", body)))
    assert not any(f.startswith("D10 over") or f.startswith("D10 wall") for f in found)


def test_d10_flags_a_deck_that_is_dense_on_average():
    """Every slide inside its own budget, but the deck as a whole is a document.

    The slides carry a diagram so each one is measured against the larger diagram budget —
    which is the only way to sit above the deck average without tripping a per-slide rule.
    Each one sits just under the 135 hard cap, which is what "dense on average" has to mean
    for a drawn deck: not "it uses diagrams" but "every slide is pressed against its wall".
    """
    dense = [
        slide("layout: cover", f'<StackDiagram :layers="[]" />\n<p>{_words(133)}</p>')
        for _ in range(10)
    ]
    found = rules(deck(*dense))
    assert "D10 deck is dense" in found
    assert "D10 wall of words" not in found


def test_d10_deck_average_scales_with_the_slides_that_carry_diagrams():
    """The false positive this rule spent months emitting, and the reason it was ignored.

    A diagram slide's soft budget is 95 — ABOVE the old flat deck average of 80 — so a deck
    whose every slide passed individually still failed the deck check, permanently, with no
    edit that could clear it. A warning nobody can clear is worse than no warning: it teaches
    the reader to skim the tier that also carries the real errors.
    """
    drawn = [
        slide("layout: cover", f'<StackDiagram :layers="[]" />\n<p>{_words(95)}</p>')
        for _ in range(6)
    ]
    found = rules(deck(*drawn))
    assert "D10 over word budget" not in found, "each slide is exactly on its own budget"
    assert "D10 deck is dense" not in found


def test_d10_deck_average_is_unchanged_for_a_deck_with_no_diagrams():
    """The generalisation must not become a loosening: prose is still held to 80."""
    wordy = [slide("layout: cover", f"<p>{_words(88)}</p>") for _ in range(12)]
    assert "D10 deck is dense" in rules(deck(*wordy))
    lean = [slide("layout: cover", f"<p>{_words(60)}</p>") for _ in range(12)]
    assert "D10 deck is dense" not in rules(deck(*lean))


def test_d7_does_not_accept_a_background_image_as_the_visual():
    """The regression this rule exists for.

    Six slides of the Appistoki deck each carried a generated background image, passed D7
    because `image:` counted as a visual, passed D10 because the prose that had carried the
    argument was deleted to make room — and explained less than what they replaced.
    """
    found = rules_on(deck(slide("layout: cover\nimage: /images/mood.png", "<h2>A CLAIM</h2>")), 2)
    assert "D7 image is doing the explaining" in found
    assert "D7 wall of prose" not in found


def test_d7_accepts_a_diagram_on_a_slide_that_also_has_an_image():
    body = (
        '<h2>A CLAIM</h2>\n<ScopeMap :entities="[]" :gaps="[]" span-label="X" covered-label="Y" />'
    )
    found = rules_on(deck(slide("layout: cover\nimage: /images/mood.png", body)), 2)
    assert "D7 image is doing the explaining" not in found


def test_d7_still_exempts_chapter_dividers():
    """A divider carries no argument, so an atmospheric image loses nothing there — and it
    is the one place the deck is *supposed* to use generated art."""
    found = rules_on(
        deck(slide("layout: chapter\nimage: /images/divider.png", "<h1>CHAPTER 01</h1>")), 2
    )
    assert "D7 image is doing the explaining" not in found


def test_d10_flags_a_slide_that_says_too_little():
    """The floor. A headline over a background passes every other tier while explaining
    nothing — which is exactly how the revamp made the deck worse."""
    found = rules_on(
        deck(slide("layout: cover\nimage: /images/mood.png", "<h2>THE BOTTLENECK</h2>")), 2
    )
    assert "D10 under-explained" in found


def test_d10_floor_does_not_fire_when_a_diagram_carries_the_slide():
    """A diagram slide is allowed to be word-light — the structure is doing the explaining."""
    body = '<h2>THE BOTTLENECK</h2>\n<GateFunnel in-label="BUILT" out-label="LIVE" />'
    assert "D10 under-explained" not in rules_on(deck(slide("layout: cover", body)), 2)


def test_d10_gives_a_diagram_slide_a_larger_budget():
    """80 words of schematic labels is not 80 words of paragraph, and holding both to one
    budget is what pushed slides toward being short because they were empty."""
    prose = rules_on(deck(slide("layout: cover", f"<p>{_words(80)}</p>")), 2)
    drawn = rules_on(
        deck(
            slide(
                "layout: cover", f'<FlowSequence :nodes="[]" :checks="[]" />\n<p>{_words(80)}</p>'
            )
        ),
        2,
    )
    assert "D10 over word budget" in prose
    assert "D10 over word budget" not in drawn


def test_d10_counts_text_that_lives_in_a_flat_component_prop():
    """`left-caption="…"` is read by the audience exactly like a paragraph is. Counting only
    the `:array=` props would let a diagram park real copy outside the budget."""
    body = f'<ProofContrast left-key="A LOG" left-caption="{_words(140)}" />'
    assert "D10 wall of words" in rules(deck(slide("layout: cover", body)))


def test_d10_ignores_machine_valued_props():
    """`kind`, `icon`, `page` and friends are not read by anyone — counting them would
    make the budget noise."""
    body = '<Banner kind="punch" icon="rocket launch" page="fabric">Short.</Banner>'
    assert "D10 over word budget" not in rules_on(deck(slide("layout: cover", body)), 2)


def test_lint_ok_suppression_matches_two_digit_tiers():
    """`D\\d` would read `lint-ok D10` as `D1` and suppress the wrong rule while looking
    like it worked — the bug two-digit tiers introduced."""
    body = f"<p>{_words(95)}</p>\n<!-- lint-ok D10: evidence slide, agreed with the reviewer -->"
    found = rules(deck(slide("layout: cover", body)))
    # Per-slide findings are suppressed. The deck-level average (slide 0) is deliberately not —
    # a per-slide exemption should not silently switch off the whole-deck check.
    assert not any(f.startswith("D10 wall") or f.startswith("D10 over") for f in found)


# ── D9 · render weight (theme audit) ────────────────────────────────────────────────────
#
# D9 is different from D1–D8: it audits a deck-theme *directory*, not a slides.md string.
# Fixtures build a throwaway theme under tmp_path so these stay independent of whatever
# real theme happens to be checked out — see test_the_shipped_theme_has_no_errors for that.


def _theme(tmp_path, files: dict[str, str]):
    theme = tmp_path / "deck-theme"
    (theme / "styles").mkdir(parents=True)
    (theme / "styles" / "slidev-overrides.css").write_text(
        "html.deck-export { --card-blur: none; }\n"
    )
    for name, content in files.items():
        path = theme / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return theme


def test_d9_flags_a_hardcoded_backdrop_filter(tmp_path):
    theme = _theme(
        tmp_path, {"components/NewCard.vue": "<style>.new-card{backdrop-filter:blur(8px);}</style>"}
    )
    assert any(f.rule == "hardcoded backdrop-filter" for f in dl.lint_theme(theme))


def test_d9_quiet_on_backdrop_filter_through_the_token(tmp_path):
    theme = _theme(
        tmp_path,
        {"components/NewCard.vue": "<style>.new-card{backdrop-filter:var(--card-blur);}</style>"},
    )
    assert not any(f.rule == "hardcoded backdrop-filter" for f in dl.lint_theme(theme))


def test_d9_flags_an_unguarded_filter(tmp_path):
    theme = _theme(
        tmp_path, {"components/NewGlow.vue": "<style>.new-glow{filter:blur(20px);}</style>"}
    )
    assert any(f.rule == "unguarded filter" for f in dl.lint_theme(theme))


def test_d9_quiet_on_an_allowlisted_ambient_layer(tmp_path):
    theme = _theme(
        tmp_path, {"components/PulseHalo.vue": "<style>.pulse-halo{filter:blur(40px);}</style>"}
    )
    assert not any(f.rule == "unguarded filter" for f in dl.lint_theme(theme))


def test_d9_quiet_on_a_photo_bound_filter(tmp_path):
    theme = _theme(
        tmp_path, {"layouts/cover.vue": "<style>.bg-image{filter:saturate(108%);}</style>"}
    )
    assert not any(f.rule == "unguarded filter" for f in dl.lint_theme(theme))


def test_d9_flags_a_keyframe_that_ends_on_a_nonzero_filter(tmp_path):
    theme = _theme(
        tmp_path,
        {
            "styles/animations.css": "@keyframes enter { from{filter:blur(12px);} to{filter:blur(0);} }"
        },
    )
    assert any(f.rule == "keyframes ends non-none filter" for f in dl.lint_theme(theme))


def test_d9_quiet_on_a_keyframe_that_ends_on_none(tmp_path):
    theme = _theme(
        tmp_path,
        {"styles/animations.css": "@keyframes enter { from{filter:blur(12px);} to{filter:none;} }"},
    )
    assert not any(f.rule == "keyframes ends non-none filter" for f in dl.lint_theme(theme))


def test_d9_does_not_misfire_inside_a_keyframe_that_uses_blur(tmp_path):
    """Regression: a fragile keyframes-stripper could leave `from { filter: blur(12px) }`
    behind as an ordinary rule and misfire 'unguarded filter' on it — the exact failure mode
    `_strip_keyframes` uses exact character spans (not string replace) to avoid."""
    theme = _theme(
        tmp_path,
        {"styles/animations.css": "@keyframes enter { from{filter:blur(12px);} to{filter:none;} }"},
    )
    assert not any(f.rule == "unguarded filter" for f in dl.lint_theme(theme))


def test_d9_flags_unguarded_gradient_text(tmp_path):
    theme = _theme(
        tmp_path, {"components/NewLabel.vue": "<style>.new-label{background-clip:text;}</style>"}
    )
    findings = dl.lint_theme(theme)
    assert any(f.rule == "unguarded gradient text" and f.severity == dl.WARN for f in findings)


def test_d9_quiet_on_allowlisted_gradient_text(tmp_path):
    theme = _theme(
        tmp_path, {"components/GradientText.vue": "<style>.gt{background-clip:text;}</style>"}
    )
    assert not any(f.rule == "unguarded gradient text" for f in dl.lint_theme(theme))


def test_d9_flags_a_theme_missing_the_print_guard(tmp_path):
    theme = tmp_path / "deck-theme"
    (theme / "styles").mkdir(parents=True)
    (theme / "styles" / "slidev-overrides.css").write_text("/* no guard here */")
    assert any(f.rule == "print guard missing" for f in dl.lint_theme(theme))


# ── the shipped deck ──────────────────────────────────────────────────────────────────


def test_the_shipped_deck_has_no_errors():
    """The regression fixture: any already-built deck must pass its own gate.

    content/ is per-tenant and not shipped in every checkout, so this globs for
    whatever real deck happens to be present locally rather than naming a tenant.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    candidates = sorted(root.glob("content/*/accounts/*/deck-slides-*.md"))
    if not candidates:
        pytest.skip("no account deck content present")
    path = candidates[0]
    dossier = path.parent / "dossier-spec.json"
    bank = dl.question_bank(dossier) if dossier.exists() else None
    approved = path.parent / path.name.replace("deck-slides-", "deck-questions-", 1)
    if bank is not None and approved.exists():
        bank += dl.question_bank(approved)
    rails = dl.guardrails_from(dossier) if dossier.exists() else []
    errors = [
        f
        for f in dl.lint(
            path.read_text(encoding="utf-8"), bank=bank, guardrails=rails, template="A4"
        )
        if f.severity == dl.ERROR
    ]
    assert errors == [], "\n".join(
        f"{f.tier} {f.rule} slide {f.slide}: {f.excerpt}" for f in errors
    )


def test_the_shipped_theme_has_no_errors():
    """The theme regression fixture for D9: a real deck-theme must pass its own audit.

    The theme lives in a separate deck workspace, not in this repo, and its location is
    machine-specific — so it comes from `GTM_DECK_THEME` and the test skips when unset.
    Hardcoding the sibling checkout's path named the workspace in-tree, which the de-brand
    RELEASE gate rejects (correctly: tests must carry no company or product token).
    """
    import os
    from pathlib import Path

    configured = os.environ.get("GTM_DECK_THEME")
    if not configured:
        pytest.skip("set GTM_DECK_THEME to the deck-theme dir to run the D9 regression")
    theme = Path(configured).expanduser()
    if not theme.exists():
        pytest.skip(f"GTM_DECK_THEME does not exist: {theme}")
    errors = [f for f in dl.lint_theme(theme) if f.severity == dl.ERROR]
    assert errors == [], "\n".join(f"{f.rule}: {f.excerpt}" for f in errors)
