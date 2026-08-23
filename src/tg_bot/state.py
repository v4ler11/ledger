from dataclasses import dataclass, field
from typing import Dict

from aiogram.client.session import aiohttp


@dataclass
class State:
    http_session: aiohttp.ClientSession
    album_buffers: Dict[str, object] = field(default_factory=dict)
