from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.db.session import SessionFactory
from app.handlers.admin_runtime_fix_v8 import allowed
from app.services.app_settings import STARS_SHOP_URL_KEY, set_setting
from app.services.ui_screen import render_user_screen

router = Router(name="payment_shop_disable_v10")


@router.callback_query(F.data == "settingsv10:stars_shop_disable")
async def disable_stars_shop(callback: CallbackQuery) -> None:
    if not await allowed(callback, owner=True):
        return
    async with SessionFactory() as session:
        await set_setting(session, STARS_SHOP_URL_KEY, "", callback.from_user.id)
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        "<b>✅ Ссылка магазина Stars отключена</b>\n\n"
        "Рекламный блок останется в оплате с пометкой «скоро», но перехода в другой бот не будет.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ К настройкам Stars",
                        callback_data="settingsv10:stars_shop",
                    )
                ],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
        source_message=callback.message,
        media_key="admin",
        text_only=True,
    )
    await callback.answer("Ссылка отключена", show_alert=True)
