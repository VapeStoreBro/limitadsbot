# Limit Ads Bot

Система продажи и автоматизации рекламы для тестовой барахолки Limit Vape.

## Что уже реализовано

- сбор доступных Telegram-данных пользователя и истории username;
- проверка участия в тестовой барахолке `-1003377593526`;
- добровольная передача номера телефона;
- тарифы Standard, Middle и Best с базовыми и персональными ценами;
- готовый текст или фотоальбом до 8 фотографий;
- ограничения Standard: без активных ссылок и телефонов;
- до двух URL-кнопок для Best;
- модерация в группе стафа `-5466156820`;
- тестовая полная оплата и бронь с предоплатой 50%;
- таймер запускается только после ручной активации администрацией;
- префикс `Реклама до ДД.ММ`;
- Middle: закреп следующего сообщения клиента и две замены;
- Best: основной закреп, повтор каждые 3 часа, хранение последних 3 отправок;
- окончание тарифа, открепление и снятие префикса;
- клавиатурная админ-панель;
- PostgreSQL и восстановление расписания после перезапуска;
- тесты и автоматическая CI-проверка каждого обновления;
- Telegram webhook и отдельный HMAC-проверяемый GitHub deploy webhook.

## Локальный запуск

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Для polling оставьте WEBHOOK_BASE_URL пустым.
python -m app
```

## Схема сервера 195.133.9.214

Старый проект не затрагивается:

```text
/root/kalivanbot          kalivan.service
порт 9000                 kalivan-webhook.service
```

Новый проект изолирован:

```text
/root/limitadsbot         limitadsbot.service
порт 9102                 limitadsbot-deploy.service
PostgreSQL host port      5433
```

## Установка на сервер

```bash
cd /root
git clone https://github.com/VapeStoreBro/limitadsbot.git
cd /root/limitadsbot
bash deploy/install.sh
nano .env
docker compose up -d
ufw allow 9102/tcp
systemctl enable --now limitadsbot.service limitadsbot-deploy.service
```

Для первого теста `WEBHOOK_BASE_URL` оставляется пустым: Telegram-бот работает через polling, как существующий `kalivanbot`.

## GitHub deploy webhook

В настройках репозитория создайте webhook:

```text
http://195.133.9.214:9102/deploy/ЗНАЧЕНИЕ_DEPLOY_PATH_SECRET
```

- Content type: `application/json`
- Secret: значение `GITHUB_WEBHOOK_SECRET` из `.env`
- Events: только `push`

Слушатель проверяет HMAC-подпись, принимает только `VapeStoreBro/limitadsbot` и только ветку `main`, затем обновляет код, устанавливает зависимости, проверяет синтаксис и перезапускает только `limitadsbot.service`.

## Telegram webhook позже

Для Telegram webhook понадобится публичный HTTPS-домен. Тогда задаются:

```text
WEBHOOK_BASE_URL=https://ВАШ_ДОМЕН
TELEGRAM_WEBHOOK_PATH=/telegram/СЕКРЕТНЫЙ_ПУТЬ
TELEGRAM_WEBHOOK_SECRET=СЕКРЕТ_ЗАГОЛОВКА
```

Пример Nginx находится в `deploy/nginx.conf.example`.

## Ограничение Telegram

Bot API не позволяет прикреплять inline-кнопки прямо к фотоальбому. Для Best с несколькими фотографиями бот отправляет альбом и связанное сообщение с кнопками; закрепляется сообщение с кнопками.
