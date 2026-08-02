from aiogram.fsm.state import State, StatesGroup


class OrderFlow(StatesGroup):
    choosing_tariff = State()
    choosing_duration = State()
    choosing_start_mode = State()
    entering_booking_date = State()
    waiting_post = State()
    adding_buttons = State()


class AdminFlow(StatesGroup):
    adding_admin = State()
    setting_personal_price = State()
