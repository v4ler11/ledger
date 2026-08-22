import base64
import datetime
import os
from typing import List, Dict, Union

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


def make_user_message(text: str, image_data_url: str | None) -> ChatMessageUser:
    if image_data_url is None:
        return ChatMessageUser(content=text)

    parts: List[Union[ChatContentPartText, ChatContentPartImage]] = []
    if text:
        parts.append(ChatContentPartText(text=text))
    parts.append(ChatContentPartImage(image_url=ChatImageUrl(url=image_data_url)))
    return ChatMessageUser(content=parts)


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

@dp.message()
async def handler_any_text(
        message: types.Message,
        app_state: State,
        state: FSMContext,
) -> None:
    user_text = message.text or message.caption or ""
    chat_id = message.chat.id

    image_data_url = None
    if message.photo:
        photo = message.photo[-1]
        buf = await message.bot.download(photo.file_id)
        if buf is None:
            raise ValueError("failed to download photo")
        image_data_url = encode_image_to_base64_data_url(buf.getvalue(), "image/jpeg")

    if not user_text.strip() and image_data_url is None:
        await message.answer("Invalid message: text or image is required")
        return

    history_text = user_text or "[image]"
    fsm_data = await state.get_data()
    history = fsm_data.get("history", [])
    history.append({"role": "user", "content": history_text})
    await state.update_data(history=history)

    messages = history_into_chat_messages(history)
    messages = messages[-15:]
    if image_data_url is not None:
        messages[-1] = make_user_message(user_text, image_data_url)
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
