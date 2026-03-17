# Grant Site Monitor Bot

Серверный бот для Telegram, который проверяет сайт грантов и рассылает подписчикам уведомления `Сайт ожил` и `Сайт упал`.
`Сайт ожил` отправляется только после `SUCCESS_STREAK_REQUIRED` успешных логинов подряд (HTTP `2xx`) на `POST /api/v3/auth/sign-in`.

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
