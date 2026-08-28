# Facebook optimization (2026)

De-branded platform playbook. Source of truth for the Facebook rules the content linter enforces
(`lint_facebook` in [`tests/linter/content_linter.py`](../tests/linter/content_linter.py)). Companion to
`docs/{linkedin,x,instagram}-optimization.md`. Facebook is a **secondary / future-facing** surface for
this system — the plumbing exists so a profile can post there when it makes sense, not because it's a
primary B2B channel.

## How the feed ranks in 2026

Facebook optimizes for **meaningful social interactions** — content that provokes conversation between
people, not passive consumption. The signal hierarchy:

1. **Shares** — the top signal. A share puts your post on someone else's feed with their name on it. Write
   things people want to be seen sharing.
2. **Comments (with substance)** — a real reply outweighs a reaction. End on something that earns one.
3. **Reactions** — the weakest signal. Likes alone barely move distribution.
4. **Dwell / native video** — Reels and native video get distribution priority; off-platform links get
   suppressed.

## The rules

- **No outbound links in the post body.** Facebook down-ranks posts that send people off-platform. Put the
  link in the **first comment**. *(linter: error)*
- **Hook budget ≈ 477 characters** before "see more". The first line has to earn the click. Front-load the
  most interesting, specific thing. *(linter: warn if the first line spills past the fold)*
- **Short and punchy.** Long posts lose people on Facebook faster than on LinkedIn. Say it in half the
  words; every sentence earns its place. *(linter: warn on long bodies)*
- **Hashtags are weak here.** 0–2 at most; they don't help discovery the way they do on Instagram.
  *(linter: warn above 3)*
- **End with a prompt that earns a comment or a share**, not just a reaction. A specific question with no
  obvious answer beats "Thoughts?".
- **One idea per post.** Mobile-first formatting: short paragraphs, white space, one idea per line.

## Brand-safety and voice

Same as every surface: complementary-positioning (no named-competitor attacks), augmentation framing, and
the prose-craft pass (`docs/prose-craft.md`) before publish. Hooks follow `docs/hook-craft.md`, calibrated
to the ~477-char budget.

## Mechanizable subset (what `lint_facebook` checks)

| Rule | Severity |
|---|---|
| body present | error |
| URL in body (put link in first comment) | error |
| first line past the ~477-char "see more" fold | warn |
| body longer than the short-and-punchy budget | warn |
| more than a couple of hashtags | warn |

Everything else in this doc is judgment the linter can't see — apply it at draft time.
