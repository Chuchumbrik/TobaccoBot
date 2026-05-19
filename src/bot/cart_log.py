"""Журнал добавлений в корзину из Telegram (сессии «заказов»)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from oshisha.cart import CartAddBatchResult, CartAddResult

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = ROOT / "data" / "cart_log.jsonl"
DEFAULT_STATE_PATH = ROOT / "data" / "cart_log_state.json"
MAX_LOG_LINES = 5000

# Москва (UTC+3, без перехода на летнее время с 2014 г.)
DISPLAY_TZ = timezone(timedelta(hours=3))


@dataclass
class CartLogState:
    """Текущая сессия журнала (один «заказ»)."""

    session_id: int = 1
    session_started_at: str = ""
    started_by_user_id: int | None = None
    started_by_username: str | None = None
    started_by_full_name: str | None = None


@dataclass
class CartLogEntry:
    ts: str
    telegram_user_id: int
    username: str | None
    full_name: str | None
    query: str
    success: bool
    message: str
    session_id: int = 1
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_ts_display(ts: str, *, tz=DISPLAY_TZ) -> str:
    """Время добавления для пользователя (МСК)."""
    try:
        raw = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        return ts.replace("T", " ").replace("Z", "")[:16]


def format_session_started(state: CartLogState) -> str:
    if state.session_started_at:
        return format_ts_display(state.session_started_at)
    return "—"


class CartLog:
    def __init__(
        self,
        path: Path | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.path = path or DEFAULT_LOG_PATH
        self.state_path = state_path or DEFAULT_STATE_PATH

    def load_state(self) -> CartLogState:
        if not self.state_path.exists():
            state = CartLogState(session_started_at=utc_now_iso())
            self.save_state(state)
            return state
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            state = CartLogState(
                session_id=int(data.get("session_id", 1)),
                session_started_at=str(data.get("session_started_at", "")),
                started_by_user_id=data.get("started_by_user_id"),
                started_by_username=data.get("started_by_username"),
                started_by_full_name=data.get("started_by_full_name"),
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            state = CartLogState(session_started_at=utc_now_iso())
            self.save_state(state)
        if not state.session_started_at:
            state = CartLogState(
                session_id=state.session_id,
                session_started_at=utc_now_iso(),
                started_by_user_id=state.started_by_user_id,
                started_by_username=state.started_by_username,
                started_by_full_name=state.started_by_full_name,
            )
            self.save_state(state)
        return state

    def save_state(self, state: CartLogState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def start_new_session(
        self,
        *,
        user_id: int,
        username: str | None,
        full_name: str | None,
    ) -> CartLogState:
        state = self.load_state()
        state = CartLogState(
            session_id=state.session_id + 1,
            session_started_at=utc_now_iso(),
            started_by_user_id=user_id,
            started_by_username=username,
            started_by_full_name=full_name,
        )
        self.save_state(state)
        return state

    def append_entries(self, entries: list[CartLogEntry]) -> None:
        if not entries:
            return
        session_id = self.load_state().session_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for entry in entries:
                row = asdict(entry)
                row.setdefault("session_id", session_id)
                row["session_id"] = session_id
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
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

    def _parse_line(self, line: str) -> CartLogEntry | None:
        line = line.strip()
        if not line:
            return None
        try:
            data = json.loads(line)
            if "query" not in data:
                return None
            if "session_id" not in data:
                data["session_id"] = 1
            return CartLogEntry(**data)
        except (json.JSONDecodeError, TypeError):
            return None

    def read_session(
        self,
        session_id: int | None = None,
        *,
        limit: int = 30,
        telegram_user_id: int | None = None,
    ) -> list[CartLogEntry]:
        if not self.path.exists():
            return []
        sid = session_id if session_id is not None else self.load_state().session_id
        rows: list[CartLogEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            entry = self._parse_line(line)
            if entry is None:
                continue
            if entry.session_id != sid:
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
    session_id: int,
) -> list[CartLogEntry]:
    now = utc_now_iso()
    out: list[CartLogEntry] = []
    for item in batch.items:
        out.append(
            CartLogEntry(
                ts=now,
                session_id=session_id,
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
        config_path = path
        if config_path is None:
            cfg = context.application.bot_data.get("bot_config")
            config_path = cfg.cart_log_path if cfg else None
        log = CartLog(config_path)
        context.application.bot_data[key] = log
    return log
