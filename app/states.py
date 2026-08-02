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
