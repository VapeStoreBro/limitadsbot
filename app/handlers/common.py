from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.db.session import SessionFactory
from app.keyboards import customer_menu
from app.models import User
from app.services.users import is_admin, upsert_user

router = Router(name="common")


@router.message(CommandStart())
async def start(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    async with SessionFactory() as session:
        user = await upsert_user(session, bot, message.from_user)
        admin = await is_admin(session, user.id)
    membership = "✅ состоит в барахолке" if user.is_bazaar_member else "❌ не состоит в барахолке"
    await message.answer(
        "👋 Добро пожаловать в систему рекламы Limit Vape.\n\n"
        f"Проверка аккаунта: {membership}.\n"
        "Номер телефона можно передать добровольно, чтобы администрация могла помочь.",
        reply_markup=customer_menu(admin),
    )


@router.message(F.contact)
async def save_contact(message: Message) -> None:
    if not message.from_user or not message.contact:
        return
    if message.contact.user_id not in (None, message.from_user.id):
        await message.answer("Можно отправить только собственный номер.")
        return
    async with SessionFactory() as session:
        user = await session.get(User, message.from_user.id)
        if user is None:
            await message.answer("Сначала нажмите /start.")
            return
        user.phone = message.contact.phone_number
        user.last_seen_at = datetime.now(timezone.utc)
        await session.commit()
    await message.answer("✅ Номер сохранён и виден только администрации.")


@router.message(F.text == "ℹ️ Тарифы")
async def show_tariffs(message: Message) -> None:
    await message.answer(
        "<b>Standard</b> — публикации вручную, без активных ссылок и телефона.\n"
        "500 ₽ день · 1000 ₽ неделя · 1500 ₽ месяц\n\n"
        "<b>Middle</b> — публикации вручную, ссылки разрешены, один закреп и две замены. "
        "Одновременно до трёх клиентов.\n"
        "700 ₽ день · 1400 ₽ неделя · 2000 ₽ месяц\n\n"
        "<b>Best</b> — один клиент, основной закреп и автопубликация каждые три часа. "
        "Остаются последние три отправки, разрешено до двух кнопок.\n"
        "1500 ₽ день · 2000 ₽ неделя · 2700 ₽ месяц"
    )
