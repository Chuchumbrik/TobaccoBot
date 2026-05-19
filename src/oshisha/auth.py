"""Авторизация на oshisha.cc (Bitrix + ctweb/sms.authorize)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

AUTH_AJAX_PATH = "/local/components/ctweb/sms.authorize/ajax.php"
SESSID_RE = re.compile(r'"bitrix_sessid"\s*:\s*"([a-f0-9]+)"')
STATE_SUCCESS = "4"


class OshishaAuthError(Exception):
    """Ошибка входа или сессии."""


class OshishaAuth:
    """
    Клиент авторизации Oshisha.

    Поддерживаемые методы (как на сайте):
    - EMAIL_AUTH — email + пароль
    - SMS — телефон + код (send_code / verify_code)
  """

    def __init__(
        self,
        base_url: str = "https://oshisha.cc",
        *,
        session_file: Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_file = session_file
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
            follow_redirects=True,
        )
        if session_file and session_file.exists():
            self._load_cookies(session_file)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OshishaAuth:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _load_cookies(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for cookie in data.get("cookies", []):
            self._client.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )

    def save_session(self, path: Path | None = None) -> None:
        path = path or self.session_file
        if not path:
            raise OshishaAuthError("Не задан путь для сохранения сессии")
        path.parent.mkdir(parents=True, exist_ok=True)
        cookies = [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path or "/",
            }
            for c in self._client.cookies.jar
        ]
        path.write_text(
            json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def fetch_sessid(self) -> str:
        """Получить bitrix_sessid со страницы (нужен PHPSESSID в cookies)."""
        url = f"{self.base_url}/?referer=retail/"
        resp = self._client.get(url)
        resp.raise_for_status()
        match = SESSID_RE.search(resp.text)
        if not match:
            raise OshishaAuthError("Не удалось найти bitrix_sessid на странице")
        return match.group(1)

    def _post_auth(self, data: dict[str, str]) -> dict[str, Any]:
        url = urljoin(self.base_url, AUTH_AJAX_PATH)
        payload = {**data, "is_ajax_post": "Y"}
        resp = self._client.post(
            url,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/?referer=retail/",
            },
        )
        resp.raise_for_status()
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise OshishaAuthError(f"Некорректный ответ сервера: {resp.text[:200]}") from exc

    def login_email(self, email: str, password: str, *, save_session: bool = True) -> dict[str, Any]:
        """
        Вход по email и паролю (METHOD=EMAIL_AUTH).

        При успехе сервер выставляет authToken (JWT) и BITRIX_SM_LOGIN.
        """
        sessid = self.fetch_sessid()
        result = self._post_auth(
            {
                "sessid": sessid,
                "METHOD": "EMAIL_AUTH",
                "EMAIL": email,
                "PASSWORD": password,
                "SAVE_SESSION": "Y" if save_session else "N",
            }
        )
        if str(result.get("STEP")) != STATE_SUCCESS:
            errors = result.get("ERRORS") or []
            raise OshishaAuthError(
                f"Вход не выполнен. STEP={result.get('STEP')}, errors={errors}"
            )
        if self.session_file:
            self.save_session()
        return result

    def send_sms_code(self, phone: str) -> dict[str, Any]:
        """Запросить SMS-код на телефон."""
        sessid = self.fetch_sessid()
        return self._post_auth(
            {
                "sessid": sessid,
                "method": "getCode",
                "PHONE": phone,
            }
        )

    def verify_sms_code(self, phone: str, code: str) -> dict[str, Any]:
        """Подтвердить SMS-код."""
        sessid = self.fetch_sessid()
        result = self._post_auth(
            {
                "sessid": sessid,
                "method": "checkCode",
                "PHONE": phone,
                "CODE": code,
            }
        )
        if str(result.get("STEP")) != STATE_SUCCESS:
            errors = result.get("ERRORS") or []
            raise OshishaAuthError(
                f"Код не принят. STEP={result.get('STEP')}, errors={errors}"
            )
        if self.session_file:
            self.save_session()
        return result

    @property
    def auth_token(self) -> str | None:
        return self._client.cookies.get("authToken")

    @property
    def is_authenticated(self) -> bool:
        return bool(self.auth_token or self._client.cookies.get("BITRIX_SM_LOGIN"))

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """HTTP GET с текущей сессией (для будущего парсера)."""
        url = path if path.startswith("http") else urljoin(self.base_url, path)
        return self._client.get(url, **kwargs)
