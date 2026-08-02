import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

from aiogram.types import Message


class MediaGroupCollector:
    def __init__(self) -> None:
        self._messages: dict[tuple[int, str], list[Message]] = defaultdict(list)
        self._tasks: dict[tuple[int, str], asyncio.Task] = {}

    def add(self, message: Message, callback: Callable[[list[Message]], Awaitable[None]]) -> None:
        if not message.media_group_id or not message.from_user:
            raise ValueError("Message is not part of a media group")
        key = (message.from_user.id, message.media_group_id)
        self._messages[key].append(message)
        previous = self._tasks.get(key)
        if previous:
            previous.cancel()
        self._tasks[key] = asyncio.create_task(self._finish(key, callback))

    async def _finish(self, key: tuple[int, str], callback: Callable[[list[Message]], Awaitable[None]]) -> None:
        try:
            await asyncio.sleep(1.2)
            messages = sorted(self._messages.pop(key, []), key=lambda item: item.message_id)
            self._tasks.pop(key, None)
            if messages:
                await callback(messages)
        except asyncio.CancelledError:
            raise
