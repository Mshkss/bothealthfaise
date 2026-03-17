# Grant Site Monitor Bot

Серверный бот для Telegram, который проверяет сайт грантов и рассылает подписчикам уведомление `Сайт ожил`.
`Сайт ожил` отправляется только после `SUCCESS_STREAK_REQUIRED` успешных логинов подряд (по умолчанию 5).
После того как сервис стал `UP`, обратно в `DOWN` он переводится только после `FAILURE_STREAK_REQUIRED` неуспешных проверок подряд (по умолчанию 3).
Проверка идет по цепочке:
1. `POST TARGET_URL` (`/api/v3/auth/sign-in`) и извлечение `token` из JSON ответа.
2. `GET ACCOUNT_URL` (`/api/v2/account`) с Cookie `<AUTH_COOKIE_NAME>=<token>`.
Проверка считается успешной только если второй шаг возвращает `ACCOUNT_EXPECTED_STATUS` (по умолчанию `200`).

## Как использовать

1. Создайте `.env` из примера:
   ```bash
   cp .env.example .env
   ```
2. Вставьте `BOT_TOKEN` вашего бота и `AUTH_LOGIN`/`AUTH_PASSWORD` для проверки авторизации.
3. Запустите:
   ```bash
   docker compose up -d --build
   ```
4. Откройте вашего бота в Telegram и отправьте `/start`.

После этого любой грантополучатель может открыть бота и тоже отправить `/start`, чтобы подписаться.

## Команды бота

- `/start` подписаться на уведомления
- `/stop` отписаться
- `/status` посмотреть последний статус сайта

## Полезные команды

```bash
docker compose logs -f
docker compose down
```

cd /opt/bothealthfaise
docker compose exec grant-site-monitor-bot sh -lc "python - <<'PY'
import sqlite3
conn=sqlite3.connect('/data/bot.db')
for row in conn.execute('select chat_id, created_at from subscribers order by created_at desc'):
    print(row)
conn.close()
PY"

docker compose exec grant-site-monitor-bot sh -lc "python - <<'PY'
import sqlite3
conn=sqlite3.connect('/data/bot.db')
print(conn.execute('select count(*) from subscribers').fetchone()[0])
conn.close()
PY"
