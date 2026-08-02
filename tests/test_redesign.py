from PIL import Image

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


def test_price_card_is_generated(tmp_path, monkeypatch) -> None:
    target = tmp_path / "limit-price.png"
    monkeypatch.setattr(price_card, "CARD_PATH", target)
    generated = price_card.ensure_price_card()
    assert generated == target
    assert generated.exists()
    with Image.open(generated) as image:
        assert image.size == (1200, 1200)
        assert image.format == "PNG"
