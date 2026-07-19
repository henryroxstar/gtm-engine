# Prose craft — the human-voice pass

Cross-profile craft reference. Companion to the mechanizable subset in
[`tests/linter/content_linter.py`](../tests/linter/content_linter.py) (`lint_prose_quality`), the way
the platform playbooks (`docs/{linkedin,x,facebook,instagram}-optimization.md`) pair with the platform
lints. The linter flags the pattern-matchable tells as **warnings**; this doc is the judgment layer for
everything a regex can't see. Both are advisory — a human-voice call needs a human-ish eye — but a
warning is a revise-or-justify item, not noise.

This is **de-branded**. Per-profile additions (words a specific company must never say) live in that
profile's `knowledge/voice-bans.txt`, resolved via
`python -m gtm_core.resolve_knowledge voice-bans.txt --profile <active>`.

## Why it exists

Model-written copy has a smell, and readers (and platform ranking) discount it. The reach cost is real:
generic, hedged, symmetrical prose reads as low-effort and gets skimmed. The fix is not "sound smarter"
— it is to sound like a specific person who did a specific thing.

## The tells to cut

Run the draft against these. Each has a plain-English replacement — the point is to *rewrite*, not just
delete.

**1. Borrowed-vocabulary words.** The register a model reaches for when it has nothing specific to say:
*delve, leverage (as a verb), foster, seamless, robust, realm, tapestry, testament, underscores, boasts,
myriad, plethora, pivotal, elevate.* Swap each for the plain word (`use`, not `leverage`; `look at`, not
`delve into`). If a sentence needs one of these to sound important, the sentence has no content yet.

**2. Em dashes and dash-pauses.** The `—` (and ` -- ` used as one) is the single most reliable AI tell in
short copy. Recast as two sentences, or a comma, or a colon. (En dashes in number ranges like `30–90` are
fine — those aren't pauses.)

**3. Antithetical parallelism.** "It's not X, it's Y." "Not just X, but Y." "X isn't about A. It's about
B." It feels punchy and is instantly recognizable as generated. State the thing you actually mean,
positively and once. "It's not about speed, it's about trust" → "Trust is what closes these deals."

**4. Anaphora and rule-of-three cadence.** Three sentences opening with the same word, or three parallel
clauses stacked for rhythm, read as a slogan, not a thought. Vary the openings; break the symmetry.

**5. Rhetorical questions as drama.** "So what does this mean for you?" Cut it and just say what it means.

**6. Generic openers.** "In today's fast-paced world…", "In an era where…", "In a world of…". Delete the
runway; open on the concrete thing.

**7. Hedge and filler.** "It's worth noting that", "It's important to understand", "when it comes to",
"needless to say". These delay the sentence. Start at the noun.

**8. Empty intensifiers.** "very", "really", "truly", "significantly", "massively" standing in for a
number. If it's significant, give the figure; if you don't have one, drop the adverb.

## What to put in instead (B2B-calibrated)

The goal is *specific and credible*, not confessional. This is enterprise/security copy — we want the
voice of someone who shipped the thing and knows the trade-offs, not a founder oversharing at midnight.

- **A real number, name, or date.** One concrete anchor beats three adjectives.
- **A decision and its cost.** "We picked X; it cost us Y" reads as lived; "X is the best approach"
  reads as generated.
- **One honest concession.** What was hard, what almost broke, what you'd do differently. Kept dry and
  matter-of-fact.
- **Spoken cadence.** Read it aloud. If you wouldn't say it to a smart colleague, rewrite it.
- **White space.** Short paragraphs, one idea per line. Mobile readers bounce off walls of text.

## The pass

1. Draft.
2. Run the lint (it runs automatically on any asset; add `--ban-file <resolved voice-bans.txt>` for the
   profile's own list):
   `uv run python3 tests/linter/content_linter.py <asset.json> --ban-file <path>`
   For long-form (`article.md`, `podcast-script.md`): `--prose-file <path>`.
3. Every warning is a **revise-or-justify** item. Clear it, or note why it stays (long-form em dash in a
   quoted source, a banned word that's a real product term, etc.).
4. Read the result aloud once more.

## What this is NOT

Not a hard gate — it never blocks review (warnings don't change the linter's exit code). Not a
personality transplant: the profile's `voice.md` still owns *how this company sounds*; this doc only
strips the tells that make any copy read as machine-written.
