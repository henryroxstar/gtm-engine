"""Phase 12 (§6.2) — the Article 50 synthetic-media disclosure check at Gate 2.

Drives the real handlers (``on_text`` stages, ``on_callback`` approves) exactly like
tests/cockpit/test_publish_gate.py, adding a ``[disclosure]``-configured BRAND.toml
under the cockpit's own ``profiles_root`` so ``_candidate_disclosure_lines`` has
something real to read.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    FakeMsg,
    FakePublisher,
    callback_update,
    make_cfg,
    publish_block,
    stream_of,
    text_update,
)

from agent.publish import content_hash  # noqa: E402
from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 91
DISCLOSURE_LINE = "Made with AI. Posted by a human."


def _make_cockpit_with_kit(tmp_path, *, publisher=None, disclosure_line=DISCLOSURE_LINE):
    cfg = make_cfg(tmp_path, chat_ids={CHAT_ID})
    if disclosure_line is not None:
        knowledge = cfg.profiles_root / "example" / "knowledge"
        knowledge.mkdir(parents=True)
        (knowledge / "BRAND.toml").write_text(
            f'[disclosure]\nline = "{disclosure_line}"\n', encoding="utf-8"
        )
    cockpit = botmod.Cockpit(cfg)
    if publisher is not None:
        cockpit._publisher = publisher
    return cockpit


def _stage(cockpit, monkeypatch, post, *, identity=(), media=()):
    monkeypatch.setattr(
        cockpit.store, "run", stream_of(publish_block(post, media, identity=identity))
    )
    msg = FakeMsg(CHAT_ID, text="draft the post")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))
    return msg


def _press(cockpit, data):
    update, context, query = callback_update(CHAT_ID, data)
    asyncio.run(cockpit.on_callback(update, context))
    return query


@pytest.mark.parametrize("identity", [("soul",), ("generated",)])
def test_a_post_with_synthetic_identity_and_no_disclosure_line_in_the_caption_is_refused(
    monkeypatch, tmp_path, identity
):
    """`generated` (§3.2 fix) — an AI-generated/restyled asset with no likeness/voice handle —
    must trip the identical refusal as `soul`. Before the fix this token did not exist, so a
    video-restyle asset had nothing whitelisted to carry and the check was a silent no-op."""
    cockpit = _make_cockpit_with_kit(tmp_path, publisher=FakePublisher())
    post = "A post about our new feature, no disclosure text here."
    msg = _stage(cockpit, monkeypatch, post, identity=identity)

    assert cockpit._pending_publish == {}
    reply = "\n".join(msg.replies)
    assert "Not staged for publish" in reply
    assert "disclosure" in reply.lower()


@pytest.mark.parametrize("identity", [("soul", "voice"), ("generated",)])
def test_a_post_with_the_disclosure_line_present_stages_and_shows_the_confirmation(
    monkeypatch, tmp_path, identity
):
    cockpit = _make_cockpit_with_kit(tmp_path, publisher=FakePublisher())
    post = f"A post about our new feature. {DISCLOSURE_LINE}"
    msg = _stage(cockpit, monkeypatch, post, identity=identity)

    token = content_hash(post, ())[:16]
    assert token in cockpit._pending_publish
    preview = "\n".join(msg.replies)
    assert "disclosed" in preview.lower()
    assert "✓" in preview


def test_approving_a_disclosed_post_publishes_the_exact_bytes(monkeypatch, tmp_path):
    publisher = FakePublisher()
    cockpit = _make_cockpit_with_kit(tmp_path, publisher=publisher)
    post = f"A post about our new feature. {DISCLOSURE_LINE}"
    _stage(cockpit, monkeypatch, post, identity=("soul",))

    token = content_hash(post, ())[:16]
    _press(cockpit, f"pub:ok:{token}")

    assert len(publisher.calls) == 1
    published_post, media, _ = publisher.calls[0]
    assert published_post == post
    assert media == ()


def test_identity_used_but_no_disclosure_line_configured_at_all_is_refused(monkeypatch, tmp_path):
    """Fail-closed: an operator who never set [disclosure].line has not opted out of
    the duty, they just haven't configured how to meet it — refuse, don't pass."""
    cockpit = _make_cockpit_with_kit(tmp_path, publisher=FakePublisher(), disclosure_line=None)
    msg = _stage(cockpit, monkeypatch, "Any post text at all.", identity=("voice",))

    assert cockpit._pending_publish == {}
    reply = "\n".join(msg.replies)
    assert "Not staged for publish" in reply
    assert "no disclosure line is configured" in reply


def test_no_identity_marker_stages_exactly_as_before_the_check_existed(monkeypatch, tmp_path):
    """Regression pin: an ordinary, non-synthetic post is completely unaffected by
    the disclosure check — identity_used defaults to () and the check is a no-op."""
    cockpit = _make_cockpit_with_kit(tmp_path, publisher=FakePublisher())
    post = "A completely ordinary post with no synthetic media at all."
    msg = _stage(cockpit, monkeypatch, post)

    token = content_hash(post, ())[:16]
    assert token in cockpit._pending_publish
    preview = "\n".join(msg.replies)
    assert "disclosed" not in preview.lower()


def test_disclosure_is_re_verified_at_dispatch_not_only_trusted_from_staging(monkeypatch, tmp_path):
    """New (backend disclosure-gap fix): dispatch_approved_publish now re-checks
    disclosure itself, so a brand kit edited (or emptied) between staging and the
    Approve press is caught, not just trusted from the staging-time check."""
    publisher = FakePublisher()
    cockpit = _make_cockpit_with_kit(tmp_path, publisher=publisher)
    post = f"A post about our new feature. {DISCLOSURE_LINE}"
    _stage(cockpit, monkeypatch, post, identity=("soul",))

    # The disclosure line disappears between staging and the approve press.
    knowledge = cockpit.cfg.profiles_root / "example" / "knowledge"
    (knowledge / "BRAND.toml").write_text('[disclosure]\nline = ""\n', encoding="utf-8")

    token = content_hash(post, ())[:16]
    query = _press(cockpit, f"pub:ok:{token}")

    assert publisher.calls == []
    reply = "\n".join(query.message.replies)
    assert "not published" in reply.lower()
    assert "disclos" in reply.lower()


def test_a_product_kits_own_disclosure_line_is_accepted_when_it_differs_from_companys(
    monkeypatch, tmp_path
):
    """The cockpit doesn't know which kit (company vs a specific product) produced
    the draft's identity — it must accept ANY configured line, including a
    product-specific override the company kit doesn't carry."""
    cfg = make_cfg(tmp_path, chat_ids={CHAT_ID})
    knowledge = cfg.profiles_root / "example" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "BRAND.toml").write_text(
        '[disclosure]\nline = "Company line."\n', encoding="utf-8"
    )
    product = cfg.profiles_root / "example" / "products" / "widget"
    product.mkdir(parents=True)
    (product / "BRAND.toml").write_text(
        '[disclosure]\nline = "Widget-specific disclosure text."\n', encoding="utf-8"
    )
    # Declare the product in PROFILE.md so load_products finds it (agent.profiles'
    # inline-mapping shape: `- { slug: ..., name: ... }`, not YAML block style).
    (cfg.profiles_root / "example" / "PROFILE.md").write_text(
        "products:\n- { slug: widget, name: Widget }\n\nother: value\n", encoding="utf-8"
    )

    cockpit = botmod.Cockpit(cfg)
    cockpit._publisher = FakePublisher()
    post = "A post about Widget. Widget-specific disclosure text."
    msg = _stage(cockpit, monkeypatch, post, identity=("element",))

    token = content_hash(post, ())[:16]
    assert token in cockpit._pending_publish
    assert "disclosed" in "\n".join(msg.replies).lower()
