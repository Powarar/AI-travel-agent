"""Who is calling: a registered user (Bearer JWT) or a Telegram Mini App user (signed initData)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from .auth import InvalidToken, decode_access

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


def get_owner(authorization: str | None = Header(default=None)) -> str | None:
    """`Authorization: Bearer <jwt>` → `user:<uuid>`; `Authorization: tma <initData>` → `tg:<id>`.

    Подпись Telegram и JWT проверяются всегда: неверные — 401, а не тихий откат.
    Анонимного доступа больше нет: без заголовка владелец не определён (None).
    """
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    scheme = scheme.lower()
    if scheme == "tma":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise HTTPException(status_code=401, detail="Вход через Telegram не настроен")
        try:
            user = verify_init_data(value, token)
        except InvalidInitData as exc:
            raise HTTPException(status_code=401, detail="Не удалось подтвердить пользователя Telegram") from exc
        return f"tg:{user['id']}"
    if scheme == "bearer":
        try:
            user_id = decode_access(value)
        except InvalidToken as exc:
            raise HTTPException(status_code=401, detail="Не удалось подтвердить пользователя") from exc
        return f"user:{user_id}"
    return None


def require_owner(authorization: str | None = Header(default=None)) -> str:
    owner = get_owner(authorization)
    if owner is None:
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя")
    return owner
