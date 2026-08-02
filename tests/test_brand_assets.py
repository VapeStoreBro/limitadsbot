from app.services import price_card


def test_brand_asset_filenames_are_stable() -> None:
    assert price_card.PRICE_CARD_PATH.name == "limit_price.jpg"
    assert price_card.MAIN_MENU_PATH.name == "limit_main_menu.jpg"
