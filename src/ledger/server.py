import asyncio
import os

import aiohttp
from chat import ChatPost, ChatMessageUser, chat_completion_not_stream, chat_completion_not_stream_with_tools, ToolContext

from ledger import chat_tools

API_KEY  = os.environ.get("LEDGER_OPENROUTER_API_KEY")
MODEL = "openrouter/google/gemini-3.7-flash"

assert API_KEY is not None


async def main_async():
    messages = [
        ChatMessageUser(content="Today is Aug 21 '26. I bought a coffee using cash, it was 5 EUR")
    ]
    tools = chat_tools()

    post = ChatPost(
        model=MODEL,
        messages=messages,
        api_key=API_KEY,
        tools=[t.into_chat_tool() for t in tools]
    )
    # response, usage = await chat_completion_not_stream(post)

    session = aiohttp.ClientSession()
    ctx = ToolContext(session=session)
    resp, usage, new_msgs = await chat_completion_not_stream_with_tools(ctx, post, tools)

    print(new_msgs)

    print(resp)

    # print(resp.choices[0].message.content)
    # print(usage.model_dump())


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
