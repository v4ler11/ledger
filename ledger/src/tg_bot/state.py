from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict

import aiohttp

if TYPE_CHECKING:
    from tg_bot.handlers.any_text import _AlbumBuffer


@dataclass
class State:
    http_session: aiohttp.ClientSession
    album_buffers: Dict[str, "_AlbumBuffer"] = field(default_factory=dict)
