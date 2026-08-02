import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.enums import TariffCode

URL_PATTERN = re.compile(r"(?i)(?:https?://|www\.|t\.me/|telegram\.me/|tg://)")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?7|8)[\s\-()]*(?:\d[\s\-()]*){10}(?!\d)")


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    error: str | None = None


def validate_post(tariff_code: str, text: str, media: list[dict], buttons: list[dict] | None = None) -> ValidationResult:
    buttons = buttons or []
    if not text.strip() and not media:
        return ValidationResult(False, "Пост не может быть пустым.")
    if media and len(text) > 1024:
        return ValidationResult(False, "Подпись к фото должна помещаться в 1024 символа Telegram.")
    if not media and len(text) > 4096:
        return ValidationResult(False, "Текст должен помещаться в одно сообщение Telegram.")
    if len(media) > 8:
        return ValidationResult(False, "Можно добавить не больше 8 фотографий.")
    if any(item.get("type") != "photo" for item in media):
        return ValidationResult(False, "Разрешены только фотографии. Видео и GIF запрещены.")
    if tariff_code == TariffCode.STANDARD.value:
        if URL_PATTERN.search(text):
            return ValidationResult(False, "В Standard запрещены активные ссылки.")
        if PHONE_PATTERN.search(text):
            return ValidationResult(False, "В Standard запрещены номера телефонов.")
        if buttons:
            return ValidationResult(False, "Кнопки доступны только в Best.")
    if tariff_code == TariffCode.MIDDLE.value and buttons:
        return ValidationResult(False, "Кнопки доступны только в Best.")
    if tariff_code == TariffCode.BEST.value:
        if len(buttons) > 2:
            return ValidationResult(False, "В Best разрешено максимум две кнопки.")
        for button in buttons:
            if not button.get("text", "").strip() or not URL_PATTERN.search(button.get("url", "")):
                return ValidationResult(False, "У каждой кнопки должны быть название и ссылка.")
    return ValidationResult(True)


def advertising_prefix(ends_at: datetime, timezone_name: str = "Europe/Moscow") -> str:
    local = ends_at.astimezone(ZoneInfo(timezone_name))
    value = f"Реклама до {local:%d.%m}"
    if len(value) > 16:
        raise ValueError("Telegram custom title exceeds 16 characters")
    return value
