from __future__ import annotations

from typing import Any

from aiohttp import BasicAuth, ClientSession, ClientTimeout

from app.config import get_settings
from app.payment_models import PaymentTransaction


class YooKassaError(RuntimeError):
    pass


class YooKassaProvider:
    name = "yookassa"
    api_base = "https://api.yookassa.ru/v3"

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.yookassa_shop_id
            and self.settings.yookassa_secret_key
            and self.settings.yookassa_return_url
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        idempotency_key: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise YooKassaError("YooKassa credentials are not configured")

        headers = {"Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotence-Key"] = idempotency_key

        timeout = ClientTimeout(total=20)
        auth = BasicAuth(
            self.settings.yookassa_shop_id,
            self.settings.yookassa_secret_key,
        )
        async with ClientSession(timeout=timeout, auth=auth) as client:
            async with client.request(
                method,
                f"{self.api_base}{path}",
                headers=headers,
                json=payload,
            ) as response:
                try:
                    data = await response.json()
                except Exception:
                    body = await response.text()
                    raise YooKassaError(
                        f"YooKassa returned HTTP {response.status}: {body[:500]}"
                    ) from None
                if response.status >= 400:
                    code = data.get("code") or data.get("type") or "api_error"
                    description = data.get("description") or data.get("parameter") or str(data)
                    raise YooKassaError(f"YooKassa {code}: {description}")
                return data

    async def create_payment(
        self,
        transaction: PaymentTransaction,
        *,
        description: str,
    ) -> dict[str, Any]:
        amount = f"{transaction.amount_minor / 100:.2f}"
        payload = {
            "amount": {
                "value": amount,
                "currency": transaction.currency,
            },
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": self.settings.yookassa_return_url,
            },
            "description": description[:128],
            "metadata": {
                "transaction_id": transaction.transaction_id,
                "order_id": str(transaction.order_id),
                "user_id": str(transaction.user_id),
                "kind": transaction.kind,
            },
        }
        return await self._request(
            "POST",
            "/payments",
            idempotency_key=transaction.idempotency_key,
            payload=payload,
        )

    async def retrieve_payment(self, provider_payment_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/payments/{provider_payment_id}")
