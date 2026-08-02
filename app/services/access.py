from aiogram import Bot

from app.db.session import SessionFactory
from app.keyboards import membership_keyboard, phone_keyboard
from app.keyboards_v3 import home_keyboard
from app.models import User
from app.services.blocking import get_user_block
from app.services.users import inspect_membership, is_admin
from app.config import get_settings

settings = get_settings()


async def ensure_buyer_access(user_id: int, bot: Bot) -> tuple[bool, bool]:
    """Return (allowed, admin) and explain every denial in the bot."""
    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        admin = await is_admin(session, user_id)
        block = await get_user_block(session, user_id)

    if admin:
        return True, True
    if block:
        await bot.send_message(
            user_id,
            "<b>🚫 Покупка рекламы временно недоступна</b>\n\n"
            f"Причина: <i>{block.reason}</i>",
            reply_markup=home_keyboard(),
        )
        return False, False
    if not user or not user.phone:
        await bot.send_message(
            user_id,
            "<b>📱 Сначала подтвердите номер телефона</b>",
            reply_markup=phone_keyboard(),
        )
        return False, False

    result, _, _ = await inspect_membership(bot, user_id)
    if result != "member":
        await bot.send_message(
            user_id,
            "<b>❌ Для покупки рекламы нужно состоять в барахолке</b>",
            reply_markup=membership_keyboard(settings.bazaar_url),
        )
        return False, False
    return True, False
