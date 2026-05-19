"""Контекст последних результатов для inline-действий."""

from __future__ import annotations

from telegram.ext import ContextTypes

from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult

KEY_FLAVOR_HITS = "sc_fh"
KEY_FLAVOR_QUERY = "sc_fq"
KEY_CHECKS = "sc_ch"
KEY_PICK_MSG_ID = "sc_pick_msg"


def save_flavor_search(
    context: ContextTypes.DEFAULT_TYPE,
    result: FlavorSearchResult,
) -> None:
    context.user_data[KEY_FLAVOR_QUERY] = result.query
    context.user_data[KEY_FLAVOR_HITS] = list(result.hits)


def get_flavor_hits(context: ContextTypes.DEFAULT_TYPE) -> list[FlavorSearchHit]:
    return context.user_data.get(KEY_FLAVOR_HITS) or []


def save_checks(
    context: ContextTypes.DEFAULT_TYPE,
    results: list[ProductCheckResult],
) -> None:
    context.user_data[KEY_CHECKS] = list(results)


def get_checks(context: ContextTypes.DEFAULT_TYPE) -> list[ProductCheckResult]:
    return context.user_data.get(KEY_CHECKS) or []


def set_pick_message_id(context: ContextTypes.DEFAULT_TYPE, message_id: int) -> None:
    context.user_data[KEY_PICK_MSG_ID] = message_id


def get_pick_message_id(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    return context.user_data.get(KEY_PICK_MSG_ID)


def clear_pick_message_id(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(KEY_PICK_MSG_ID, None)


def clear_action_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(KEY_FLAVOR_HITS, None)
    context.user_data.pop(KEY_FLAVOR_QUERY, None)
    context.user_data.pop(KEY_CHECKS, None)
    clear_pick_message_id(context)
