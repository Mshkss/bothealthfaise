import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests
from telegram import Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TARGET_URL = os.getenv(
    "TARGET_URL", "https://online.fasie.ru/api/v3/auth/sign-in"
).strip()
AUTH_LOGIN = os.getenv("AUTH_LOGIN", "").strip()
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "").strip()
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "45"))
REQUEST_TIMEOUT_SEC = int(os.getenv("REQUEST_TIMEOUT_SEC", "8"))
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")
SUCCESS_STREAK_REQUIRED = int(os.getenv("SUCCESS_STREAK_REQUIRED", "5"))
FAILURE_STREAK_REQUIRED = int(os.getenv("FAILURE_STREAK_REQUIRED", "3"))


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not AUTH_LOGIN or not AUTH_PASSWORD:
    raise RuntimeError("AUTH_LOGIN and AUTH_PASSWORD are required")


@dataclass
class MonitorState:
    last_status_code: Optional[int] = None
    last_error: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_up: Optional[bool] = None
    consecutive_successes: int = 0
    consecutive_failures: int = 0


STATE = MonitorState()


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def add_subscriber(chat_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscribers(chat_id, created_at) VALUES (?, ?)",
            (chat_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def remove_subscriber(chat_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
        conn.commit()


def list_subscribers() -> list[int]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT chat_id FROM subscribers").fetchall()
        return [row[0] for row in rows]


async def notify_all(app: Application, text: str) -> None:
    subscribers = list_subscribers()
    if not subscribers:
        logger.info("No subscribers yet, skip broadcast")
        return

    logger.info("Broadcasting to %s subscribers", len(subscribers))
    for chat_id in subscribers:
        try:
            await app.bot.send_message(chat_id=chat_id, text=text)
        except Forbidden:
            logger.warning("Bot was blocked by chat_id=%s, removing", chat_id)
            remove_subscriber(chat_id)
        except TelegramError as err:
            logger.error("Failed to send to chat_id=%s: %s", chat_id, err)


def check_site() -> tuple[Optional[int], Optional[str], Optional[str]]:
    try:
        response = requests.post(
            TARGET_URL,
            json={"login": AUTH_LOGIN, "password": AUTH_PASSWORD},
            timeout=REQUEST_TIMEOUT_SEC,
        )
        return response.status_code, None, response.text
    except requests.RequestException as err:
        return None, str(err), None


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def status_text() -> str:
    checked = (
        STATE.last_checked_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if STATE.last_checked_at
        else "never"
    )
    if STATE.last_status_code is not None:
        state_label = "UP" if STATE.last_up else "DOWN"
        return (
            f"URL: {TARGET_URL}\n"
            f"State: {state_label}\n"
            f"HTTP status: {STATE.last_status_code}\n"
            f"Success streak: {STATE.consecutive_successes}/{SUCCESS_STREAK_REQUIRED}\n"
            f"Failure streak: {STATE.consecutive_failures}/{FAILURE_STREAK_REQUIRED}\n"
            f"Checked: {checked}"
        )
    return f"URL: {TARGET_URL}\nState: UNKNOWN\nLast error: {STATE.last_error}\nChecked: {checked}"


async def monitor_loop(app: Application) -> None:
    logger.info("Monitor started: url=%s, interval=%ss", TARGET_URL, CHECK_INTERVAL_SEC)
    while True:
        status_code, err, _ = await asyncio.to_thread(check_site)
        STATE.last_checked_at = datetime.now(timezone.utc)
        STATE.last_status_code = status_code
        STATE.last_error = err

        if status_code is not None:
            if 200 <= status_code < 300:
                STATE.consecutive_successes += 1
                STATE.consecutive_failures = 0
            else:
                STATE.consecutive_successes = 0
                STATE.consecutive_failures += 1
        else:
            logger.warning("Check error: %s", err)
            STATE.consecutive_successes = 0
            STATE.consecutive_failures += 1

        if STATE.last_up is True:
            is_up = STATE.consecutive_failures < FAILURE_STREAK_REQUIRED
        else:
            is_up = STATE.consecutive_successes >= SUCCESS_STREAK_REQUIRED

        logger.info(
            "Check result: status=%s (success=%s/%s, failure=%s/%s, up=%s)",
            status_code,
            STATE.consecutive_successes,
            SUCCESS_STREAK_REQUIRED,
            STATE.consecutive_failures,
            FAILURE_STREAK_REQUIRED,
            is_up,
        )

        if STATE.last_up is False and is_up:
            await notify_all(
                app,
                "Сайт ожил.\n"
                "Проверка статуса сервиса авторизации ФСИ\n"
                f"URL: {TARGET_URL}\n"
                f"HTTP: {status_code}\n"
                f"Время: {now_str()}\n"
                "/stop - отписаться от рассылки",
            )

        STATE.last_up = is_up

        await asyncio.sleep(CHECK_INTERVAL_SEC)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    add_subscriber(chat_id)
    await update.message.reply_text(
        "Вы подписаны на уведомления.\n"
        f"Сайт считается ожившим только после {SUCCESS_STREAK_REQUIRED} "
        "успешных логинов подряд (HTTP 2xx).\n"
        f"Сайт считается упавшим только после {FAILURE_STREAK_REQUIRED} "
        "подряд неуспешных проверок.\n"
        "Команды: /status, /stop"
    )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    remove_subscriber(chat_id)
    await update.message.reply_text("Подписка отключена.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(status_text())


async def post_init(app: Application) -> None:
    init_db()
    app.create_task(monitor_loop(app))


def main() -> None:
    application = (
        Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    )
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
