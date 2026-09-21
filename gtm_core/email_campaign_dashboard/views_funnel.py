from __future__ import annotations

from ..prospects_consolidate import _prospects_dir
from .format import _e


def _attrition_funnel_block(m: dict) -> str:
    """Visual Attrition Funnel block displaying strict conservation-of-accounts waterfall."""
    ar = m.get("attrition_receipt") or {}
    total = ar.get("total_intake", 0)
    failed_fit = ar.get("failed_fit", 0)
    failed_intent = ar.get("failed_intent", 0)
    failed_enrichment = ar.get("failed_enrichment", 0)
    held = ar.get("held", 0)
    ready = ar.get("ready", 0)

    if total == 0:
        ps = m.get("prospect_status") or {}
        counts = ps.get("counts") or {}
        ready = counts.get("ready_to_send", 0)
        held = counts.get("waiting_on_you", 0)
        total = ps.get("total", ready + held)

    stages = [
        ("Total Intake", total, "base"),
        ("Failed Fit", failed_fit, "bad"),
        ("Failed Intent", failed_intent, "bad"),
        ("Enrichment Miss", failed_enrichment, "bad"),
        ("Held", held, "warn"),
        ("Ready", ready, "good"),
    ]

    steps_html = "".join(
        f'<div class="funnel-step" style="display:inline-block;padding:8px 14px;margin:4px;background:var(--panel);border:1px solid var(--line);border-radius:10px;text-align:center;">'
        f'<div class="funnel-label" style="font-size:12px;color:var(--muted);">{_e(label)}</div>'
        f'<div class="funnel-count" style="font-size:20px;font-weight:700;">{count:,}</div>'
        f"</div>"
        + (
            '<span class="funnel-arrow" style="margin:0 6px;color:var(--muted);font-weight:bold;">→</span>'
            if i < len(stages) - 1
            else ""
        )
        for i, (label, count, _tone) in enumerate(stages)
    )

    return f"""
      <div class="card funnel-card">
        <h2>Attrition Funnel & Receipt</h2>
        <p class="muted">Strict conservation-of-accounts waterfall across filtering, intent, and enrichment gates.</p>
        <div class="funnel-waterfall" style="display:flex;align-items:center;flex-wrap:wrap;margin:12px 0;">{steps_html}</div>
      </div>"""


def _safe_downloads_block(m: dict) -> str:
    """Safe-Download Deliverables section linking only to current dated ready-to-load CSVs."""
    downloads = m.get("safe_downloads")
    go_live = m.get("go_live_status") or "staged"

    if downloads is None:
        import time

        root = m.get("_content_root")
        profile = m.get("profile", "")
        downloads = []
        if profile:
            seq_dir = _prospects_dir(profile, root) / "sequences"
            rtl = seq_dir / "ready-to-load.csv"
            if rtl.exists():
                try:
                    age_s = time.time() - rtl.stat().st_mtime
                    if age_s < 7 * 86400:
                        downloads = [
                            {
                                "name": "ready-to-load.csv",
                                "path": "prospects/sequences/ready-to-load.csv",
                                "age_days": round(age_s / 86400, 1),
                            }
                        ]
                except OSError:
                    pass

    items_html = ""
    if downloads:
        items_html = "".join(
            f'<li><a href="{_e(d["path"])}" download><strong>{_e(d["name"])}</strong></a> '
            f'<span class="pill good">current</span> '
            f'<span class="muted">({d.get("age_days", 0)}d old)</span></li>'
            for d in downloads
        )
    else:
        items_html = '<li class="muted">No current verified CSVs ready for download.</li>'

    go_live_badge = f'<span class="pill {"good" if go_live == "active" else "warn"}">{_e(go_live.upper())}</span>'

    return f"""
      <div class="card safe-downloads-card">
        <h2>Safe-Download Deliverables</h2>
        <p>Go-Live Status: {go_live_badge}</p>
        <p class="muted">Direct links to verified, current ready-to-load prospect CSVs. Internal pool directories and stale files (&gt;7d) are masked.</p>
        <ul class="steps">{items_html}</ul>
      </div>"""
