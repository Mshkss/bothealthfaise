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
TARGET_URL = os.getenv("TARGET_URL", "https://online.fasie.ru/api/v3/auth/sign-in").strip()
ACCOUNT_URL = os.getenv("ACCOUNT_URL", "https://online.fasie.ru/api/v2/account").strip()
AUTH_LOGIN = os.getenv("AUTH_LOGIN", "").strip()
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "").strip()
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "45"))
REQUEST_TIMEOUT_SEC = int(os.getenv("REQUEST_TIMEOUT_SEC", "8"))
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")
SUCCESS_STREAK_REQUIRED = int(os.getenv("SUCCESS_STREAK_REQUIRED", "5"))
FAILURE_STREAK_REQUIRED = int(os.getenv("FAILURE_STREAK_REQUIRED", "3"))
ACCOUNT_SUCCESS_STREAK_REQUIRED = int(os.getenv("ACCOUNT_SUCCESS_STREAK_REQUIRED", "5"))
ACCOUNT_FAILURE_STREAK_REQUIRED = int(os.getenv("ACCOUNT_FAILURE_STREAK_REQUIRED", "3"))
ACCOUNT_UNAUTHORIZED_OK_STATUSES = {
    int(x.strip())
    for x in os.getenv("ACCOUNT_UNAUTHORIZED_OK_STATUSES", "401,403").split(",")
    if x.strip()
}


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not AUTH_LOGIN or not AUTH_PASSWORD:
    raise RuntimeError("AUTH_LOGIN and AUTH_PASSWORD are required")


@dataclass
class EndpointState:
    last_status_code: Optional[int] = None
    last_error: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_up: Optional[bool] = None
    consecutive_successes: int = 0
    consecutive_failures: int = 0


@dataclass
class MonitorState:
    auth: EndpointState
    account: EndpointState
    overall_up: Optional[bool] = None


STATE = MonitorState(auth=EndpointState(), account=EndpointState())


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


def check_auth() -> tuple[Optional[int], Optional[str], Optional[str]]:
    try:
        response = requests.post(
            TARGET_URL,
            json={"login": AUTH_LOGIN, "password": AUTH_PASSWORD},
            timeout=REQUEST_TIMEOUT_SEC,
        )
        return response.status_code, None, response.text
    except requests.RequestException as err:
        return None, str(err), None


def check_account() -> tuple[Optional[int], Optional[str]]:
    try:
        response = requests.get(ACCOUNT_URL, timeout=REQUEST_TIMEOUT_SEC)
        return response.status_code, None
    except requests.RequestException as err:
        return None, str(err)


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def endpoint_status_text(
    title: str,
    url: str,
    endpoint: EndpointState,
) -> str:
    checked = (
        endpoint.last_checked_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if endpoint.last_checked_at
        else "never"
    )
    if endpoint.last_status_code is not None:
        state_label = "UP" if endpoint.last_up else "DOWN"
        details = f"HTTP status: {endpoint.last_status_code}"
        if endpoint.last_status_code >= 400 and endpoint.last_status_code not in ACCOUNT_UNAUTHORIZED_OK_STATUSES:
            details = (
                f"HTTP status: {endpoint.last_status_code}\n"
                "Сервис личного кабинета не работает"
            )
        return (
            f"{title}\n"
            f"URL: {url}\n"
            f"State: {state_label}\n"
            f"{details}\n"
            f"Checked: {checked}"
        )
    return (
        f"{title}\n"
        f"URL: {url}\n"
        "State: UNKNOWN\n"
        f"Last error: {endpoint.last_error}\n"
        f"Checked: {checked}"
    )


def status_text() -> str:
    overall = "UNKNOWN"
    if STATE.overall_up is True:
        overall = "UP"
    elif STATE.overall_up is False:
        overall = "DOWN"
    return (
        "Проверка статуса сервиса авторизации ФСИ\n"
        f"Overall state: {overall}\n\n"
        f"{endpoint_status_text('Эндпоинт авторизации', TARGET_URL, STATE.auth)}\n\n"
        f"{endpoint_status_text('Эндпоинт аккаунта', ACCOUNT_URL, STATE.account)}"
    )


def evaluate_endpoint(
    endpoint: EndpointState,
    success: bool,
    success_required: int,
    failure_required: int,
) -> bool:
    if success:
        endpoint.consecutive_successes += 1
        endpoint.consecutive_failures = 0
    else:
        endpoint.consecutive_successes = 0
        endpoint.consecutive_failures += 1

    if endpoint.last_up is True:
        return endpoint.consecutive_failures < failure_required
    return endpoint.consecutive_successes >= success_required


async def monitor_loop(app: Application) -> None:
    logger.info(
        "Monitor started: auth_url=%s, account_url=%s, interval=%ss",
        TARGET_URL,
        ACCOUNT_URL,
        CHECK_INTERVAL_SEC,
    )
    while True:
        auth_status_code, auth_err, _ = await asyncio.to_thread(check_auth)
        auth_state = STATE.auth
        auth_state.last_checked_at = datetime.now(timezone.utc)
        auth_state.last_status_code = auth_status_code
        auth_state.last_error = auth_err
        auth_success = auth_status_code is not None and 200 <= auth_status_code < 300
        auth_up = evaluate_endpoint(
            auth_state,
            auth_success,
            SUCCESS_STREAK_REQUIRED,
            FAILURE_STREAK_REQUIRED,
        )
        auth_state.last_up = auth_up

        account_status_code, account_err = await asyncio.to_thread(check_account)
        account_state = STATE.account
        account_state.last_checked_at = datetime.now(timezone.utc)
        account_state.last_status_code = account_status_code
        account_state.last_error = account_err
        account_success = (
            account_status_code is not None
            and (
                (200 <= account_status_code < 300)
                or (account_status_code in ACCOUNT_UNAUTHORIZED_OK_STATUSES)
            )
        )
        account_up = evaluate_endpoint(
            account_state,
            account_success,
            ACCOUNT_SUCCESS_STREAK_REQUIRED,
            ACCOUNT_FAILURE_STREAK_REQUIRED,
        )
        account_state.last_up = account_up

        overall_up = auth_up and account_up

        logger.info(
            "Check result: "
            "auth_status=%s auth_up=%s auth_streak=%s/%s auth_fail_streak=%s/%s, "
            "account_status=%s account_up=%s account_streak=%s/%s account_fail_streak=%s/%s, "
            "overall_up=%s",
            auth_status_code,
            auth_up,
            auth_state.consecutive_successes,
            SUCCESS_STREAK_REQUIRED,
            auth_state.consecutive_failures,
            FAILURE_STREAK_REQUIRED,
            account_status_code,
            account_up,
            account_state.consecutive_successes,
            ACCOUNT_SUCCESS_STREAK_REQUIRED,
            account_state.consecutive_failures,
            ACCOUNT_FAILURE_STREAK_REQUIRED,
            overall_up,
        )

        if STATE.overall_up is False and overall_up:
            await notify_all(
                app,
                "Сайт ожил.\n"
                "Проверка статуса сервиса авторизации ФСИ\n"
                f"URL (auth): {TARGET_URL}\n"
                f"URL (account): {ACCOUNT_URL}\n"
                f"Время: {now_str()}\n"
                "/stop - отписаться от рассылки",
            )

        STATE.overall_up = overall_up
        await asyncio.sleep(CHECK_INTERVAL_SEC)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    add_subscriber(chat_id)
    await update.message.reply_text(
        "Проверка статуса сервиса авторизации ФСИ\n"
        "Вы подписаны на уведомления.\n"
        "Команды: /status, /stop"
    )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    remove_subscriber(chat_id)
    await update.message.reply_text(
        "Проверка статуса сервиса авторизации ФСИ\nПодписка отключена."
    )


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
