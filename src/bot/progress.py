"""Прогресс-статус: пошаговая обратная связь в Telegram-сообщении."""
from __future__ import annotations

import logging

from telegram.constants import ParseMode
from telegram.error import BadRequest

logger = logging.getLogger(__name__)


class StepStatus:
    """Telegram-сообщение с анимированным чеклистом этапов.

    Пример:
        status = await send_status(update, "…")
        p = StepStatus(status, "🎯 <b>Заголовок</b>", ["Этап 1", "Этап 2"])
        await p.begin("Этап 1")                          # ⏳ Этап 1…
        decision = await llm_call()
        await p.begin("Этап 2")                          # ✅ Этап 1  ⏳ Этап 2…
        await p.done(note="Найдено 8")                   # ✅ Найдено 8
        await finish_status(status, ...)                 # заменяет сообщение результатом
    """

    PENDING = "◻️"
    ACTIVE  = "⏳"
    DONE    = "✅"

    def __init__(self, message, header: str, steps: list[str]) -> None:
        self.message = message
        self._header = header
        self._keys: list[str] = list(steps)
        self._labels: dict[str, str] = {s: s for s in steps}
        self._states: dict[str, str] = {s: self.PENDING for s in steps}
        self._current: str | None = None

    async def begin(self, step: str, *, note: str = "") -> None:
        """Завершить предыдущий этап ✅, начать новый ⏳."""
        if self._current and self._current != step:
            self._states[self._current] = self.DONE
        self._current = step
        self._states[step] = self.ACTIVE
        if note:
            self._labels[step] = note
        await self._edit()

    async def done(self, step: str | None = None, *, note: str = "") -> None:
        """Пометить этап ✅ (по умолчанию — текущий)."""
        target = step or self._current
        if not target:
            return
        self._states[target] = self.DONE
        if note:
            self._labels[target] = note
        if self._current == target:
            self._current = None
        await self._edit()

    def _render(self) -> str:
        lines = [self._header, ""]
        for key in self._keys:
            state = self._states[key]
            label = self._labels[key]
            suffix = "…" if state == self.ACTIVE else ""
            lines.append(f"{state} {label}{suffix}")
        return "\n".join(lines)

    async def _edit(self) -> None:
        try:
            await self.message.edit_text(self._render(), parse_mode=ParseMode.HTML)
        except BadRequest:
            pass
        except Exception as e:
            logger.debug("StepStatus._edit failed: %s", e)
