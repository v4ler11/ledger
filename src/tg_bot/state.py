from dataclasses import dataclass

from aiogram.client.session import aiohttp


@dataclass
class State:
    http_session: aiohttp.ClientSession
