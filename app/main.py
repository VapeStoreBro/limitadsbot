import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.config import get_settings
from app.db.bootstrap import bootstrap_database
from app.handlers import (
    admin,
    admin_controls_v5,
    admin_final_v7,
    admin_orders_v4,
    admin_pages_v4,
    admin_panel_v3,
    admin_runtime_fix_v8,
    best_buttons_v3,
    best_edit_v5,
    buyer_ads_v3,
    buyer_lifecycle_v5,
    client_controls_v4,
    client_single_v6,
    common,
    customer,
    entry_v3,
    moderation,
    order_admin_v2,
    order_compose_v6,
    order_flow_v2,
    order_selection_v2,
    owner_emergency_entry,
    payment_methods_v9,
    payment_shop_ad_v10,
    payment_shop_disable_v10,
    payments_v3,
    revision_v9,
    single_actions_v6,
    single_screen_v6,
    submission_v5,
)
from app.payments.webhook import handle_yookassa_webhook, payment_return
from app.services.price_card import ensure_price_card
from app.services.scheduler import OrderScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()
bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# The hard-coded owner rescue router runs first and guarantees access even when
# the branded one-screen UI or a saved screen record is broken.
dp.include_routers(
    owner_emergency_entry.router,
    entry_v3.router,
    common.router,
    payment_shop_disable_v10.router,
    payment_shop_ad_v10.router,
    admin_runtime_fix_v8.router,
    admin_final_v7.router,
    single_screen_v6.router,
    single_actions_v6.router,
    client_single_v6.router,
    client_controls_v4.router,
    order_selection_v2.router,
    order_compose_v6.router,
    submission_v5.router,
    revision_v9.router,
    order_flow_v2.router,
    best_edit_v5.router,
    best_buttons_v3.router,
    payment_methods_v9.router,
    payments_v3.router,
    buyer_lifecycle_v5.router,
    buyer_ads_v3.router,
    moderation.router,
    admin_controls_v5.router,
    admin_orders_v4.router,
    order_admin_v2.router,
    admin_pages_v4.router,
    admin_panel_v3.router,
    customer.router,
    admin.router,
)

scheduler = OrderScheduler(bot)


async def on_startup() -> None:
    await bootstrap_database()
    ensure_price_card()
    scheduler.start()
    try:
        await bot.delete_my_commands()
    except Exception:
        logger.exception("Could not clear bot commands")
    if settings.webhook_base_url:
        await bot.set_webhook(
            settings.webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        logger.info("Telegram webhook configured: %s", settings.webhook_url)


async def on_shutdown() -> None:
    await scheduler.stop()
    await bot.session.close()


dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "limitadsbot"})


async def yookassa_webhook(request: web.Request) -> web.Response:
    return await handle_yookassa_webhook(request, bot)


def run_webhook() -> None:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post(settings.yookassa_webhook_path, yookassa_webhook)
    app.router.add_get("/payments/return", payment_return)
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.telegram_webhook_secret,
    ).register(app, path=settings.telegram_webhook_path)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host=settings.web_server_host, port=settings.web_server_port)


async def run_polling() -> None:
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    if settings.webhook_base_url:
        run_webhook()
    else:
        asyncio.run(run_polling())
