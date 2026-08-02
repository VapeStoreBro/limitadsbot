import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.config import get_settings
from app.db.bootstrap import bootstrap_database
from app.handlers import admin, common, customer, moderation
from app.services.scheduler import OrderScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()
bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_routers(common.router, customer.router, moderation.router, admin.router)
scheduler = OrderScheduler(bot)


async def on_startup() -> None:
    await bootstrap_database()
    scheduler.start()
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


def run_webhook() -> None:
    app = web.Application()
    app.router.add_get("/health", health)
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
