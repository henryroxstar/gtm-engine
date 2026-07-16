"""Shared network-free fakes for cockpit handler tests.

Deliberately telegram-import-free so this module (and the sibling conftest) can
be collected even when python-telegram-bot is not installed — each test module
still does its own ``pytest.importorskip("telegram")`` before importing
``cockpit.bot``. The fakes mirror just enough of the PTB message/query surface
for the handlers: ``reply_text`` returns the same object so it doubles as the
streamed placeholder (house style, see test_voice_handler.py).
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.publish import PublishResult, PublishSettings


def make_cfg(tmp_path, *, chat_ids, api_key=None, content_root=None, profiles_root=None):
    """A SimpleNamespace config with the fields Cockpit + SessionStore read."""
    return SimpleNamespace(
        telegram_allowed_chat_ids=set(chat_ids),
        elevenlabs_api_key=api_key,
        elevenlabs_voice_id="DefaultVoiceId0000000",
        default_profile="example",
        content_root=content_root or tmp_path,
        profiles_root=profiles_root or (tmp_path / "profiles"),
    )


class FakeMsg:
    """Stands in for the inbound message, the streamed placeholder, and previews."""

    def __init__(self, chat_id, text=None):
        self.chat_id = chat_id
        self.text = text
        self.caption = None
        self.voice = None
        self.photo = None
        self.document = None
        self.replies: list[str] = []
        self.reply_kwargs: list[dict] = []
        self.edits: list[str] = []
        self.edit_kwargs: list[dict] = []
        self.documents: list[tuple[str | None, bytes]] = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        self.reply_kwargs.append(kw)
        return self  # acts as the placeholder for _run_and_deliver

    async def edit_text(self, text, **kw):
        self.edits.append(text)
        self.edit_kwargs.append(kw)

    async def reply_document(self, document=None, filename=None, **kw):
        data = document.read() if hasattr(document, "read") else bytes(document or b"")
        self.documents.append((filename, data))
        return self


class FakeQuery:
    """Stands in for telegram.CallbackQuery in on_callback round-trips."""

    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answered = False
        self.markup_edits: list = []
        self.text_edits: list[str] = []
        self.text_edit_kwargs: list[dict] = []

    async def answer(self):
        self.answered = True

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_edits.append(reply_markup)

    async def edit_message_text(self, text, **kw):
        self.text_edits.append(text)
        self.text_edit_kwargs.append(kw)


class FakeBot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_chat_action(self, **_kw):
        return None

    async def send_voice(self, **_kw):  # only reachable if a test leaves TTS on
        return None

    async def send_message(self, **kw):
        self.sent.append(kw)


class FakeFile:
    def __init__(self, payload=b"ogg-bytes"):
        self._payload = payload

    async def download_as_bytearray(self):
        return bytearray(self._payload)


class FakeVoice:
    def __init__(self):
        self.file_size = 1024
        self.mime_type = "audio/ogg"

    async def get_file(self):
        return FakeFile()


def text_update(chat_id, msg):
    """(update, context) for a plain-text / command handler drive."""
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), message=msg)
    context = SimpleNamespace(bot=FakeBot(), args=[])
    return update, context


def callback_update(chat_id, data, msg=None):
    """(update, context, query) for an inline-keyboard press."""
    message = msg if msg is not None else FakeMsg(chat_id)
    query = FakeQuery(data, message)
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        callback_query=query,
        message=None,
    )
    context = SimpleNamespace(bot=FakeBot(), args=[])
    return update, context, query


def stream_of(*chunks):
    """A fake ``store.run`` yielding the given chunks."""

    async def _run(chat_id, prompt):
        for c in chunks:
            yield c

    return _run


def recording_stream(calls, *chunks):
    """A fake ``store.run`` that records (chat_id, prompt) then yields chunks."""

    async def _run(chat_id, prompt):
        calls.append((chat_id, prompt))
        for c in chunks:
            yield c

    return _run


def raising_stream(exc):
    """A fake ``store.run`` that raises before yielding anything."""

    async def _run(chat_id, prompt):
        raise exc
        yield  # pragma: no cover — makes this an async generator

    return _run


class FakePublisher:
    """Records publish() calls and returns a canned PublishResult.

    Carries a real (frozen) PublishSettings so ``_finalize_publish_gate`` can read
    ``settings.max_chars`` exactly as it does off the real LinkedInPublisher.
    """

    def __init__(self, result=None, max_chars=3000):
        self.settings = PublishSettings(max_chars=max_chars)
        self.result = result or PublishResult(ok=True, status="published", post_id="p1")
        self.calls: list[tuple] = []

    async def publish(self, post, media_urls=(), *, is_published=None):
        self.calls.append((post, tuple(media_urls), is_published))
        return self.result


def publish_block(post, media=(), prose="Drafted the post below."):
    """The exact ⟦GATE:publish⟧ block shape the content-publish skill emits."""
    block = f"{prose}\n⟦GATE:publish⟧\n⟦POST⟧\n{post}\n⟦/POST⟧"
    if media:
        block += "\n⟦MEDIA⟧\n" + "\n".join(media) + "\n⟦/MEDIA⟧"
    return block
