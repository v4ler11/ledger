"""Tests for image handling in the tg_bot any-text handler."""

import base64
from typing import cast

import aiohttp
from aiogram import types
from aiogram.fsm.context import FSMContext
from chat import ChatContentPartImage, ChatContentPartText, ChatImageUrl

from tg_bot.handlers.any_text import (
    encode_image_to_base64_data_url,
    history_into_chat_messages,
    make_user_message,
)

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF"


def test_encode_image_to_base64_data_url():
    url = encode_image_to_base64_data_url(JPEG_BYTES, "image/jpeg")
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.removeprefix("data:image/jpeg;base64,")) == JPEG_BYTES


def test_make_user_message_text_only_stays_str():
    msg = make_user_message("hello", None)
    assert msg.content == "hello"


def test_make_user_message_image_only():
    url = encode_image_to_base64_data_url(JPEG_BYTES, "image/jpeg")
    msg = make_user_message("", [url])
    assert msg.content == [ChatContentPartImage(image_url=ChatImageUrl(url=url))]


def test_make_user_message_image_with_caption():
    url = encode_image_to_base64_data_url(JPEG_BYTES, "image/jpeg")
    msg = make_user_message("what is this?", [url])
    assert msg.content == [
        ChatContentPartText(text="what is this?"),
        ChatContentPartImage(image_url=ChatImageUrl(url=url)),
    ]


def test_make_user_message_multiple_images_in_one_message():
    url1 = encode_image_to_base64_data_url(JPEG_BYTES, "image/jpeg")
    url2 = encode_image_to_base64_data_url(JPEG_BYTES + b"second", "image/jpeg")
    msg = make_user_message("compare", [url1, url2])
    assert msg.content == [
        ChatContentPartText(text="compare"),
        ChatContentPartImage(image_url=ChatImageUrl(url=url1)),
        ChatContentPartImage(image_url=ChatImageUrl(url=url2)),
    ]


def test_make_user_message_wire_payload_excludes_none_detail():
    """Serialization must match call_chat's exclude_none=True message dump."""
    url = encode_image_to_base64_data_url(JPEG_BYTES, "image/jpeg")
    dumped = make_user_message("desc", [url]).model_dump(exclude_none=True)
    assert dumped["content"] == [
        {"type": "text", "text": "desc"},
        {"type": "image_url", "image_url": {"url": url}},
    ]


def test_history_never_contains_images():
    history = [{"role": "user", "content": "[image]"}, {"role": "assistant", "content": "ok"}]
    messages = history_into_chat_messages(history)
    assert messages[0].content == "[image]"
    assert messages[1].content == "ok"


def test_album_photos_batched_into_one_message(monkeypatch):
    """An album (separate photo messages sharing media_group_id) must be
    collapsed into a single user message rather than one per photo."""
    import asyncio
    import io
    from types import SimpleNamespace

    from tg_bot.handlers import any_text as any_text_mod
    from tg_bot.state import State

    any_text_mod.ALBUM_SETTLE_SECONDS = 0

    calls = []

    class FakeBot:
        async def download(self, file_id):
            return io.BytesIO(b"\xff\xd8\xff\xe0" + file_id.encode())

    async def fake_respond(message, app_state, state, user_text, urls):
        calls.append((user_text, urls))

    monkeypatch.setattr(any_text_mod, "respond", fake_respond)

    def fake_message(route, group, caption, file_id):
        m = SimpleNamespace()
        m.chat = SimpleNamespace(id=route)
        m.from_user = SimpleNamespace(id=route)
        m.media_group_id = group
        m.caption = caption
        m.photo = [SimpleNamespace(file_id=file_id)]
        m.bot = FakeBot()
        return m

    async def scenario():
        app_state = State(http_session=cast(aiohttp.ClientSession, []))
        any_text_mod._schedule_album_flush(
            cast(types.Message, fake_message(1, "alb", "receipts", "f1")),
            app_state,
            cast(FSMContext, None),
        )
        any_text_mod._schedule_album_flush(
            cast(types.Message, fake_message(1, "alb", "", "f2")),
            app_state,
            cast(FSMContext, None),
        )
        album = app_state.album_buffers["1:alb"]
        # both photos accumulate; caption reflects the first message
        assert album.photo_file_ids == ["f1", "f2"]
        # second schedule cancelled the first, so only one task is pending
        assert album.task is not None
        await album.task
        # a single logical user message carries both images
        assert len(calls) == 1
        assert len(calls[0][1]) == 2
        assert all(u.startswith("data:image/jpeg;base64,") for u in calls[0][1])
        assert "1:alb" not in app_state.album_buffers

    asyncio.run(scenario())