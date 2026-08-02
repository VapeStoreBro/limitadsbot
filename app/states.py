from aiogram.fsm.state import State, StatesGroup


class OrderFlow(StatesGroup):
    choosing_tariff = State()
    confirming_selection = State()
    waiting_post = State()
    adding_buttons = State()
    previewing = State()


class AdminFlow(StatesGroup):
    adding_admin = State()
    removing_admin = State()
    setting_personal_price = State()
    choosing_price_user = State()
    choosing_price_tariff = State()
    choosing_price_duration = State()
    entering_price_amount = State()
    choosing_price_discount = State()
    entering_staff_chat_id = State()
