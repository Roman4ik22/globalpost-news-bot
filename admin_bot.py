"""
Админ-бот GlobalPost. Управление настройками через Telegram.
Запуск: python admin_bot.py

Команды:
  /start    — показать все команды
  /post     — опубликовать новость прямо сейчас
  /format   — изменить формат (news / stat / analysis)
  /status   — текущие настройки
  /history  — последние 5 публикаций
  /pause    — приостановить / возобновить авто-публикацию
  /weekend  — вкл/выкл публикации на выходных
"""

import os
import sys
import json
import time
import logging
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SETTINGS_FILE = Path(__file__).parent / "settings.json"
HISTORY_FILE = Path(__file__).parent / "history.json"

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {"post_on_weekends": True, "format_override": None, "paused": False, "admin_ids": []}


def save_settings(settings: dict):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False))


def load_history() -> list[str]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text()).get("published", [])
        except (json.JSONDecodeError, KeyError):
            return []
    return []


def send_message(chat_id: int, text: str):
    requests.post(f"{API_BASE}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }, timeout=10)


def is_admin(user_id: int, settings: dict) -> bool:
    """Первый пользователь автоматически становится админом."""
    if not settings["admin_ids"]:
        settings["admin_ids"].append(user_id)
        save_settings(settings)
        logger.info(f"Первый админ зарегистрирован: {user_id}")
        return True
    return user_id in settings["admin_ids"]


def handle_command(message: dict):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()
    settings = load_settings()

    if not is_admin(user_id, settings):
        send_message(chat_id, "⛔ У вас немає доступу до цього бота.")
        return

    if text == "/start" or text == "/help":
        send_message(chat_id, (
            "🤖 <b>GlobalPost News Bot — Адмін-панель</b>\n\n"
            "/post — опублікувати новину зараз\n"
            "/format — змінити формат (news / stat / analysis)\n"
            "/status — поточні налаштування\n"
            "/history — останні 5 публікацій\n"
            "/pause — призупинити / відновити авто-публікацію\n"
            "/weekend — вкл/викл публікації на вихідних"
        ))

    elif text == "/status":
        fmt = settings.get("format_override") or "авто (за розкладом)"
        paused = "⏸ Призупинено" if settings.get("paused") else "▶️ Активний"
        weekend = "✅ Так" if settings.get("post_on_weekends") else "❌ Ні"
        history = load_history()
        last = history[-1] if history else "—"
        send_message(chat_id, (
            f"📊 <b>Статус бота</b>\n\n"
            f"Стан: {paused}\n"
            f"Формат: {fmt}\n"
            f"Вихідні: {weekend}\n"
            f"Всього публікацій: {len(history)}\n"
            f"Остання: {last}"
        ))

    elif text == "/pause":
        settings["paused"] = not settings.get("paused", False)
        save_settings(settings)
        state = "⏸ Призупинено" if settings["paused"] else "▶️ Відновлено"
        send_message(chat_id, f"{state}")

    elif text == "/weekend":
        settings["post_on_weekends"] = not settings.get("post_on_weekends", True)
        save_settings(settings)
        state = "✅ Публікації на вихідних увімкнено" if settings["post_on_weekends"] else "❌ Публікації на вихідних вимкнено"
        send_message(chat_id, state)

    elif text.startswith("/format"):
        parts = text.split()
        if len(parts) == 2 and parts[1] in ("news", "stat", "analysis", "auto"):
            if parts[1] == "auto":
                settings["format_override"] = None
            else:
                settings["format_override"] = parts[1]
            save_settings(settings)
            send_message(chat_id, f"✅ Формат змінено: <b>{parts[1]}</b>")
        else:
            send_message(chat_id, (
                "Використання: /format [тип]\n\n"
                "Типи:\n"
                "• <b>news</b> — новина\n"
                "• <b>stat</b> — цифра дня\n"
                "• <b>analysis</b> — аналітика\n"
                "• <b>auto</b> — за розкладом"
            ))

    elif text == "/history":
        history = load_history()
        if not history:
            send_message(chat_id, "Історія порожня.")
            return
        last5 = history[-5:]
        lines = [f"{i+1}. {url}" for i, url in enumerate(reversed(last5))]
        send_message(chat_id, "📋 <b>Останні публікації:</b>\n\n" + "\n".join(lines))

    elif text == "/post":
        send_message(chat_id, "⏳ Генерую та публікую новину...")
        try:
            # Импортируем и запускаем основной бот
            from bot import main as run_bot
            # Убираем проверку выходных для ручного запуска
            os.environ["FORCE_POST"] = "1"
            run_bot()
            send_message(chat_id, "✅ Новину опубліковано!")
        except Exception as e:
            send_message(chat_id, f"❌ Помилка: {e}")
        finally:
            os.environ.pop("FORCE_POST", None)

    else:
        send_message(chat_id, "Невідома команда. Напишіть /start для списку команд.")


def poll():
    """Поллинг Telegram для получения команд."""
    logger.info("Админ-бот запущен. Ожидаю команды...")
    offset = 0

    while True:
        try:
            resp = requests.get(f"{API_BASE}/getUpdates", params={
                "offset": offset,
                "timeout": 30,
            }, timeout=35)

            if resp.status_code != 200:
                logger.warning(f"getUpdates error: {resp.status_code}")
                time.sleep(5)
                continue

            data = resp.json()
            if not data.get("ok"):
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")
                if message and message.get("text", "").startswith("/"):
                    handle_command(message)

        except requests.exceptions.Timeout:
            continue
        except KeyboardInterrupt:
            logger.info("Админ-бот остановлен")
            break
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            time.sleep(5)


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("Установите TELEGRAM_BOT_TOKEN:")
        print("  export TELEGRAM_BOT_TOKEN='ваш_токен'")
        sys.exit(1)
    poll()
