import asyncio

from app.main import run_polling, run_webhook, settings

if settings.webhook_base_url:
    run_webhook()
else:
    asyncio.run(run_polling())
