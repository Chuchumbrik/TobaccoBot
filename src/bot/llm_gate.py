"""Лимиты вызовов LLM на пользователя."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from telegram.ext import ContextTypes

from bot.config import BotConfig

KEY_LLM_WINDOW = "llm_rate_window"


@dataclass
class LlmRateState:
    window_start: float
    count: int


def _max_per_hour() -> int:
    return int(os.environ.get("LLM_MAX_PER_USER_PER_HOUR", "40"))


def _max_per_request() -> int:
    return int(os.environ.get("LLM_MAX_CALLS_PER_REQUEST", "3"))


def is_llm_exempt(config: BotConfig, user_id: int) -> bool:
    return bool(config.telegram_admin_ids) and user_id in config.telegram_admin_ids


def check_llm_allowed(
    context: ContextTypes.DEFAULT_TYPE,
    config: BotConfig,
    user_id: int,
    *,
    calls: int = 1,
) -> bool:
    """Проверка часового лимита без списания (списание — после успешного LLM)."""
    if user_id <= 0 or is_llm_exempt(config, user_id):
        return True
    st: LlmRateState | None = context.user_data.get(KEY_LLM_WINDOW)
    now = time.time()
    if st is None or now - st.window_start >= 3600:
        return True
    return st.count + calls <= _max_per_hour()


def record_llm_hourly_use(
    context: ContextTypes.DEFAULT_TYPE,
    config: BotConfig,
    user_id: int,
    *,
    calls: int = 1,
) -> None:
    if user_id <= 0 or is_llm_exempt(config, user_id) or calls <= 0:
        return
    st: LlmRateState | None = context.user_data.get(KEY_LLM_WINDOW)
    now = time.time()
    if st is None or now - st.window_start >= 3600:
        st = LlmRateState(window_start=now, count=0)
    st.count += calls
    context.user_data[KEY_LLM_WINDOW] = st


def bind_llm_gate(
    context: ContextTypes.DEFAULT_TYPE,
    config: BotConfig,
    user_id: int,
) -> None:
    """Привязка контекста: успешные вызовы LLM списывают часовой лимит."""
    from oshisha import llm as llm_mod

    def _on_success(n: int) -> None:
        record_llm_hourly_use(context, config, user_id, calls=n)

    llm_mod.set_hourly_quota_hook(_on_success)


def unbind_llm_gate() -> None:
    from oshisha import llm as llm_mod

    llm_mod.set_hourly_quota_hook(None)


def begin_request_llm_budget(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    config: BotConfig | None = None,
    user_id: int | None = None,
) -> None:
    context.user_data["llm_req_count"] = 0
    if config is not None and user_id is not None:
        bind_llm_gate(context, config, user_id)


def end_request_llm_budget() -> None:
    unbind_llm_gate()


def remaining_request_llm_budget(context: ContextTypes.DEFAULT_TYPE) -> int:
    used = int(context.user_data.get("llm_req_count", 0))
    return max(0, _max_per_request() - used)


def consume_request_llm_budget(context: ContextTypes.DEFAULT_TYPE, n: int = 1) -> bool:
    used = int(context.user_data.get("llm_req_count", 0))
    if used + n > _max_per_request():
        return False
    context.user_data["llm_req_count"] = used + n
    return True


def reserve_request_llm_budget(context: ContextTypes.DEFAULT_TYPE, n: int) -> bool:
    """Резервирует до n слотов LLM в рамках одного пользовательского шага."""
    if n <= 0:
        return True
    return consume_request_llm_budget(context, n)
