from app.services import price_card


def test_missing_main_menu_is_safe(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(price_card, "MAIN_MENU_PATH", tmp_path / "missing.jpg")
    assert price_card.ensure_main_menu_card() is None
