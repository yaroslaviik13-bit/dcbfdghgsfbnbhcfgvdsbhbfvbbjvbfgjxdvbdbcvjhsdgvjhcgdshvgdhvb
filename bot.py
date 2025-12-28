#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Сначала импортируем все стандартные библиотеки
import asyncio
import json
import os
import re
import sqlite3
import time
import hashlib
import base64
from datetime import datetime, timedelta
from io import BytesIO

# Затем сторонние библиотеки
import requests
from PIL import Image
import pytesseract

# И только потом импорты telegram
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    BotCommand
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.constants import ChatAction

# ================= НАСТРОЙКИ =================
TELEGRAM_BOT_TOKEN = "8041973439:AAGhahaBEKUmQ5S52JHozLwllrrjrNQRS7k"
OPENROUTER_API_KEY = "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"
FLOOD_TIMEOUT = 3
ADMIN_ID = 6904586409
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
# ============================================

SYSTEM_PROMPT = """
Ты умный помощник. Решай задачи, объясняй, помогай.
Если задача — решай по шагам.
Если вопрос — дай понятный ответ.
Будь дружелюбным и полезным.
"""

# ---------- База данных ----------
def init_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            messages_count INTEGER DEFAULT 0,
            tokens INTEGER DEFAULT 100,
            next_token_reset TEXT,
            referral_code TEXT UNIQUE,
            referred_by TEXT,
            last_bonus_claim TEXT,
            join_date TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Таблица диалогов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    # Таблица промокодов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            tokens INTEGER,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT,
            used_by TEXT DEFAULT NULL,
            used_at TEXT DEFAULT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            max_uses INTEGER DEFAULT 1,
            use_count INTEGER DEFAULT 0
        )
    ''')

    conn.commit()
    conn.close()

def parse_datetime(dt_str):
    """Парсит дату из строки SQLite"""
    if not dt_str:
        return None

    # Убираем микросекунды если они есть
    dt_str = dt_str.split('.')[0]

    try:
        # Пробуем разные форматы
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d',
            '%d.%m.%Y %H:%M:%S',
            '%d.%m.%Y'
        ]

        for fmt in formats:
            try:
                return datetime.strptime(dt_str, fmt)
            except ValueError:
                continue

        return None
    except Exception:
        return None

# ---------- Функции для работы с пользователями ----------
def get_user(user_id):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def get_all_users_ids():
    """Получить список всех ID пользователей"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def create_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    # Генерируем уникальный реферальный код
    referral_code = hashlib.md5(f'{user_id}{time.time()}'.encode()).hexdigest()[:8].upper()

    # Устанавливаем сброс токенов через 30 дней
    next_reset = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('''
        INSERT OR IGNORE INTO users 
        (user_id, username, first_name, last_name, referral_code, next_token_reset) 
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name, referral_code, next_reset))

    conn.commit()
    conn.close()
    return referral_code

def update_user_messages(user_id):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET messages_count = messages_count + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def update_user_tokens(user_id, tokens_change):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET tokens = tokens + ? WHERE user_id = ?', (tokens_change, user_id))
    conn.commit()

    # Проверяем, не отрицательное ли количество токенов
    cursor.execute('SELECT tokens FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if result and result[0] < 0:
        cursor.execute('UPDATE users SET tokens = 0 WHERE user_id = ?', (user_id,))
        conn.commit()

    conn.close()

def update_user_tokens_direct(user_id, new_tokens_amount):
    """Прямое обновление количества токенов пользователя (админ)"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET tokens = ? WHERE user_id = ?', (new_tokens_amount, user_id))
    conn.commit()
    conn.close()

def get_user_tokens(user_id):
    """Получить количество токенов пользователя"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT tokens FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

# ---------- Функции для истории диалога ----------
def add_to_conversation(user_id, role, content):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO conversations (user_id, role, content)
        VALUES (?, ?, ?)
    ''', (user_id, role, content))
    conn.commit()
    conn.close()

def get_conversation(user_id, limit=10):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, content
        FROM conversations
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (user_id, limit))
    messages = cursor.fetchall()
    conn.close()

    # Возвращаем в правильном порядке (старые -> новые)
    messages.reverse()
    return messages

def clear_conversation(user_id):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM conversations WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# ---------- Функции для промокодов ----------
def create_promo_code(code, tokens, created_by, expires_in_days=30, max_uses=1):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    expires_at = (datetime.now() + timedelta(days=expires_in_days)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT OR REPLACE INTO promo_codes (code, tokens, created_by, expires_at, max_uses) 
        VALUES (?, ?, ?, ?, ?)
    ''', (code.upper(), tokens, created_by, expires_at, max_uses))
    conn.commit()
    conn.close()
    return True

def use_promo_code(code: str, user_id: int) -> tuple[bool, str]:
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    # Проверяем промокод
    cursor.execute('''
        SELECT *
        FROM promo_codes
        WHERE code = ?
          AND is_active = TRUE
    ''', (code.upper(),))
    promo = cursor.fetchone()

    if not promo:
        conn.close()
        return False, "Промокод не найден"

    # Проверяем срок действия
    expires_at = promo[4]
    if expires_at:
        expires_date = parse_datetime(expires_at)
        if expires_date and datetime.now() > expires_date:
            cursor.execute('UPDATE promo_codes SET is_active = FALSE WHERE code = ?', (code.upper(),))
            conn.commit()
            conn.close()
            return False, "Промокод истек"

    # Проверяем максимальное количество использований
    max_uses = promo[8] or 1
    use_count = promo[9] or 0

    if max_uses > 0 and use_count >= max_uses:
        cursor.execute('UPDATE promo_codes SET is_active = FALSE WHERE code = ?', (code.upper(),))
        conn.commit()
        conn.close()
        return False, "Промокод уже использован максимальное количество раз"

    # Проверяем, использовал ли уже пользователь этот промокод
    used_by = promo[6]  # used_by поле
    if used_by:
        try:
            used_list = json.loads(used_by)
            if str(user_id) in used_list:
                conn.close()
                return False, "Вы уже использовали этот промокод"
        except:
            pass
    else:
        used_list = []

    # Обновляем промокод
    used_list.append(str(user_id))
    new_use_count = use_count + 1
    is_active = True if (max_uses == 0 or new_use_count < max_uses) else False

    tokens_amount = promo[1]

    cursor.execute('''
        UPDATE promo_codes
        SET use_count = ?,
            used_by = ?,
            used_at = datetime('now'),
            is_active = ?
        WHERE code = ?
    ''', (new_use_count, json.dumps(used_list), is_active, code.upper()))

    # Начисляем токены пользователю
    cursor.execute('UPDATE users SET tokens = tokens + ? WHERE user_id = ?', (tokens_amount, user_id))

    conn.commit()
    conn.close()
    return True, f"Промокод активирован! Получено {tokens_amount} токенов"

def get_active_promo_codes():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT code, tokens, created_by, expires_at, max_uses, use_count
        FROM promo_codes
        WHERE is_active = TRUE
    ''')
    promos = cursor.fetchall()
    conn.close()
    return promos

def get_all_users():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username, first_name, last_name, tokens FROM users ORDER BY join_date DESC')
    users = cursor.fetchall()
    conn.close()
    return users

# ---------- Функция для оповещения пользователей ----------
async def broadcast_message(context: ContextTypes.DEFAULT_TYPE, message_text: str):
    """Отправить сообщение всем пользователям бота"""
    users = get_all_users_ids()
    successful = 0
    failed = 0

    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode='Markdown'
            )
            successful += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            failed += 1

    return successful, failed

# ---------- Главное меню ----------
def main_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("📝 Задать вопрос", callback_data="ask")],
        [InlineKeyboardButton("📷 Решить по фото", callback_data="photo")],
        [InlineKeyboardButton("🌍 Переводчик", callback_data="translate")],
        [InlineKeyboardButton("🧹 Очистить историю", callback_data="clear_history")],
        [InlineKeyboardButton("🎁 Промокоды", callback_data="promo_menu")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ]

    # Добавляем админ-панель для админа
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Админ-панель", callback_data="admin_panel")])

    return InlineKeyboardMarkup(keyboard)

def promo_menu():
    keyboard = [
        [InlineKeyboardButton("🎫 Ввести промокод", callback_data="enter_promo")],
        [InlineKeyboardButton("🎁 Ежедневный бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton("📤 Реферальная ссылка", callback_data="referral")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_panel_menu():
    keyboard = [
        [InlineKeyboardButton("🎫 Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton("👥 Выдать токены", callback_data="admin_give_tokens")],
        [InlineKeyboardButton("✏️ Изменить токены", callback_data="admin_edit_tokens")],
        [InlineKeyboardButton("📢 Оповестить всех", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "ask":
        await query.message.reply_text("📝 Напиши вопрос текстом")
        context.user_data['waiting_for'] = 'question'

    elif query.data == "photo":
        await query.message.reply_text("📷 Отправь фото задачи")
        context.user_data['waiting_for'] = 'photo'

    elif query.data == "translate":
        await query.message.reply_text("🌍 Введи текст для перевода")
        context.user_data['waiting_for'] = 'translate'

    elif query.data == "clear_history":
        clear_conversation(user_id)
        await query.message.reply_text("✅ История диалога очищена!")

    elif query.data == "help":  # ДОБАВЬТЕ ЭТОТ БЛОК
        help_text = (
            "🤖 *Текстовый помощник AI*\n\n"
            "📝 *Основные функции:*\n"
            "• Задать вопрос и получить развернутый ответ\n"
            "• Решать задачи с фото (распознавание текста)\n"
            "• Переводить тексты на разные языки\n"
            "• Очистка истории диалога\n\n"
            "🎁 *Промокоды и бонусы:*\n"
            "• Ежедневный бонус - каждый день новый промокод\n"
            "• Реферальная система - 50 токенов за друга\n"
            "• Ввод промокодов - активация полученных кодов\n\n"
            "📊 *Профиль:*\n"
            "• Количество сообщений\n"
            "• Баланс токенов\n"
            "• Дата следующего пополнения\n\n"
            "💎 *Токены* используются для:\n"
            "• Ответов на вопросы (1 токен)\n"
            "• Решения задач с фото (2 токена)\n\n"
            "📌 *Команды:*\n"
            "`/start` - запустить бота\n"
            "`/profile` - мой профиль\n"
            "`/promo` - меню промокодов\n"
            "`/help` - помощь"
        )
        await query.message.reply_text(help_text, parse_mode='Markdown')

    elif query.data == "promo_menu":
        await query.message.edit_text(
            "🎁 Меню промокодов и бонусов:",
            reply_markup=promo_menu()
        )

    elif query.data == "enter_promo":
        await query.message.reply_text("🎫 Пожалуйста, введите промокод:")
        context.user_data['waiting_for'] = 'promo_code'

    elif query.data == "daily_bonus":
        # Проверяем, когда последний раз получал бонус
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        cursor.execute('SELECT last_bonus_claim FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()

        last_claim = None
        if result and result[0]:
            last_claim = parse_datetime(result[0])

        if last_claim and (datetime.now() - last_claim) < timedelta(hours=24):
            hours_left = 24 - ((datetime.now() - last_claim).seconds // 3600)
            await query.message.reply_text(f"⏳ Вы уже получали бонус сегодня. Следующий через {hours_left} часов.")
            conn.close()
            return

        # Генерируем промокод для ежедневного бонуса
        daily_code = f"DAILY{hashlib.md5(f'{user_id}{datetime.now().date()}'.encode()).hexdigest()[:6].upper()}"
        tokens = 50

        create_promo_code(daily_code, tokens, ADMIN_ID, expires_in_days=1, max_uses=1)

        # Обновляем время последнего бонуса
        cursor.execute('UPDATE users SET last_bonus_claim = datetime("now") WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()

        await query.message.reply_text(
            f"🎉 Ежедневный бонус!\n"
            f"Ваш промокод: `{daily_code}`\n"
            f"Токенов: {tokens}\n"
            f"Действует 24 часа\n\n"
            f"Используйте команду /promo или кнопку 'Ввести промокод'",
            parse_mode='Markdown'
        )

    elif query.data == "referral":
        user = get_user(user_id)
        if user:
            ref_code = user[7]
            bot_username = (await context.bot.get_me()).username
            ref_link = f"https://t.me/{bot_username}?start={ref_code}"
            await query.message.reply_text(
                f"📤 Ваша реферальная ссылка:\n\n"
                f"`{ref_link}`\n\n"
                f"Приглашайте друзей! За каждого приглашенного "
                f"вы получите 50 токенов, а друг получит 25 токенов!",
                parse_mode='Markdown'
            )

    elif query.data == "admin_panel":
        if user_id == ADMIN_ID:
            await query.message.edit_text(
                "👑 Админ-панель:",
                reply_markup=admin_panel_menu()
            )

    elif query.data == "admin_create_promo":
        if user_id == ADMIN_ID:
            await query.message.reply_text(
                "🎫 Создание промокода\n\n"
                "Отправьте в формате:\n"
                "`КОД:КОЛИЧЕСТВО_ТОКЕНОВ:МАКСИМАЛЬНОЕ_ИСПОЛЬЗОВАНИЕ`\n\n"
                "Пример: `SUMMER2024:100:10`\n"
                "Для бессрочного: `FOREVER:100:0`",
                parse_mode='Markdown'
            )
            context.user_data['waiting_for'] = 'admin_create_promo'

    elif query.data == "admin_give_tokens":
        if user_id == ADMIN_ID:
            users = get_all_users()
            if not users:
                await query.message.reply_text("📭 В боте пока нет пользователей")
                return

            keyboard = []
            for user in users[:50]:
                user_info = f"{user[2]} (@{user[1]})" if user[1] else f"{user[2]}"
                display_text = f"{user_info[:20]} - {user[4]} токенов"
                keyboard.append([
                    InlineKeyboardButton(
                        display_text,
                        callback_data=f"admin_user_{user[0]}"
                    )
                ])

            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")])

            await query.message.edit_text(
                "👥 Выберите пользователя для выдачи токенов:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif query.data.startswith("admin_user_"):
        if user_id == ADMIN_ID:
            selected_user_id = int(query.data.split('_')[2])
            context.user_data['selected_user'] = selected_user_id
            await query.message.reply_text(
                f"Введите количество токенов для выдачи пользователю ID {selected_user_id}:\n"
                f"(Для отмены отправьте 'отмена')"
            )
            context.user_data['waiting_for'] = 'admin_give_tokens_amount'

    elif query.data == "admin_edit_tokens":
        if user_id == ADMIN_ID:
            users = get_all_users()
            if not users:
                await query.message.reply_text("📭 В боте пока нет пользователей")
                return

            keyboard = []
            for user in users[:50]:
                user_info = f"{user[2]} (@{user[1]})" if user[1] else f"{user[2]}"
                display_text = f"{user_info[:20]} - {user[4]} токенов"
                keyboard.append([
                    InlineKeyboardButton(
                        display_text,
                        callback_data=f"admin_edituser_{user[0]}"
                    )
                ])

            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")])

            await query.message.edit_text(
                "✏️ Выберите пользователя для изменения токенов:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif query.data.startswith("admin_edituser_"):
        if user_id == ADMIN_ID:
            selected_user_id = int(query.data.split('_')[2])
            context.user_data['edit_user'] = selected_user_id

            user_data = get_user(selected_user_id)
            current_tokens = user_data[5] if user_data else 0

            await query.message.reply_text(
                f"✏️ Изменение токенов пользователя ID {selected_user_id}\n"
                f"Текущее количество: {current_tokens}\n\n"
                f"Введите новое количество токенов:\n"
                f"(Для отмены отправьте 'отмена')"
            )
            context.user_data['waiting_for'] = 'admin_edit_tokens_amount'

    elif query.data == "admin_broadcast":
        if user_id == ADMIN_ID:
            await query.message.reply_text(
                "📢 *Оповещение всех пользователей*\n\n"
                "Отправьте сообщение, которое получит каждый пользователь бота.\n\n"
                "Можно использовать Markdown разметку:\n"
                "*жирный* _курсив_ `код`\n\n"
                "Для отмены отправьте 'отмена'",
                parse_mode='Markdown'
            )
            context.user_data['waiting_for'] = 'admin_broadcast_message'

    elif query.data == "admin_stats":
        if user_id == ADMIN_ID:
            try:
                users = get_all_users()
                promos = get_active_promo_codes()
                total_users = len(users)
                total_tokens = sum(user[4] for user in users)
                active_promos = len(promos)

                # Подсчитываем общее количество сообщений
                conn = sqlite3.connect('bot_database.db')
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM conversations")
                total_messages = cursor.fetchone()[0]
                conn.close()

                stats_text = f"""
📊 СТАТИСТИКА БОТА

👥 ПОЛЬЗОВАТЕЛИ: {total_users}
💬 СООБЩЕНИЯ: {total_messages}
💰 ТОКЕНОВ В СИСТЕМЕ: {total_tokens}
🎫 АКТИВНЫХ ПРОМОКОДОВ: {active_promos}

👇 Выберите пользователя для изменения токенов:"""

                keyboard = []
                for user in users:
                    user_id_from_db = user[0]
                    username = f"@{user[1]}" if user[1] else user[2]
                    tokens = user[4]

                    if len(username) > 15:
                        display_name = username[:12] + "..."
                    else:
                        display_name = username

                    button_text = f"{display_name} - {tokens}💎"
                    keyboard.append([
                        InlineKeyboardButton(
                            button_text,
                            callback_data=f"admin_edituser_{user_id_from_db}"
                        )
                    ])

                keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")])

                await query.message.edit_text(
                    stats_text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

            except Exception as e:
                print(f"Ошибка статистики: {e}")
                await query.message.reply_text(f"❌ Ошибка: {e}")

    elif query.data == "back_to_main":
        await query.message.edit_text(
            "Главное меню:",
            reply_markup=main_menu(user_id)
        )

# ---------- Антифлуд ----------
last_message_time = {}

def is_flood(user_id):
    now = time.time()
    last = last_message_time.get(user_id, 0)
    if now - last < FLOOD_TIMEOUT:
        return True
    last_message_time[user_id] = now
    return False

# ---------- DeepSeek ответ ----------
def deepseek_reply(text: str, conversation_history=None):
    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    # Формируем сообщения с историей
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if conversation_history:
        for role, content in conversation_history:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": text})

    payload = {
        "model": "deepseek/deepseek-chat",
        "messages": messages,
        "max_tokens": 1000
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"DeepSeek error: {e}")
        return "Извините, произошла ошибка при обработке запроса."

# ---------- Команды ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id

    # Создаем пользователя если его нет
    create_user(user_id, user.username, user.first_name, user.last_name)

    # Проверяем реферальный код
    if context.args:
        ref_code = context.args[0]
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        cursor.execute('SELECT user_id FROM users WHERE referral_code = ? AND user_id != ?',
                       (ref_code, user_id))
        referrer = cursor.fetchone()

        if referrer:
            cursor.execute('UPDATE users SET tokens = tokens + 50 WHERE user_id = ?', (referrer[0],))
            cursor.execute('UPDATE users SET referred_by = ? WHERE user_id = ?', (referrer[0], user_id))
            cursor.execute('UPDATE users SET tokens = tokens + 25 WHERE user_id = ?', (user_id,))
            conn.commit()

            await update.message.reply_text(
                "🎉 Вы зарегистрировались по реферальной ссылке! "
                "Получено 25 бонусных токенов!"
            )

            try:
                await context.bot.send_message(
                    chat_id=referrer[0],
                    text=f"🎉 По вашей реферальной ссылке зарегистрировался новый пользователь!\n"
                         f"Вы получили 50 токенов! 💎"
                )
            except:
                pass

        conn.close()

    welcome_text = (
        f"👋 Привет, {user.first_name}! Я текстовый помощник AI.\n\n"
        f"💎 У вас есть стартовые 100 токенов!\n"
        f"🎁 Получайте больше токенов через:\n"
        "• Ежедневные бонусы\n"
        "• Реферальная система\n"
        "• Промокоды\n\n"
        "📝 Используйте меню ниже ⬇️"
    )

    await update.message.reply_text(welcome_text, reply_markup=main_menu(user_id))

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id

    user_data = get_user(user_id)

    if user_data:
        messages_count = user_data[4]
        tokens = user_data[5]
        next_reset = user_data[6]

        if next_reset:
            next_reset_date = parse_datetime(next_reset)
            if next_reset_date:
                reset_str = next_reset_date.strftime("%d.%m.%Y %H:%M")
            else:
                reset_str = "не установлено"
        else:
            reset_str = "не установлено"

        join_date_str = "неизвестно"
        if len(user_data) > 10 and user_data[10]:
            join_date = parse_datetime(user_data[10])
            if join_date:
                join_date_str = join_date.strftime("%d.%m.%Y")

        profile_text = (
            f"🧑 *Профиль*\n\n"
            f"👤 Имя: {user.first_name}\n"
            f"🆔 ID: `{user.id}`\n"
            f"📊 Сообщений отправлено: {messages_count}\n"
            f"💎 Токенов: {tokens}\n"
            f"🔄 Следующее обновление: {reset_str}\n"
            f"📅 Зарегистрирован: {join_date_str}"
        )

        keyboard = [
            [InlineKeyboardButton("🎁 Реферальная ссылка", callback_data="referral")],
            [InlineKeyboardButton("🎫 Промокоды", callback_data="promo_menu")],
            [InlineKeyboardButton("🎁 Получить бонус", callback_data="daily_bonus")]
        ]

        await update.message.reply_text(
            profile_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("Профиль не найден")

async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎁 Меню промокодов и бонусов:",
        reply_markup=promo_menu()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *Текстовый помощник AI*\n\n"
        "📝 *Основные функции:*\n"
        "• Задать вопрос и получить развернутый ответ\n"
        "• Решать задачи с фото (распознавание текста)\n"
        "• Переводить тексты на разные языки\n"
        "• Очистка истории диалога\n\n"
        "🎁 *Промокоды и бонусы:*\n"
        "• Ежедневный бонус - каждый день новый промокод\n"
        "• Реферальная система - 50 токенов за друга\n"
        "• Ввод промокодов - активация полученных кодов\n\n"
        "📊 *Профиль:*\n"
        "• Количество сообщений\n"
        "• Баланс токенов\n"
        "• Дата следующего пополнения\n\n"
        "💎 *Токены* используются для:\n"
        "• Ответов на вопросы (1 токен)\n"
        "• Решения задач с фото (2 токена)\n\n"
        "📌 *Команды:*\n"
        "`/start` - запустить бота\n"
        "`/profile` - мой профиль\n"
        "`/promo` - меню промокодов\n"
        "`/help` - помощь"
    )

    await update.message.reply_text(help_text, parse_mode='Markdown')

# ---------- Обработка текста ----------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    if is_flood(user_id):
        return

    # Проверяем, что ожидается от пользователя
    if 'waiting_for' in context.user_data:
        waiting_for = context.user_data['waiting_for']

        if waiting_for == 'promo_code':
            success, message = use_promo_code(text, user_id)
            await update.message.reply_text(message)
            del context.user_data['waiting_for']
            return

        elif waiting_for == 'admin_create_promo' and user_id == ADMIN_ID:
            try:
                parts = text.split(':')
                if len(parts) >= 2:
                    code = parts[0].strip().upper()
                    tokens = int(parts[1].strip())
                    max_uses = int(parts[2].strip()) if len(parts) > 2 else 1

                    create_promo_code(code, tokens, ADMIN_ID, max_uses=max_uses)
                    await update.message.reply_text(
                        f"✅ Промокод создан!\n"
                        f"Код: `{code}`\n"
                        f"Токенов: {tokens}\n"
                        f"Макс. использований: {max_uses}"
                    )
                else:
                    await update.message.reply_text(
                        "❌ Неверный формат. Используйте: КОД:ТОКЕНЫ:МАКС_ИСПОЛЬЗОВАНИЙ"
                    )
            except ValueError:
                await update.message.reply_text("❌ Неверный формат чисел")
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {e}")

            del context.user_data['waiting_for']
            return

        elif waiting_for == 'admin_give_tokens_amount' and user_id == ADMIN_ID:
            if text.lower() == 'отмена':
                await update.message.reply_text("❌ Отменено")
            else:
                try:
                    tokens = int(text)
                    selected_user = context.user_data.get('selected_user')

                    if selected_user:
                        update_user_tokens(selected_user, tokens)
                        user_data = get_user(selected_user)
                        username = f"@{user_data[1]}" if user_data and user_data[1] else "пользователь"

                        await update.message.reply_text(
                            f"✅ Выдано {tokens} токенов пользователю {username} (ID: {selected_user})"
                        )
                    else:
                        await update.message.reply_text("❌ Пользователь не выбран")
                except ValueError:
                    await update.message.reply_text("❌ Введите число")
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка: {e}")

            del context.user_data['waiting_for']
            if 'selected_user' in context.user_data:
                del context.user_data['selected_user']
            return

        elif waiting_for == 'admin_edit_tokens_amount' and user_id == ADMIN_ID:
            if text.lower() == 'отмена':
                await update.message.reply_text("❌ Отменено")
            else:
                try:
                    edit_user = context.user_data.get('edit_user')
                    if edit_user:
                        user_data = get_user(edit_user)
                        current_tokens = user_data[5] if user_data else 0
                        username = f"@{user_data[1]}" if user_data[1] else user_data[2]

                        # Прямо устанавливаем новое значение токенов
                        new_tokens = int(text)
                        update_user_tokens_direct(edit_user, new_tokens)

                        await update.message.reply_text(
                            f"✅ Установлено {new_tokens} токенов пользователю {username}\n"
                            f"💎 Предыдущий баланс: {current_tokens}"
                        )
                    else:
                        await update.message.reply_text("❌ Пользователь не выбран")
                except ValueError:
                    await update.message.reply_text("❌ Введите число")
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка: {e}")

            del context.user_data['waiting_for']
            if 'edit_user' in context.user_data:
                del context.user_data['edit_user']
            return

        elif waiting_for == 'admin_broadcast_message' and user_id == ADMIN_ID:
            if text.lower() == 'отмена':
                await update.message.reply_text("❌ Отменено")
            else:
                successful, failed = await broadcast_message(context, text)
                await update.message.reply_text(
                    f"📢 Рассылка завершена!\n"
                    f"✅ Успешно: {successful}\n"
                    f"❌ Не доставлено: {failed}"
                )

            del context.user_data['waiting_for']
            return

        elif waiting_for in ['question', 'translate']:
            # Удаляем ожидание и продолжаем как обычное сообщение
            del context.user_data['waiting_for']
            # Продолжаем выполнение для обработки как обычного текста

    # ================ ОБЫЧНЫЕ ВОПРОСЫ ================
    try:
        # Проверяем баланс токенов
        tokens = get_user_tokens(user_id)
        if tokens <= 0:
            await update.message.reply_text("❌ Недостаточно токенов!")
            return

        # Списываем токен за вопрос
        update_user_tokens(user_id, -1)

        await update.message.reply_text("🧠 Думаю...")

        # Получаем историю диалога
        conversation_history = get_conversation(user_id, limit=10)

        # Добавляем вопрос в историю
        add_to_conversation(user_id, "user", text)

        # Получаем ответ от AI
        answer = deepseek_reply(text, conversation_history)

        # Добавляем ответ в историю
        add_to_conversation(user_id, "assistant", answer)

        # Обновляем счетчик сообщений
        update_user_messages(user_id)

        # Отправляем ответ
        await update.message.reply_text(answer)

    except Exception as e:
        print(f"Ошибка при обработке текста: {e}")
        # Возвращаем токен при ошибке
        update_user_tokens(user_id, 1)
        await update.message.reply_text("❌ Произошла ошибка при обработке запроса")

# ---------- Фото ----------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if is_flood(user_id):
        return

    # Проверяем баланс токенов
    tokens = get_user_tokens(user_id)
    if tokens <= 0:
        await update.message.reply_text("❌ Недостаточно токенов!")
        return

    # Списываем токены за обработку фото
    tokens_to_deduct = 2
    update_user_tokens(user_id, -tokens_to_deduct)

    await update.message.reply_text("📷 Читаю фото...")

    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        bio = BytesIO()
        await file.download_to_memory(out=bio)
        bio.seek(0)

        image = Image.open(bio)
        text = pytesseract.image_to_string(image, lang="rus+eng").strip()

        if not text:
            update_user_tokens(user_id, tokens_to_deduct)
            await update.message.reply_text("❌ Не удалось распознать текст")
            return

        await update.message.reply_text("🧠 Решаю...")

        conversation_history = get_conversation(user_id, limit=5)
        add_to_conversation(user_id, "user", f"Фото с текстом: {text}")
        answer = deepseek_reply(f"Реши задачу:\n{text}", conversation_history)
        add_to_conversation(user_id, "assistant", answer)

        await update.message.reply_text(answer)

    except Exception as e:
        print("PHOTO ERROR:", e)
        update_user_tokens(user_id, tokens_to_deduct)
        await update.message.reply_text("❌ Ошибка обработки фото")

# ---------- MAIN ----------
async def set_commands(app):
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("profile", "Мой профиль"),
        BotCommand("promo", "Промокоды и бонусы"),
        BotCommand("help", "Помощь")
    ]
    await app.bot.set_my_commands(commands)

def main():
    # Инициализируем базу данных
    init_db()

    try:
        # Упрощенная версия без прокси
        app = ApplicationBuilder() \
            .token(TELEGRAM_BOT_TOKEN) \
            .read_timeout(60) \
            .connect_timeout(60) \
            .build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("profile", profile))
        app.add_handler(CommandHandler("promo", promo_command))
        app.add_handler(CommandHandler("help", help_command))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        app.add_handler(CallbackQueryHandler(menu_callback))

        app.post_init = set_commands

        print("=" * 50)
        print("🤖 ТЕКСТОВЫЙ БОТ ЗАПУЩЕН")
        print(f"👑 Админ ID: {ADMIN_ID}")
        print(f"🧠 AI: DeepSeek")
        print(f"📷 OCR: Tesseract")
        print("=" * 50)
        print("\n📱 Бот готов к работе!")
        print("✅ Функции: вопросы, фото-задачи, переводы, промокоды")
        print("❌ Генерация изображений: отдельный бот")
        print("\nИспользуйте /start в Telegram")

        app.run_polling()

    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        print("\n📌 Проверь:")
        print("1. Интернет соединение")
        print("2. Токен бота (правильный ли?)")
        print("3. Попробуй VPN если Telegram заблокирован")

if __name__ == "__main__":

    main()
