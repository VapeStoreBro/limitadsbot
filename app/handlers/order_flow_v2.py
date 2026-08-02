from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html import escape
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import TariffCode
from app.keyboards import DURATION_NAMES, TARIFF_NAMES, preview_keyboard
from app.models import User
from app.rules import validate_post
from app.services.media_groups import MediaGroupCollector
from app.services.orders import create_order, slot_available
from app.services.staff_delivery import (
    deliver_order_to_staff,
    notify_delivery_failure,
    send_ad_content_resilient,
)
from app.states import OrderFlow

logger = logging.getLogger(__name__)
router = Router(name="order_flow_v2")
collector = MediaGroupCollector()
settings = get_settings()


def upload_instructions(tariff: str, booked: bool = False) -> str:
    header = "📅 <b><u>ШАГ 3 · МАТЕРИАЛ ДЛЯ БРОНИ</u></b>" if booked else "📝 <b><u>ШАГ 3 · РЕКЛАМНЫЙ ПОСТ</u></b>"
    restrictions = {
        TariffCode.STANDARD.value: (
            "• текст или до 8 фотографий\n"
            "• <b>без</b> активных ссылок и номера телефона\n"
            "• @username разрешён"
        ),
        TariffCode.MIDDLE.value: (
            "• текст или до 8 фотографий\n"
            "• ссылки разрешены\n"
            "• кнопки под постом недоступны"
        ),
        TariffCode.BEST.value: (
            "• текст или до 8 фотографий\n"
            "• ссылки разрешены\n"
            "• после поста можно добавить до 2 кнопок"
        ),
    }[tariff]
    return (
        f"{header}\n\n"
        "Отправьте сюда <b>готовый пост целиком</b>. Бот сохранит оформление, "
        "покажет предпросмотр и только после вашего подтверждения передаст его администрации.\n\n"
        f"<b>Разрешено:</b>\n{restrictions}\n\n"
        "<b>Не принимаются:</b> видео, GIF, документы, голосовые и стикеры.\n\n"
        "<i>Можно отправить обычный текст без фотографии — он тоже должен приниматься.</i>"
    )


def formatted_message_text(message: Message) -> tuple[str, str, list]:
    plain = message.caption if message.caption is not None else message.text or ""
    entities = list(message.caption_entities or message.entities or [])
    try:
        if message.caption is not None:
            formatted = message.html_caption or escape(plain)
        else:
            formatted = message.html_text or escape(plain)
    except Exception:
        formatted = escape(plain)
    return plain, formatted, entities


async def show_processing_error(message: Message, error: Exception) -> None:
    logger.exception("Advertising post processing failed", exc_info=error)
    await message.answer(
        "<b>⚠️ Не удалось обработать пост</b>\n\n"
        f"Причина: <code>{escape(type(error).__name__)}: {escape(str(error))}</code>\n\n"
        "Попробуйте отправить пост ещё раз. Ошибка также передана владельцу."
    )
    try:
        await message.bot.send_message(
            settings.owner_id,
            "<b>⚠️ Ошибка приёма рекламного поста</b>\n"
            f"Пользователь: <code>{message.from_user.id if message.from_user else 'unknown'}</code>\n"
            f"Ошибка: <code>{escape(type(error).__name__)}: {escape(str(error))}</code>",
        )
    except Exception:
        pass


@router.callback_query(OrderFlow.confirming_selection, F.data == "order:continue")
async def continue_order_v2(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tariff = data.get("tariff_code")
    if not tariff:
        await callback.answer("Выберите тариф заново.", show_alert=True)
        return
    await state.update_data(requested_start_at=None)
    await state.set_state(OrderFlow.waiting_post)
    await callback.message.edit_caption(
        caption=upload_instructions(tariff),
        reply_markup=None,
    )
    await callback.answer("Жду ваш пост")


@router.callback_query(OrderFlow.confirming_selection, F.data.startswith("order:book:"))
async def confirm_booking_v2(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tariff = data.get("tariff_code")
    if not tariff:
        await callback.answer("Выберите тариф заново.", show_alert=True)
        return
    timestamp = int(callback.data.rsplit(":", 1)[1])
    requested = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    await state.update_data(requested_start_at=requested.isoformat())
    await state.set_state(OrderFlow.waiting_post)
    await callback.message.edit_caption(
        caption=upload_instructions(tariff, booked=True),
        reply_markup=None,
    )
    await callback.answer("Место выбрано")


async def process_post_v2(messages: list[Message], state: FSMContext, bot: Bot) -> None:
    first = messages[0]
    try:
        data = await state.get_data()
        tariff = data.get("tariff_code")
        if not tariff:
            await first.answer(
                "<b>Оформление устарело</b>\nНажмите /start и начните покупку заново."
            )
            await state.clear()
            return

        source = next(
            (item for item in messages if item.caption is not None or item.text is not None),
            first,
        )
        plain_text, formatted_text, entities = formatted_message_text(source)
        media: list[dict[str, str]] = []

        for item in messages:
            if item.photo:
                media.append({"type": "photo", "file_id": item.photo[-1].file_id})
                continue
            if item.text is not None:
                continue
            await first.answer(
                "<b>❌ Такой формат не подходит</b>\n\n"
                "Отправьте обычный текст, одну фотографию или фотоальбом до 8 снимков. "
                "Видео, GIF, документы, голосовые и стикеры не принимаются."
            )
            return

        entity_types = {
            getattr(entity.type, "value", str(entity.type)) for entity in entities
        }
        if tariff == TariffCode.STANDARD.value and entity_types.intersection(
            {"url", "text_link", "phone_number"}
        ):
            await first.answer(
                "<b>❌ Пост не подходит для Standard</b>\n\n"
                "В тексте найдена активная ссылка или номер телефона. Удалите их и отправьте пост заново. "
                "Обычный @username разрешён."
            )
            return

        result = validate_post(tariff, plain_text, media, [])
        if not result.ok:
            await first.answer(
                "<b>❌ Пост не прошёл проверку</b>\n\n"
                f"Причина: <b>{escape(result.error or 'неизвестная ошибка')}</b>\n\n"
                "Исправьте пост и отправьте его ещё раз."
            )
            return

        await state.update_data(
            content_text=formatted_text,
            validation_text=plain_text,
            media=media,
            buttons=[],
            waiting_button=False,
            submitting=False,
        )
        await first.answer(
            "<b>✅ Пост принят</b>\n"
            "Сейчас подготовлю точный предпросмотр."
        )

        if tariff == TariffCode.BEST.value:
            await state.set_state(OrderFlow.adding_buttons)
            from app.keyboards import best_setup_keyboard

            await first.answer(
                "🔗 <b><u>ШАГ 4 · КНОПКИ</u></b>\n\n"
                "Можно добавить до <b>2 кнопок</b>: например, контакт продавца и ссылку на канал, бот или барахолку.\n\n"
                "Кнопки необязательны — можно сразу перейти к предпросмотру.",
                reply_markup=best_setup_keyboard(0),
            )
            return

        await show_preview_v2(first, state, bot)
    except Exception as error:
        await show_processing_error(first, error)


@router.message(OrderFlow.waiting_post)
async def receive_post_v2(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.media_group_id:
        collector.add(message, lambda items: process_post_v2(items, state, bot))
        return
    await process_post_v2([message], state, bot)


async def show_preview_v2(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    preview = SimpleNamespace(
        content_text=data.get("content_text", ""),
        media=data.get("media", []),
        buttons=data.get("buttons", []),
    )
    await state.set_state(OrderFlow.previewing)
    await message.answer("👁 <b><u>ШАГ 4 · ПРЕДПРОСМОТР</u></b>")
    await send_ad_content_resilient(bot, message.chat.id, preview)

    booking_line = ""
    if data.get("requested_start_at"):
        local = datetime.fromisoformat(data["requested_start_at"]).astimezone(
            ZoneInfo(settings.timezone)
        )
        booking_line = f"\n├ Бронь: <b>{local:%d.%m.%Y в %H:%M}</b>"

    await message.answer(
        "<b><u>ПРОВЕРЬТЕ ЗАКАЗ</u></b>\n\n"
        f"├ Тариф: <b>{TARIFF_NAMES[data['tariff_code']]}</b>\n"
        f"├ Срок: <b>{DURATION_NAMES[data['duration_code']]}</b>\n"
        f"├ Стоимость: <b>{data['price_rub']} ₽</b>{booking_line}\n"
        f"└ Фотографий: <b>{len(data.get('media', []))}</b>\n\n"
        "Нажмите зелёную кнопку, чтобы передать пост администрации. "
        "До нажатия заявка никуда не отправляется.",
        reply_markup=preview_keyboard(),
    )


@router.callback_query(OrderFlow.adding_buttons, F.data == "best:preview")
async def best_preview_v2(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer("Готовлю предпросмотр")
    await show_preview_v2(callback.message, state, bot)


@router.callback_query(OrderFlow.previewing, F.data == "preview:redo")
async def redo_post_v2(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(
        content_text=None,
        validation_text=None,
        media=[],
        buttons=[],
        waiting_button=False,
        submitting=False,
    )
    await state.set_state(OrderFlow.waiting_post)
    await callback.message.edit_text(
        upload_instructions(data.get("tariff_code", TariffCode.STANDARD.value)),
        reply_markup=None,
    )
    await callback.answer("Отправьте новый пост")


@router.callback_query(OrderFlow.previewing, F.data == "preview:submit")
async def submit_preview_v2(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    if data.get("submitting"):
        await callback.answer("Заявка уже отправляется…", show_alert=True)
        return

    await state.update_data(submitting=True)
    await callback.answer("Отправляю заявку администрации…")

    order = None
    try:
        requested = (
            datetime.fromisoformat(data["requested_start_at"])
            if data.get("requested_start_at")
            else None
        )
        async with SessionFactory() as session:
            if requested and not await slot_available(
                session,
                data["tariff_code"],
                requested,
                requested + timedelta(hours=data["duration_hours"]),
            ):
                await state.update_data(submitting=False)
                await callback.message.answer(
                    "<b>⚠️ Это место только что заняли</b>\n"
                    "Вернитесь в главное меню и выберите новое свободное время."
                )
                return

            order = await create_order(
                session,
                user_id=callback.from_user.id,
                tariff_code=data["tariff_code"],
                duration_code=data["duration_code"],
                content_text=data.get("content_text", ""),
                media=data.get("media", []),
                buttons=data.get("buttons", []),
                requested_start_at=requested,
            )
            user = await session.get(User, callback.from_user.id)

        if user is None:
            raise RuntimeError("Профиль покупателя не найден")

        delivered = True
        delivery_error: Exception | None = None
        try:
            card_message_id = await deliver_order_to_staff(bot, order, user)
            async with SessionFactory() as session:
                stored = await session.get(type(order), order.id)
                if stored:
                    stored.moderation_card_message_id = card_message_id
                    stored.updated_at = datetime.now(timezone.utc)
                    await session.commit()
        except Exception as error:
            delivered = False
            delivery_error = error
            logger.exception("Order %s was not delivered to staff", order.id)
            await notify_delivery_failure(bot, order.id, error)

        await state.clear()
        try:
            await callback.message.edit_text(
                (
                    f"<b>✅ Заявка №{order.id} отправлена на модерацию</b>\n\n"
                    "Администрация проверит пост и пришлёт решение сюда."
                    if delivered
                    else
                    f"<b>✅ Заявка №{order.id} сохранена</b>\n\n"
                    "Группа модерации временно не приняла сообщение, но заказ уже находится "
                    "в админ-панели и не потерян. Владелец получил точную ошибку."
                ),
                reply_markup=None,
            )
        except Exception:
            await bot.send_message(
                callback.from_user.id,
                f"✅ Заявка №{order.id} сохранена и ожидает проверки.",
            )

        if delivery_error:
            logger.error("Staff delivery error for order %s: %r", order.id, delivery_error)
    except Exception as error:
        await state.update_data(submitting=False)
        logger.exception("Order submission failed")
        await callback.message.answer(
            "<b>❌ Не удалось отправить заявку</b>\n\n"
            f"Причина: <code>{escape(type(error).__name__)}: {escape(str(error))}</code>\n\n"
            "Попробуйте нажать кнопку ещё раз. Ошибка отправлена владельцу."
        )
        try:
            await bot.send_message(
                settings.owner_id,
                "<b>❌ Ошибка создания заявки</b>\n"
                f"Пользователь: <code>{callback.from_user.id}</code>\n"
                f"Ошибка: <code>{escape(type(error).__name__)}: {escape(str(error))}</code>",
            )
        except Exception:
            pass
