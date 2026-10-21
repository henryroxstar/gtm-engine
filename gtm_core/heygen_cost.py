"""The HeyGen cost model — the authoritative statement. Every other surface points here.

Import these constants; never re-derive a rate, and never restate one in prose. Seven files had
restated this rate before 2026-09-07 and several had it wrong, including two tenant voice notes.
That is the failure this module exists to make structurally impossible: one home, imported.

THE MODEL: HeyGen bills PER SECOND of delivered footage, at a rate that varies with the ENGINE.
Settled 2026-09-07 from four independent balance-delta measurements in this tenant's own
`costs.jsonl`, spanning durations from ~11s to ~42s on `avatar_v` / `avatar_iii`:

    9 credits / 10.79s = 0.834      12 credits / 13.69s = 0.877
   11 credits / 12.72s = 0.865      36 credits / 42.16s = 0.854

A 42-second render costing four times a 10-second one is what rules out per-job billing. The
cheaper engine measures lower: `avatar_iv` at 720p delivered 18.16s for 11 credits = 0.606 c/s.
So the rate is a band across engines, and the engine is not known at routing time — which is why
a lane reports a RANGE and never a point estimate.

WHY THE PER-JOB READING SURVIVED AS LONG AS IT DID, since it will be tempting again. The
2026-08-27 batch recorded only "9 videos, 209 credits" with no per-video durations. Dividing
gives ~23 credits/video, which looks like a flat per-job charge; at 0.865 c/s the same 209
credits is ~242 seconds, i.e. ~27s per video, which is an ordinary presenter beat. Both readings
fit a batch total. Only a set of renders at DIFFERENT durations separates them, and the ledger
did not contain one until 2026-09-04.

A SECOND TRAP, and the one that cost this repo a day on 2026-09-07: the ledger note for the
first per-second measurement reads "217 -> 206 across 12.72s". `206` is the balance AFTER, not
the cost; the cost is the delta, 11. Read as a cost it gives 16.2 c/s, which reconciles with
nothing and reads as evidence the per-second claim is broken. It is not — it is a balance.
When quoting a cost from this ledger, quote the DELTA.

BILLING PLANE: HeyGen's API docs quote a per-second figure for the enterprise surface. That is a
different plane from the MCP plan this system draws against, and where they disagree the ledger
wins — it prices the surface that sends us the invoice.

CONSEQUENCE FOR RENDER STRATEGY: under per-second billing a per-beat set costs the SAME as one
continuous take of the same footage. Per-beat is therefore strictly better — it confines a
failed render to one beat, and lets one line be re-rendered for the seconds it contains. That is
why `video-avatar` renders one clip per beat and why `gtm_core.video_finish.split` no longer has
a cost rationale.

NAMING: these were `HEYGEN_CREDITS_PER_RENDER_MINUTE` / `..._MIN` until 2026-09-07, holding 23
and 11 — per-JOB figures under a per-minute name. Both the name and the model were wrong.
"""

from __future__ import annotations

#: Credits per second of delivered footage on the default presenter engines (`avatar_v`,
#: `avatar_iii`). The four measurements above span 0.834-0.877 with a mean of 0.857; this takes
#: the upper-middle rather than the mean, deliberately, so a routing estimate errs toward
#: over-stating spend. An operator who is quoted high and spends less is not harmed; one quoted
#: low and billed more stops trusting the estimate, which is the failure C5 exists to prevent.
HEYGEN_CREDITS_PER_SECOND = 0.865

#: The cheap end of the engine band — `avatar_iv` at 720p, measured 2026-08-28 (11 credits over
#: 18.16s). Present so a lane can report a range rather than a point estimate, because the engine
#: is chosen after routing.
HEYGEN_CREDITS_PER_SECOND_MIN = 0.606

#: The per-MINUTE figures the lane estimate is built from. Derived, not separately measured —
#: stated as constants so the arithmetic happens once, here, rather than at each call site.
HEYGEN_CREDITS_PER_MINUTE = round(HEYGEN_CREDITS_PER_SECOND * 60)
HEYGEN_CREDITS_PER_MINUTE_MIN = round(HEYGEN_CREDITS_PER_SECOND_MIN * 60)

#: The nominal deliverable a routing-time estimate prices: one minute of finished video. Stated
#: as a constant so the figure is reproducible and obviously coarse, rather than looking like a
#: quote derived from a script nobody has written yet.
ESTIMATE_BASIS_MINUTES = 1
