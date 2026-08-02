from pathlib import Path

from app.keyboards import tariff_selection_keyboard
from app.services import price_card


def test_selected_tariff_uses_success_style() -> None:
    keyboard = tariff_selection_keyboard(
        "middle",
        {"day": 700, "week": 1400, "month": 2000},
    )
    selected = keyboard.inline_keyboard[0][1]
    assert selected.text.startswith("✅")
    assert selected.style == "success"


def test_approved_brand_assets_are_selected(tmp_path, monkeypatch) -> None:
    price = tmp_path / "limit_price.jpg"
    menu = tmp_path / "limit_main_menu.jpg"
    price.write_bytes(b"price")
    menu.write_bytes(b"menu")

    monkeypatch.setattr(price_card, "PRICE_CARD_PATH", price)
    monkeypatch.setattr(price_card, "MAIN_MENU_PATH", menu)
    monkeypatch.setattr(price_card, "LEGACY_PRICE_PATH", tmp_path / "missing.png")

    assert price_card.ensure_price_card() == price
    assert price_card.ensure_main_menu_card() == menu


def test_legacy_price_is_safe_fallback(tmp_path, monkeypatch) -> None:
    approved = tmp_path / "missing.jpg"
    legacy = tmp_path / "legacy.png"
    legacy.write_bytes(b"legacy")

    monkeypatch.setattr(price_card, "PRICE_CARD_PATH", approved)
    monkeypatch.setattr(price_card, "LEGACY_PRICE_PATH", legacy)

    assert price_card.ensure_price_card() == legacy
