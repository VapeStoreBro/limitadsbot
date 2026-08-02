from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.db.session import SessionFactory
from app.enums import TariffCode
from app.handlers.order_flow_v2 import upload_instructions
from app.keyboards_v3 import simplified_best_keyboard, upload_navigation_keyboard
from app.models import User
from app.rules import validate_post
from app.services.ui_screen import delete_user_input, render_user_screen
from app.states import OrderFlow

router = Router(name="best_buttons_v3")


def normalize_url(value: str, *, allow_username: bool = False) -> str | None:
    raw = value.strip()
    if allow_username and raw.startswith("@") and len(raw) > 1:
        return f"https://t.me/{raw[1:]}"
    if raw.startswith("t.me/") or raw.startswith("telegram.me/"):
        raw = "https://" + raw
    if not raw.startswith(("http://", "https://", "tg://")):
        return None
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        return None
    return raw


async def append_button(
    state: FSMContext,
    *,
    text: str,
    url: str,
    kind: str,
) -> tuple[bool, str, int]:
    data = await state.get_data()
    buttons = list(data.get("buttons", []))
    if len(buttons) >= 2:
        return False, "Уже добавлены две кнопки.", len(buttons)
    if any(button.get("kind") == kind for button in buttons):
        return False, "Такая кнопка уже добавлена.", len(buttons)

    candidate = [*buttons, {"text": text, "url": url, "kind": kind}]
    result = validate_post(
        data["tariff_code"],
        data.get("validation_text", ""),
        data.get("media", []),
        candidate,
    )
    if not result.ok:
        return False, result.error or "Ссылка не подходит.", len(buttons)

    await state.update_data(
        buttons=candidate,
        waiting_button=False,
        waiting_button_kind=None,
    )
    return True, "Кнопка добавлена.", len(candidate)


async def render_setup(
    callback: CallbackQuery,
    text: str,
    count: int,
) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        simplified_best_keyboard(count),
        source_message=callback.message,
        media_key="main",
    )


@router.callback_query(OrderFlow.adding_buttons, F.data == "bestv3:contact")
async def add_contact_button(callback: CallbackQuery, state: FSMContext) -> None:
    async with SessionFactory() as session:
        user = await session.get(User, callback.from_user.id)
    if user and user.username:
        ok, text, count = await append_button(
            state,
            text="👤 Связаться",
            url=f"https://t.me/{user.username}",
            kind="contact",
        )
        if ok:
            await render_setup(
                callback,
                "<b>✅ Контакт добавлен</b>\n\n"
                "Зелёная отметка означает, что кнопка готова. Можно добавить ресурс или открыть предпросмотр.",
                count,
            )
        await callback.answer(text, show_alert=not ok)
        return

    await state.update_data(waiting_button=True, waiting_button_kind="contact")
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        "<b>👤 Контакт</b>\n\n"
        "Отправьте только <b>@username</b> или ссылку на контакт. Название кнопки бот поставит сам.",
        upload_navigation_keyboard(),
        source_message=callback.message,
        media_key="main",
    )
    await callback.answer("Жду контакт")


@router.callback_query(OrderFlow.adding_buttons, F.data == "bestv3:resource")
async def add_resource_button(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(waiting_button=True, waiting_button_kind="resource")
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        "<b>🔗 Ресурс</b>\n\n"
        "Отправьте только ссылку на канал, барахолку, сайт или бота. Название кнопки бот поставит сам.",
        upload_navigation_keyboard(),
        source_message=callback.message,
        media_key="main",
    )
    await callback.answer("Жду ссылку")


@router.callback_query(OrderFlow.adding_buttons, F.data == "preview:redo")
async def redo_best_post(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(
        content_text=None,
        validation_text=None,
        media=[],
        buttons=[],
        waiting_button=False,
        waiting_button_kind=None,
        submitting=False,
    )
    await state.set_state(OrderFlow.waiting_post)
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        upload_instructions(data.get("tariff_code", TariffCode.BEST.value)),
        upload_navigation_keyboard(),
        source_message=callback.message,
        media_key="main",
    )
    await callback.answer("Отправьте новый пост")


@router.message(OrderFlow.adding_buttons, F.text)
async def receive_simple_button_url(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("waiting_button_kind")
    count = len(data.get("buttons", []))
    if kind not in {"contact", "resource"}:
        await render_user_screen(
            message.bot,
            message.from_user.id,
            "Выберите действие кнопкой ниже.",
            simplified_best_keyboard(count),
            media_key="main",
        )
        await delete_user_input(message)
        return

    url = normalize_url(message.text, allow_username=kind == "contact")
    if not url:
        await render_user_screen(
            message.bot,
            message.from_user.id,
            "<b>❌ Ссылка не распознана</b>\n\n"
            "Для контакта отправьте @username или полную ссылку. Для ресурса — ссылку, начинающуюся с https://.",
            upload_navigation_keyboard(),
            media_key="main",
        )
        await delete_user_input(message)
        return

    label = "👤 Связаться" if kind == "contact" else "🔗 Перейти"
    ok, text, count = await append_button(
        state,
        text=label,
        url=url,
        kind=kind,
    )
    await render_user_screen(
        message.bot,
        message.from_user.id,
        (
            f"<b>✅ {text}</b>\n\n"
            "Зелёная отметка означает, что кнопка готова. Можно добавить вторую кнопку или открыть предпросмотр."
            if ok
            else f"<b>❌ {text}</b>"
        ),
        simplified_best_keyboard(count),
        media_key="main",
    )
    await delete_user_input(message)
