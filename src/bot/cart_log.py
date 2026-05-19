"""Журнал добавлений в корзину из Telegram."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oshisha.cart import CartAddBatchResult, CartAddResult

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = ROOT / "data" / "cart_log.jsonl"
MAX_LOG_LINES = 5000


@dataclass
class CartLogEntry:
    ts: str
    telegram_user_id: int
    username: str | None
    full_name: str | None
    query: str
    success: bool
    message: str
    product_id: str | None = None
    product_name: str | None = None
    quantity: int = 0
    line_price: int | None = None

    def display_user(self) -> str:
        if self.username:
            return f"@{self.username}"
        if self.full_name:
            return self.full_name
        return str(self.telegram_user_id)


class CartLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_LOG_PATH

    def append_entries(self, entries: list[CartLogEntry]) -> None:
        if not entries:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        self._trim_if_needed()

    def _trim_if_needed(self) -> None:
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= MAX_LOG_LINES:
            return
        self.path.write_text(
            "\n".join(lines[-MAX_LOG_LINES:]) + "\n",
            encoding="utf-8",
        )

    def read_recent(
        self,
        limit: int = 30,
        *,
        telegram_user_id: int | None = None,
    ) -> list[CartLogEntry]:
        if not self.path.exists():
            return []
        rows: list[CartLogEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entry = CartLogEntry(**data)
            except (json.JSONDecodeError, TypeError):
                continue
            if telegram_user_id is not None and entry.telegram_user_id != telegram_user_id:
                continue
            rows.append(entry)
        return rows[-limit:]


def entries_from_batch(
    batch: CartAddBatchResult,
    *,
    telegram_user_id: int,
    username: str | None,
    full_name: str | None,
) -> list[CartLogEntry]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[CartLogEntry] = []
    for item in batch.items:
        out.append(
            CartLogEntry(
                ts=now,
                telegram_user_id=telegram_user_id,
                username=username,
                full_name=full_name,
                query=item.query,
                success=item.success,
                message=item.message if item.message != "_pending" else "добавлено",
                product_id=item.product_id,
                product_name=item.matched_name,
                quantity=item.quantity,
                line_price=item.line_price,
            )
        )
    return out


def get_cart_log(context: Any, path: Path | None = None) -> CartLog:
    key = "cart_log"
    log = context.application.bot_data.get(key)
    if log is None:
        log = CartLog(path)
        context.application.bot_data[key] = log
    return log
