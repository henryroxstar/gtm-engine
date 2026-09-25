"""PS20 Task 2.1b — the blocks Phase 2's tabs import, carved out of the modules that keep them."""

import pytest

from gtm_core import campaigns_dashboard as cd
from gtm_core.email_campaign_dashboard import (
    views_emails,
    views_inbound,
    views_learn,
    views_ready,
    views_what,
    views_who,
)

CARVED = [
    (views_who, ("_pool_block", "_judge_split", "_verdicts_note", "_sources_note")),
    (
        views_learn,
        ("_varies_block", "_grid_block", "_can_answer_block", "_lift_block", "_experiment_notes"),
    ),
    (
        views_ready,
        (
            "_people",
            "_contacted_sentences",
            "_sent_card",
            "_readiness_blocks",
            "_maintenance_lines",
        ),
    ),
    (
        views_what,
        (
            "_subjects_block",
            "_opening_block",
            "_qa_detail",
            "_pack_notes",
            "_capability_spread",
        ),
    ),
    (
        views_emails,
        (
            "_mail",
            "_pack_mails",
            "_rendered_mails",
            "_template_mails",
            "_touches_rows",
        ),
    ),
    (views_inbound, ("_capability_lines", "_inbound_lines")),
    (cd, ("_hypotheses_html",)),
]


@pytest.mark.parametrize("module,names", CARVED, ids=[m.__name__ for m, _ in CARVED])
def test_each_block_is_a_function_of_its_own(module, names):
    for name in names:
        assert callable(getattr(module, name, None)), f"{module.__name__}.{name}"


def test_the_before_sending_content_moved_rather_than_copied():
    """§R10: views_learn makes room for its carve by giving this content away, not by keeping
    a second copy of it."""
    for name in ("_contacted_sentences", "_sent_card", "_ops_view", "_maintenance_card"):
        assert not hasattr(views_learn, name), name


def test_the_readiness_blocks_come_in_todays_order():
    m = {
        "campaigns": {
            "campaigns": [
                {
                    "state": "staged",
                    "sequences": [],
                    "window": {"capacity_blocker": "Two mailboxes are warming up."},
                }
            ]
        },
        "status": {"sequences": []},
        "messages": [{"sequence_id": "seq1", "lint": {"drift": ["body changed"]}}],
    }
    blocks = views_ready._readiness_blocks(m)
    assert list(blocks) == ["sent", "re-push", "holding-up", "nothing-outstanding"]
    assert blocks["re-push"] and blocks["holding-up"] and blocks["nothing-outstanding"] == ""


def test_the_hypotheses_block_is_the_experiment_blocks_own():
    x = {
        "hypotheses": [
            {
                "id": "H1",
                "claim": "The seat beats the pitch",
                "status": "can answer",
                "verdict": "open",
                "needs": "replies",
            }
        ]
    }
    part = cd._hypotheses_html(x)
    assert "The 1 question we set out to answer" in part and part in cd._experiment_block(x)
