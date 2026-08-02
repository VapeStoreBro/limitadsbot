from types import SimpleNamespace

from aiogram import Bot
from aiogram.types import InputMediaPhoto

from app.keyboards import best_buttons
from app.services.staff_delivery import send_ad_content_resilient


async def send_order_preview(
    bot: Bot,
    chat_id: int,
    *,
    content_text: str,
    media: list[dict[str, str]],
    buttons: list[dict[str, str]],
) -> None:
    """Show a clean buyer preview without splitting a photo set.

    Telegram cannot attach an inline keyboard to a media group. Therefore the
    preview keeps all photos in one album and shows Best buttons immediately
    below. The real Best publication still attaches buttons to its pinned main
    post.
    """
    if len(media) >= 2:
        album = [
            InputMediaPhoto(
                media=item["file_id"],
                caption=content_text if index == 0 else None,
                parse_mode="HTML",
            )
            for index, item in enumerate(media)
        ]
        await bot.send_media_group(chat_id, media=album)
        if buttons:
            await bot.send_message(
                chat_id,
                "<b>Кнопки основного закреплённого поста:</b>",
                reply_markup=best_buttons(buttons),
            )
        return

    preview = SimpleNamespace(
        content_text=content_text,
        media=media,
        buttons=buttons,
    )
    await send_ad_content_resilient(bot, chat_id, preview)
