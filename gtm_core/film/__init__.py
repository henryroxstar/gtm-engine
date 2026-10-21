"""One film, one file, one command.

A narrated film used to live in five places that drifted apart: the voice script, a caption
dictionary inside a scratch build script, the shot list, the motion-graphic specs, and the
official script files. Every wording change (2026-10-20: "thumb" became "fingerprint") meant
editing several of them and re-running about ten hand-chained ``video_finish`` steps, and each
rebuild re-met the same traps (a stale finish record that made ``run`` skip, the bitrate floor,
the disclosure that does not burn on a pre-burned master).

``film.json`` is now the single source: each shot carries its spoken turns, its caption, its
picture (graphic scene, footage cut, split screen) and its overlays; the film carries its cast,
timing rules, sound cues and music bed. ``python -m gtm_core.film`` derives everything else from
it — the voice-led timeline, caption screens, the shot list, the cue sheet, the finish spec, the
review sheet — and only redoes what changed. The script and shot list in ``scripts/`` are
written FROM it (``sync-script``), never edited beside it.
"""
