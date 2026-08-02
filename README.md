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

## Установка на сервер 195.133.9.214

Проект использует отдельную директорию `/opt/limitadsbot`, отдельную базу и отдельные systemd-сервисы. Старый `kalivanbot` не затрагивается.

```bash
sudo PROJECT_DIR=/opt/limitadsbot bash deploy/install.sh
cd /opt/limitadsbot
sudo nano .env
sudo docker compose up -d
sudo systemctl enable --now limitadsbot.service limitadsbot-deploy.service
```

После этого добавьте конфигурацию из `deploy/nginx.conf.example` в HTTPS server block Nginx.

## GitHub deploy webhook

В настройках репозитория создайте webhook:

```text
https://ВАШ_ДОМЕН/deploy/ЗНАЧЕНИЕ_DEPLOY_PATH_SECRET
```

- Content type: `application/json`
- Secret: значение `GITHUB_WEBHOOK_SECRET` из `.env`
- Events: только `push`

## Telegram webhook

Публичный HTTPS-адрес задаётся через:

```text
WEBHOOK_BASE_URL=https://ВАШ_ДОМЕН
TELEGRAM_WEBHOOK_PATH=/telegram/СЕКРЕТНЫЙ_ПУТЬ
TELEGRAM_WEBHOOK_SECRET=СЕКРЕТ_ЗАГОЛОВКА
```

## Ограничение Telegram

Bot API не позволяет прикреплять inline-кнопки прямо к фотоальбому. Для Best с несколькими фотографиями бот отправляет альбом и связанное сообщение с кнопками; закрепляется сообщение с кнопками.
