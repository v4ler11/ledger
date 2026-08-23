import asyncio
import base64
import datetime
import os
from typing import List, Dict, Optional, Union

from aiogram import types
from aiogram.fsm.context import FSMContext

from ledger import chat_tools
from tg_bot.state import State
from tg_bot.bot import dp
from chat import ChatMessage, ChatMessageUser, ChatMessageAssistant, ChatMessageSystem, ChatPost, ToolContext, \
    ChatContentPartText, ChatContentPartImage, ChatImageUrl, \
    chat_completion_not_stream_with_tools, chat_completion_not_stream


API_KEY  = os.environ.get("LEDGER_OPENROUTER_API_KEY")
MODEL = "openrouter/google/gemini-3.7-flash"
TARGET_USER_ID = 438796199

# How long to wait for the remaining album photos after the last one seen,
# before batching the whole group into one user message. Telegram delivers an
# album as several separate messages (sharing media_group_id) in a quick burst;
# this window lets them be merged.
ALBUM_SETTLE_SECONDS = 1.5


def history_into_chat_messages(history: List[Dict]) -> List[ChatMessage]:
    messages = []
    for msg in history:
        if not (role := msg.get("role")):
            raise ValueError(f"role is absent in msg: {msg}")

        if not (content := msg.get("content")):
            raise ValueError(f"content is absent in msg: {msg}")

        if role == "user":
            messages.append(ChatMessageUser(content=content))
        elif role == "assistant":
            messages.append(ChatMessageAssistant(content=content))
        else:
            raise ValueError(f"unknown role: {role} for message {msg}")

    return messages


def encode_image_to_base64_data_url(image_bytes: bytes, mime: str) -> str:
    """Encode raw image bytes as a base64 data URL, e.g. data:image/jpeg;base64,<b64>."""
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def make_user_message(
    text: str, image_data_urls: List[str] | None
) -> ChatMessageUser:
    if not image_data_urls:
        return ChatMessageUser(content=text)

    parts: List[Union[ChatContentPartText, ChatContentPartImage]] = []
    if text:
        parts.append(ChatContentPartText(text=text))
    parts.extend(
        ChatContentPartImage(image_url=ChatImageUrl(url=url))
        for url in image_data_urls
    )
    return ChatMessageUser(content=parts)


@dp.message()
async def handler_any_text(
        message: types.Message,
        app_state: State,
        state: FSMContext,
) -> None:
    user_text = message.text or message.caption or ""

    if message.from_user is None or message.from_user.id != TARGET_USER_ID:
        await message.answer("Not Authorized")
        return

    # Telegram sends an album as several separate photo messages sharing one
    # media_group_id. Buffer the group and let the settle window lapse before
    # collapsing all its photos into a single user message.
    if message.media_group_id and message.photo:
        _schedule_album_flush(message, app_state, state)
        return

    # Single (non-album) message: text and/or one photo.
    if not user_text.strip() and not message.photo:
        await message.answer("Invalid message: text or image is required")
        return

    image_data_urls: List[str] = []
    if message.photo:
        buf = await message.bot.download(message.photo[-1].file_id)
        if buf is None:
            raise ValueError("failed to download photo")
        image_data_urls.append(
            encode_image_to_base64_data_url(buf.getvalue(), "image/jpeg")
        )

    await respond(message, app_state, state, user_text, image_data_urls)


def _album_key(message: types.Message) -> str:
    return f"{message.chat.id}:{message.media_group_id}"


def _schedule_album_flush(
    message: types.Message, app_state: State, state: FSMContext
) -> None:
    """Append this message's photo to its album buffer and (re)arm the flush."""
    key = _album_key(message)
    album = app_state.album_buffers.get(key)
    if album is None:
        album = _AlbumBuffer(message)
        app_state.album_buffers[key] = album

    album.photo_file_ids.append(message.photo[-1].file_id)
    if album.text is None:
        album.text = message.caption or ""

    album.generation += 1
    gen = album.generation
    if album.task is not None and not album.task.done():
        album.task.cancel()
    album.task = asyncio.create_task(
        _flush_album_after_settle(album, gen, app_state, state)
    )


async def _flush_album_after_settle(
    album: "_AlbumBuffer", generation: int, app_state: State, state: FSMContext
) -> None:
    """Hold the album's photos open for the settle window, then process once.

    A newer photo cancels the pending task; the generation guard stops a task
    that already woke from flushing a group that grew since it was armed.
    """
    await asyncio.sleep(ALBUM_SETTLE_SECONDS)
    if album.generation != generation:
        return

    app_state.album_buffers.pop(_album_key(album.message), None)

    image_data_urls: List[str] = []
    for file_id in album.photo_file_ids:
        buf = await album.message.bot.download(file_id)
        if buf is None:
            raise ValueError("failed to download photo")
        image_data_urls.append(
            encode_image_to_base64_data_url(buf.getvalue(), "image/jpeg")
        )

    if not album.text.strip() and not image_data_urls:
        await album.message.answer("Invalid message: text or image is required")
        return

    await respond(
        album.message, app_state, state, album.text, image_data_urls
    )


async def respond(
    message: types.Message,
    app_state: State,
    state: FSMContext,
    user_text: str,
    image_data_urls: List[str],
) -> None:
    """Run the conversational routine for one logical user message."""
    history_text = user_text or "[image]"
    fsm_data = await state.get_data()
    history = fsm_data.get("history", [])
    history.append({"role": "user", "content": history_text})
    await state.update_data(history=history)

    messages = history_into_chat_messages(history)
    messages = messages[-15:]
    if image_data_urls:
        messages[-1] = make_user_message(user_text, image_data_urls)

    messages.insert(0, ChatMessageSystem(content=get_system_prompt()))

    tools = chat_tools()

    post = ChatPost(
        model=MODEL,
        messages=messages,
        api_key=API_KEY,
        tools=[t.into_chat_tool() for t in tools]
    )

    try:
        ctx = ToolContext(session=app_state.http_session)
        resp, usage, upd_msgs = await chat_completion_not_stream_with_tools(ctx, post, tools)
        if resp is None:
            raise ValueError("chat_completion_not_stream_with_tools response is empty")

        chat_says = resp.choices[0].message.content
        upd_msgs.append(ChatMessageAssistant(content=chat_says))

        new_msgs = upd_msgs[len(messages):]

        post = ChatPost(
            model=MODEL,
            messages=[ChatMessageUser(content=history_text), *new_msgs, ChatMessageUser(content=PROMPT_SUMMARIZE)],
            api_key=API_KEY,
        )

        resp, usage = await chat_completion_not_stream(post)
        if resp is None:
            raise ValueError("chat_completion_not_stream response is empty")

        chat_says = resp.choices[0].message.content

        await message.answer(chat_says)

        history.append({"role": "assistant", "content": chat_says})
        await state.update_data(history=history)

    except Exception as e:
        error_txt = f"Failed to produce response. Error : {e}"
        await message.answer(error_txt)
        history.append({"role": "assistant", "content": error_txt})
        await state.update_data(history=history)


class _AlbumBuffer:
    """Accumulates the photos (and caption) of one Telegram media group.

    ``message`` is the first message of the group (it carries the caption and
    is the target for the reply); ``photo_file_ids`` collects the largest size
    of every photo so a later flush can download them together.
    """

    __slots__ = ("message", "text", "photo_file_ids", "generation", "task")

    def __init__(self, message: types.Message):
        self.message = message
        self.text: Optional[str] = None
        self.photo_file_ids: List[str] = []
        self.generation = 0
        self.task: Optional[asyncio.Task] = None


def get_system_prompt() -> str:
    today_str = datetime.datetime.today().strftime("%b %d %Y %H:%M")

    return f"Current DT: {today_str}"


PROMPT_SUMMARIZE = """
Summarize model actions in the chat history, what tools were called, what actions are made, and results that are accomplished.
This is the only message user will see. So if model responds with something user asked for, propagate it to user.
Format response for telegram message

Examples:
🔧 list_accounts → retrieved chart of accounts ✔️ Accounts listed
🔧 add_account → opened Expenses:Food:Coffee (2026-01-01) ✔️ Account created
🔧 add_transaction → 5 EUR cash coffee purchase (2026-08-21) ✔️ Transaction posted: Cash:EUR -5.00 / Food:Coffee +5.00

🔧 list_accounts → retrieved chart of accounts ✔️ Accounts listed
🔧 add_account → attempted to open Expenses:Food:Coffee (2026-01-01) ❌ Error: account already exists (duplicate open directive)
🔧 add_transaction → 5 EUR cash coffee purchase (2026-08-21) ✔️ Transaction posted: Cash:EUR -5.00 / Food:Coffee +5.00

"""