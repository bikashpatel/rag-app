"""One chat answer's plugin state (brief 4.4): the numbered sources (document passages, then emails), the cards, the
side effects Stop must wait for, whether the answer has seen untrusted text, and its request check."""

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.models import LLMModel, User
from app.plugins import tasks
from app.plugins.base import PluginEvent
from app.plugins.intent import RequestCheck
from app.schemas import AnswerSource, EmailSource, GeneratedFile, Source
from app.schemas_plugins import PluginCard

logger = logging.getLogger(__name__)

T = TypeVar("T")

# "[n]" not followed by "(" (a link): what the client turns into citation chips (frontend citations.ts).
_CITATION = re.compile(r"\[(\d{1,4})\](?!\()")


def cited_numbers(text: str) -> set[int]:
    return {int(found) for found in _CITATION.findall(text)}


class SourceRegistry:
    """The answer's sources: the retrieved passages (1..k, never changed), then the emails plugin tools returned
    (k+1..., one number per message, in order of first appearance)."""

    def __init__(self, documents: Sequence[Source]) -> None:
        self._entries: list[AnswerSource] = list(documents)
        self._documents = len(self._entries)
        self._keys: dict[str, int] = {}
        self._next = max((source.index for source in documents), default=0) + 1

    @property
    def entries(self) -> list[AnswerSource]:
        """Documents then emails; the same list object for the whole answer."""
        return self._entries

    def add_email(self, key: str, make: Callable[[int], EmailSource]) -> tuple[int, bool]:
        """Register an email (key "<plugin>:<message id>"); returns (its number, whether it is new). A known key keeps
        its number."""
        if key in self._keys:
            return self._keys[key], False
        index = self._next
        self._next += 1
        self._entries.append(make(index))
        self._keys[key] = index
        return index, True

    def event(self) -> PluginEvent:
        return PluginEvent("sources", {"sources": [source.model_dump(mode="json") for source in self._entries]})

    def final(self, text: str) -> list[AnswerSource]:
        """The documents and the emails whose number the answer text cites."""
        cited = cited_numbers(text)
        return self._entries[: self._documents] + [
            source for source in self._entries[self._documents :] if source.index in cited
        ]


class CardSink:
    """The answer's cards (replaced by card_id, in first-put order) and the side effects Stop waits for."""

    def __init__(self) -> None:
        self._cards: dict[str, tuple[str, PluginCard]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self.stopping = asyncio.Event()  # set by Stop: long runs (imports) stop between items

    def put(self, plugin_id: str, card: PluginCard) -> PluginEvent:
        self._cards[card.card_id] = (plugin_id, card)
        return PluginEvent("plugin", card.model_dump(mode="json"))

    def cards(self) -> list[tuple[str, PluginCard]]:
        return list(self._cards.values())

    def track(
        self, work: "Coroutine[Any, Any, T] | asyncio.Task[T]", *, name: str = "plugin action"
    ) -> "asyncio.Task[T]":
        """A side effect the saved answer must wait for: a coroutine is started with tasks.spawn (so shutdown drains it
        too); a task is tracked as it is. Await the returned task through asyncio.shield."""
        task = work if isinstance(work, asyncio.Task) else tasks.spawn(work, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def settle(self, timeout: float = 90.0) -> None:
        """Wait for the tracked side effects (logs the ones still running after `timeout` seconds)."""
        running = {task for task in self._tasks if not task.done()}
        if not running:
            return
        _, pending = await asyncio.wait(running, timeout=timeout)
        if pending:
            logger.warning("Saving an answer while %d plugin action(s) still run", len(pending))


@dataclass
class Taint:
    tainted: bool = False  # the answer has seen untrusted text (email returned by a tool, or an imported document)


@dataclass
class AnswerContext:
    user: User
    chat_model: LLMModel
    conversation_id: int
    user_message: str  # the turn's message
    sources: SourceRegistry
    cards: CardSink
    taint: Taint
    request_check: RequestCheck
    # The files and images this answer created so far: the answer's own list (document_tool and image_tool append).
    created_files: list[GeneratedFile] = field(default_factory=list)
