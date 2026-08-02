from aiogram import Bot

from app.config import get_settings
from app.db.session import SessionFactory
from app.keyboards import membership_keyboard, phone_keyboard
from app.keyboards_v3 import home_keyboard
from app.models import User
from app.services.app_settings import get_bazaar_url
from app.services.blocking import get_user_block
from app.services.ui_screen import render_user_screen
from app.services.users import inspect_membership, is_admin

settings = get_settings()


async def ensure_buyer_access(user_id: int, bot: Bot) -> tuple[bool, bool]:
    """Return (allowed, admin) and explain every denial in the current screen."""
    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        admin = await is_admin(session, user_id)
        block = await get_user_block(session, user_id)
        bazaar_url = await get_bazaar_url(session)

    if admin:
        return True, True
    if block:
        await render_user_screen(
            bot,
            user_id,
            "<b>🚫 Покупка рекламы временно недоступна</b>\n\n"
            f"Причина: <i>{block.reason}</i>",
            home_keyboard(),
            media_key="main",
        )
        return False, False
    if not user or not user.phone:
        # Contact request buttons can only be sent as a reply keyboard.
        await bot.send_message(
            user_id,
            "<b>📱 Сначала подтвердите номер телефона</b>",
            reply_markup=phone_keyboard(),
        )
        return False, False

    result, _, _ = await inspect_membership(bot, user_id)
    if result != "member":
        await render_user_screen(
            bot,
            user_id,
            "<b>❌ Для покупки рекламы нужно состоять в рабочей группе</b>",
            membership_keyboard(bazaar_url),
            media_key="main",
        )
        return False, False
    return True, False
