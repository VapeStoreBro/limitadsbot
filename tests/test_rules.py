from datetime import datetime, timezone

from app.enums import TariffCode
from app.rules import advertising_prefix, validate_post


def test_standard_rejects_links_and_phone() -> None:
    assert not validate_post(TariffCode.STANDARD.value, "https://example.com", [], []).ok
    assert not validate_post(TariffCode.STANDARD.value, "+7 999 123-45-67", [], []).ok
    assert validate_post(TariffCode.STANDARD.value, "Пишите @username", [], []).ok


def test_best_accepts_two_buttons() -> None:
    result = validate_post(
        TariffCode.BEST.value,
        "Реклама",
        [],
        [
            {"text": "Контакт", "url": "https://t.me/example"},
            {"text": "Канал", "url": "https://t.me/channel"},
        ],
    )
    assert result.ok


def test_prefix_fits_telegram_limit() -> None:
    prefix = advertising_prefix(datetime(2026, 8, 31, tzinfo=timezone.utc))
    assert prefix == "Реклама до 31.08"
    assert len(prefix) <= 16
