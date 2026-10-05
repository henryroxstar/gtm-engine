"""A5 — `gtm_core.copy_words`: the words a recipient reads, and a digest of them.

The two normalisers lived inside `agent/email_dispatch.py`. A digest recorded at staging has to
be computable by something other than the dispatcher (and `gtm_core` may not import `agent`,
`tests/contracts/test_layering.py`), so they moved here and the dispatcher imports them back
under the old private names. One definition of "the same copy".

Fixtures are fictional.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import copy_words
from gtm_core.copy_words import COPY_DIGEST_ALGO, copy_digests, normalise_copy, variant_words

STEPS = [
    {
        "step_id": "step-1",
        "variants": [
            {"subject": "A question about {{Company}}", "content": "<p>Hi &amp; hello</p>"}
        ],
    },
    {"step_id": "step-2", "variants": [{"subject": "", "content": "Following up"}]},
]


def test_the_algorithm_name_is_a_stable_string():
    assert COPY_DIGEST_ALGO == "copy-v1"


def test_normalise_drops_tags_decodes_entities_and_collapses_whitespace():
    assert normalise_copy("<div><span>Hi &amp;   hello</span></div>") == "Hi & hello"
    assert normalise_copy("<p>One</p><p>Two</p>") == "One Two"
    assert normalise_copy("a<br />b") == "a b"
    assert normalise_copy(None) == ""
    assert normalise_copy(7) == ""


def test_variant_words_include_every_link_so_a_changed_href_is_a_difference():
    a = variant_words({"subject": "s", "content": '<a href="https://one.example.test">x</a>'})
    b = variant_words({"subject": "s", "content": '<a href="https://two.example.test">x</a>'})
    assert a != b
    assert a[-1] == ("https://one.example.test",)


def test_one_digest_per_step_in_order_each_sixteen_hex():
    digests = copy_digests(STEPS)
    assert len(digests) == 2
    for d in digests:
        assert len(d) == 16 and int(d, 16) >= 0
    assert digests[0] != digests[1]


def test_rewrapping_the_html_is_not_a_difference():
    """Saleshandy re-wraps what it stores; the digest must not call that a change."""
    rewrapped = [
        {
            "step_id": "step-1",
            "variants": [
                {
                    "subject": "A question about {{Company}}",
                    "content": '<div><span style="font-size: 13px;">Hi &amp;  hello</span></div>',
                    "preheader": None,
                }
            ],
        },
        STEPS[1],
    ]
    assert copy_digests(rewrapped) == copy_digests(STEPS)


@pytest.mark.parametrize(
    ("edit", "which"),
    [
        ({"content": "<p>Hi &amp; goodbye</p>"}, 0),  # a word
        ({"subject": "A different question"}, 0),  # the subject
        ({"preheader": "Preview text"}, 0),  # the preheader
        ({"content": '<p>Hi &amp; hello <a href="https://example.test/x">here</a></p>'}, 0),
    ],
)
def test_any_change_to_what_is_read_changes_that_steps_digest_only(edit, which):
    before = copy_digests(STEPS)
    changed = json.loads(json.dumps(STEPS))
    changed[which]["variants"][0].update(edit)
    after = copy_digests(changed)
    assert after[which] != before[which]
    assert [d for i, d in enumerate(after) if i != which] == [
        d for i, d in enumerate(before) if i != which
    ]


def test_variant_order_inside_a_step_is_not_a_difference_but_a_new_variant_is():
    a = {"subject": "A", "content": "<p>one</p>"}
    b = {"subject": "B", "content": "<p>two</p>"}
    assert copy_digests([{"variants": [a, b]}]) == copy_digests([{"variants": [b, a]}])
    assert copy_digests([{"variants": [a, b]}]) != copy_digests([{"variants": [a]}])


@pytest.mark.parametrize(
    "bad", [None, "x", [{"variants": []}], [{"nope": 1}], [["x"]], [{"variants": "x"}]]
)
def test_a_shape_it_cannot_digest_raises_rather_than_returning_a_smaller_answer(bad):
    with pytest.raises(ValueError):
        copy_digests(bad)


def test_the_digest_module_is_stdlib_only_and_does_not_reach_the_agent_layer():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(copy_words.__file__).read_text(encoding="utf-8"))
    imported = {
        (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [None])
    }
    assert "agent" not in imported
