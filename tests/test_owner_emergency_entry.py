from app.config import get_settings


def test_owner_id_is_preserved() -> None:
    assert get_settings().owner_id == 6577441312
