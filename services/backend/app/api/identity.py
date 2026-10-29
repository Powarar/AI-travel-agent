"""Who is calling: a Telegram Mini App user (signed initData) or an anonymous browser."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import Header, HTTPException

#: initData Telegram выдаёт при каждом открытии Mini App; старше суток — не принимаем.
INIT_DATA_TTL_S = 24 * 3600


class InvalidInitData(Exception):
    pass


def verify_init_data(init_data: str, bot_token: str, now: float | None = None) -> dict[str, object]:
    """Проверка подписи по документации Telegram: HMAC-SHA256 от отсортированных полей без hash,
    ключ — HMAC-SHA256("WebAppData", bot_token). Возвращает пользователя из initData."""
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received = fields.pop("hash", "")
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(expected, received):
        raise InvalidInitData("bad signature")
    auth_date = int(fields.get("auth_date", "0") or 0)
    if (now if now is not None else time.time()) - auth_date > INIT_DATA_TTL_S:
        raise InvalidInitData("expired")
    try:
        user = json.loads(fields["user"])
    except (KeyError, ValueError) as exc:
        raise InvalidInitData("no user") from exc
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise InvalidInitData("no user id")
    return user


def get_owner(authorization: str | None = Header(default=None),
              x_client_id: str | None = Header(default=None)) -> str | None:
    """`Authorization: tma <initData>` → `tg:<id>`; `X-Client-Id: <uuid>` → `anon:<uuid>`.
    Подпись Telegram проверяется всегда: неверная — 401, а не тихий откат к анониму."""
    if authorization and authorization.startswith("tma "):
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise HTTPException(status_code=401, detail="Вход через Telegram не настроен")
        try:
            user = verify_init_data(authorization[4:], token)
        except InvalidInitData as exc:
            raise HTTPException(status_code=401, detail="Не удалось подтвердить пользователя Telegram") from exc
        return f"tg:{user['id']}"
    if x_client_id:
        try:
            return f"anon:{UUID(x_client_id)}"
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Некорректные параметры запроса") from exc
    return None


def require_owner(authorization: str | None = Header(default=None),
                  x_client_id: str | None = Header(default=None)) -> str:
    owner = get_owner(authorization, x_client_id)
    if owner is None:
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя")
    return owner
