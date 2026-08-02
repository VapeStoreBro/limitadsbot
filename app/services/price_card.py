from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
BRAND_DIR = PROJECT_DIR / "assets" / "brand"
RUNTIME_DIR = PROJECT_DIR / "runtime"

PRICE_CARD_PATH = BRAND_DIR / "limit_price.jpg"
MAIN_MENU_PATH = BRAND_DIR / "limit_main_menu.jpg"
LEGACY_PRICE_PATH = RUNTIME_DIR / "limit_ads_price.png"


def ensure_price_card() -> Path:
    """Return the approved advertising price image.

    During a rolling deploy the previous generated card remains a safe fallback
    until the approved brand assets are installed on the server.
    """
    if PRICE_CARD_PATH.is_file():
        return PRICE_CARD_PATH
    if LEGACY_PRICE_PATH.is_file():
        return LEGACY_PRICE_PATH
    raise FileNotFoundError(
        f"Price image is missing. Install it at {PRICE_CARD_PATH}."
    )


def ensure_main_menu_card() -> Path | None:
    """Return the approved buyer main-menu image when installed."""
    return MAIN_MENU_PATH if MAIN_MENU_PATH.is_file() else None
