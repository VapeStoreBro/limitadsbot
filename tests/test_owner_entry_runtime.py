from app.handlers.owner_emergency_entry import OWNER_ID, owner_menu


def test_owner_emergency_menu_is_available() -> None:
    assert OWNER_ID == 6577441312
    markup = owner_menu()
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "profile:admin" in callbacks
    assert "profile:orders" in callbacks
