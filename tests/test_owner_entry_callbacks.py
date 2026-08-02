from app.handlers.owner_emergency_entry import owner_menu


def test_owner_menu_callbacks_are_stable() -> None:
    buttons = [button for row in owner_menu().inline_keyboard for button in row]
    assert {button.callback_data for button in buttons} >= {
        "profile:admin",
        "profile:orders",
        "profile:buy",
    }
