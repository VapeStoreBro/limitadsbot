from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.db.session import SessionFactory
from app.models import AdOrder, User
from app.services.order_cards import update_buyer_card
from app.services.orders import create_order, slot_available
from app.services.staff_delivery import deliver_order_to_staff, notify_delivery_failure
from app.states import OrderFlow

router = Router(name="submission_v5")


@router.callback_query(OrderFlow.previewing, F.data == "preview:submit")
async def submit_preview_v5(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    if data.get("submitting"):
        await callback.answer("Заявка уже отправляется…", show_alert=True)
        return
    await state.update_data(submitting=True)
    await callback.answer("Отправляю на модерацию…")

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
                await callback.answer("Место только что заняли. Выберите тариф заново.", show_alert=True)
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
            await update_buyer_card(
                session,
                bot,
                order,
                source_message=callback.message,
            )

        if user is None:
            raise RuntimeError("Профиль покупателя не найден")

        try:
            card_message_id = await deliver_order_to_staff(bot, order, user)
            async with SessionFactory() as session:
                stored = await session.get(AdOrder, order.id)
                if stored:
                    stored.moderation_card_message_id = card_message_id
                    stored.updated_at = datetime.now(timezone.utc)
                    await session.commit()
                    await update_buyer_card(session, bot, stored)
        except Exception as error:
            await notify_delivery_failure(bot, order.id, error)

        await state.clear()
    except Exception as error:
        await state.update_data(submitting=False)
        await callback.message.edit_text(
            "<b>❌ Не удалось создать заявку</b>\n\n"
            f"Причина: <code>{escape(type(error).__name__)}: {escape(str(error))}</code>",
        )
