#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import threading
import base64
import re
import time
import hashlib
from io import BytesIO
from datetime import datetime
from functools import wraps
from PIL import Image, ImageEnhance
import pytesseract

from flask import Flask, request, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

CARD_NUMBER = "2200702150754195"
RECIPIENT = "Мухаммад А"
BANK_NAME = "Т-Банк"

MAX_SCREENSHOT_SIZE = 5 * 1024 * 1024  # 5 MB

# Ключевые слова для проверки оплаты
PAYMENT_KEYWORDS = [
    'оплат', 'перевод', 'чек', 'сумм', 'итог', 'к оплате',
    'списание', 'зачислен', 'успешно', 'платеж', 'переведен', 'получен',
    '₽', 'руб'
]
BANK_KEYWORDS = [
    'тинькофф', 'т-банк', 'сбербанк', 'альфа-банк', 'втб',
    'открытие', 'газпромбанк', 'росбанк', 'мтс банк'
]

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ================= SQLite =================
DB = "shop.db"
DB_LOCK = threading.RLock()

def get_db():
    conn = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        level TEXT,
        price INTEGER,
        screenshot TEXT,
        screenshot_hash TEXT,
        ocr_text TEXT,
        ocr_amount REAL,
        is_payment_confirmed INTEGER DEFAULT 0,
        payment_confidence INTEGER DEFAULT 0,
        fraud_details TEXT,
        validation_status TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        text TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS rate_limits (
        user_id INTEGER,
        action TEXT,
        created_at INTEGER,
        PRIMARY KEY (user_id, action)
    )
    """)
    conn.commit()
    conn.close()

init()

# ================= RATE LIMIT =================
def check_rate_limit(user_id, action="order", seconds=30):
    conn = get_db()
    now = int(time.time())
    cutoff = now - seconds
    conn.execute("DELETE FROM rate_limits WHERE created_at < ?", (cutoff,))
    row = conn.execute(
        "SELECT created_at FROM rate_limits WHERE user_id=? AND action=?",
        (user_id, action)
    ).fetchone()
    if row:
        if now - row["created_at"] < seconds:
            conn.close()
            return False, f"Подождите {seconds} секунд между заказами"
    conn.execute(
        "INSERT OR REPLACE INTO rate_limits (user_id, action, created_at) VALUES (?, ?, ?)",
        (user_id, action, now)
    )
    conn.commit()
    conn.close()
    return True, "OK"

# ================= ПРОВЕРКА НА ОПЛАТУ (без OpenCV) =================
def check_payment_screenshot(image_bytes):
    """Проверяет, похоже ли изображение на скриншот оплаты (только Pillow + pytesseract)"""
    reasons = []
    confidence = 0
    payment_found = 0
    bank_found = 0

    try:
        img = Image.open(BytesIO(image_bytes))
        gray = img.convert('L')
        enhancer = ImageEnhance.Contrast(gray)
        gray = enhancer.enhance(2.0)
        gray = gray.point(lambda x: 0 if x < 128 else 255, '1')

        try:
            text = pytesseract.image_to_string(gray, lang='rus+eng', config='--psm 6', timeout=10)
        except:
            text = pytesseract.image_to_string(gray, config='--psm 6', timeout=10)
        text_lower = text.lower()

        for kw in PAYMENT_KEYWORDS:
            if kw in text_lower:
                payment_found += 1
                confidence += 8
        for kw in BANK_KEYWORDS:
            if kw in text_lower:
                bank_found += 1
                confidence += 12

        amount_match = re.search(r'(\d+)[\s]*[₽руб]', text_lower)
        has_amount = bool(amount_match)
        if has_amount:
            confidence += 30
            reasons.append(f"сумма {amount_match.group(1)}₽")

        date_match = re.search(r'\d{2}[./]\d{2}[./]\d{4}', text)
        has_date = bool(date_match)
        if has_date:
            confidence += 20
            reasons.append(f"дата {date_match.group()}")

        if len(text) > 50:
            confidence += 5

        is_payment = has_amount and has_date and confidence >= 50
        reasons_text = ", ".join(reasons) if reasons else "нет признаков оплаты"
        return is_payment, min(100, confidence), reasons_text

    except Exception as e:
        print(f"Payment check error: {e}")
        return False, 0, f"ошибка: {e}"

def extract_amount(text):
    if not text:
        return None
    patterns = [
        r'(\d+)\s*₽',
        r'(\d+)\s*руб',
        r'сумма[:\s]*(\d+)',
        r'к оплате[:\s]*(\d+)'
    ]
    clean = text.replace('\n', ' ').strip()
    for pat in patterns:
        m = re.search(pat, clean, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except:
                continue
    return None

def simple_ocr(image_bytes):
    try:
        img = Image.open(BytesIO(image_bytes))
        gray = img.convert('L')
        enhancer = ImageEnhance.Contrast(gray)
        gray = enhancer.enhance(2.0)
        text = pytesseract.image_to_string(gray, lang='rus+eng', config='--psm 6', timeout=10)
        return text
    except Exception as e:
        print(f"OCR error: {e}")
        return ""

# ================= УРОВНИ =================
LEVELS = {
    "easy": {"name": "Лёгкий", "price": 99, "icon": "🟢", "features": ["Защита от спама", "Автоответы", "Приветствие", "Кнопка меню", "Логи 100 сообщ"]},
    "medium": {"name": "Средний", "price": 249, "icon": "🟡", "features": ["Всё из Лёгкого", "Рассылка", "Админ-панель", "Чёрный список", "Статистика"]},
    "hard": {"name": "Сложный", "price": 499, "icon": "🔴", "features": ["Всё из Среднего", "Авторассылка", "Google Sheets", "Скидки", "Аналитика", "1000+ юзеров"]}
}

# ================= ФУНКЦИИ БД =================
def add_order(user_id, username, level, screenshot=None, screenshot_hash=None,
              ocr_text=None, ocr_amount=None, is_payment=False, payment_confidence=0,
              fraud_details=None, validation_status=None):
    conn = get_db()
    cur = conn.cursor()
    price = LEVELS[level]["price"]
    cur.execute("""
        INSERT INTO orders (user_id, username, level, price, screenshot, screenshot_hash,
                           ocr_text, ocr_amount, is_payment_confirmed, payment_confidence,
                           fraud_details, validation_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, username, level, price, screenshot, screenshot_hash,
          ocr_text, ocr_amount, 1 if is_payment else 0, payment_confidence,
          fraud_details, validation_status, datetime.now().isoformat()))
    conn.commit()
    oid = cur.lastrowid
    conn.close()
    return oid

def get_orders(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_all_orders(status=None):
    conn = get_db()
    cur = conn.cursor()
    if status:
        cur.execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC", (status,))
    else:
        cur.execute("SELECT * FROM orders ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def update_order_status(order_id, status):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    conn.close()
    return row

def add_review(user_id, username, text):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO reviews (user_id, username, text, created_at) VALUES (?, ?, ?, ?)",
                (user_id, username, text, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_reviews():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT 50")
    rows = cur.fetchall()
    conn.close()
    return rows

def get_stats():
    conn = get_db()
    cur = conn.cursor()
    total = cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    pending = cur.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
    completed = cur.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0]
    rejected = cur.execute("SELECT COUNT(*) FROM orders WHERE status='rejected'").fetchone()[0]
    revenue = cur.execute("SELECT SUM(price) FROM orders WHERE status='completed'").fetchone()[0] or 0
    reviews_count = cur.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    rejected_payment = cur.execute("SELECT COUNT(*) FROM orders WHERE is_payment_confirmed=0").fetchone()[0]
    conn.close()
    return {"total": total, "pending": pending, "completed": completed, "rejected": rejected,
            "revenue": revenue, "reviews": reviews_count, "rejected_payment": rejected_payment}

# ================= АДМИН-КЛАВИАТУРЫ =================
def admin_main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📦 Все заказы", callback_data="admin_all"),
        InlineKeyboardButton("⏳ В ожидании", callback_data="admin_pending"),
        InlineKeyboardButton("✅ Выполненные", callback_data="admin_completed"),
        InlineKeyboardButton("❌ Отклонённые", callback_data="admin_rejected"),
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("⭐ Отзывы", callback_data="admin_reviews"),
        InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")
    )
    return kb

def order_action_keyboard(order_id, status):
    kb = InlineKeyboardMarkup(row_width=2)
    if status == "pending":
        kb.add(
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{order_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{order_id}")
        )
    kb.add(InlineKeyboardButton("◀ Назад", callback_data="admin_back"))
    return kb

def broadcast_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("👥 Всем пользователям", callback_data="broadcast_all"),
        InlineKeyboardButton("👤 Только с заказами", callback_data="broadcast_with_orders")
    )
    return kb

# ================= TELEGRAM =================
@bot.message_handler(commands=["start"])
def start(m):
    url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webapp"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 Открыть магазин", web_app=WebAppInfo(url=url)))
    bot.send_message(m.chat.id, "Приветствуем вас в Botopia\n\n👇 Нажми на кнопку ниже, чтобы открыть магазин", reply_markup=kb)

@bot.message_handler(commands=["admin"])
def admin(m):
    if m.from_user.id != ADMIN_ID:
        return bot.send_message(m.chat.id, "⛔ Нет доступа")
    bot.send_message(m.chat.id, "🔐 Админ-панель", reply_markup=admin_main_keyboard())

# Админ обработчики (полный набор)
@bot.callback_query_handler(func=lambda call: call.data == "admin_all")
def admin_all(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    orders = get_all_orders()
    show_orders(call.message, orders, "Все заказы")

@bot.callback_query_handler(func=lambda call: call.data == "admin_pending")
def admin_pending(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    orders = get_all_orders(status="pending")
    show_orders(call.message, orders, "В ожидании")

@bot.callback_query_handler(func=lambda call: call.data == "admin_completed")
def admin_completed(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    orders = get_all_orders(status="completed")
    show_orders(call.message, orders, "Выполненные")

@bot.callback_query_handler(func=lambda call: call.data == "admin_rejected")
def admin_rejected(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    orders = get_all_orders(status="rejected")
    show_orders(call.message, orders, "Отклонённые")

def show_orders(message, orders, title):
    if not orders:
        bot.send_message(message.chat.id, f"📭 {title}: нет заказов", reply_markup=admin_main_keyboard())
        return
    text = f"📋 {title} ({len(orders)})\n\n"
    for o in orders[:10]:
        icon = LEVELS[o["level"]]["icon"]
        status = "⏳" if o["status"] == "pending" else "✅" if o["status"] == "completed" else "❌"
        payment_ok = "💳" if o["is_payment_confirmed"] else "🚫"
        text += f"{status} #{o['id']} {icon} {o['price']}₽ {payment_ok}\n└ 👤 @{o['username'] or o['user_id']} | {o['created_at'][:16]}\n\n"
    kb = InlineKeyboardMarkup()
    for o in orders[:5]:
        kb.add(InlineKeyboardButton(f"📦 Заказ #{o['id']}", callback_data=f"view_{o['id']}"))
    kb.add(InlineKeyboardButton("◀ Назад", callback_data="admin_back"))
    bot.send_message(message.chat.id, text, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_"))
def view_order(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    order_id = int(call.data.split("_")[1])
    order = get_order(order_id)
    if not order:
        bot.send_message(call.message.chat.id, "❌ Заказ не найден")
        return
    icon = LEVELS[order["level"]]["icon"]
    status_text = "На проверке" if order["status"] == "pending" else "Выполнен" if order["status"] == "completed" else "Отклонён"
    payment_status = "✅ Оплата подтверждена" if order["is_payment_confirmed"] else "❌ Оплата НЕ подтверждена"
    text = f"📦 Заказ #{order_id}\n\n{icon} {order['level']} — {order['price']}₽\n👤 @{order['username'] or order['user_id']}\n📅 {order['created_at'][:16]}\n📌 {status_text}\n💳 {payment_status}"
    if order["validation_status"]:
        text += f"\n\n🔍 ПРОВЕРКА:\n{order['validation_status']}"
    if order["screenshot"]:
        try:
            img_data = order["screenshot"]
            if "," in img_data:
                img_data = img_data.split(",")[1]
            img = base64.b64decode(img_data)
            bot.send_photo(call.message.chat.id, BytesIO(img), caption=text, reply_markup=order_action_keyboard(order_id, order["status"]))
        except:
            bot.send_message(call.message.chat.id, text + "\n⚠️ Ошибка скриншота", reply_markup=order_action_keyboard(order_id, order["status"]))
    else:
        bot.send_message(call.message.chat.id, text, reply_markup=order_action_keyboard(order_id, order["status"]))

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
def confirm_order(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    order_id = int(call.data.split("_")[1])
    order = get_order(order_id)
    if order and order["status"] == "pending":
        update_order_status(order_id, "completed")
        icon = LEVELS[order["level"]]["icon"]
        level_name = LEVELS[order["level"]]["name"]
        bot.send_message(order["user_id"], f"✅ Заказ #{order_id} подтверждён!\n\n{icon} {level_name} — {order['price']}₽\n\nСпасибо за покупку!")
        bot.answer_callback_query(call.id, "Заказ подтверждён")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("◀ Назад", callback_data="admin_back")))
    else:
        bot.answer_callback_query(call.id, "Заказ уже обработан", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith("reject_"))
def reject_order(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    order_id = int(call.data.split("_")[1])
    order = get_order(order_id)
    if order and order["status"] == "pending":
        update_order_status(order_id, "rejected")
        bot.send_message(order["user_id"], f"❌ Заказ #{order_id} отклонён\n\nПожалуйста, оформите заказ заново.")
        bot.answer_callback_query(call.id, "Заказ отклонён")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("◀ Назад", callback_data="admin_back")))
    else:
        bot.answer_callback_query(call.id, "Заказ уже обработан", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def admin_stats(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    stats = get_stats()
    text = f"📊 СТАТИСТИКА\n\n📦 Всего: {stats['total']}\n⏳ В ожидании: {stats['pending']}\n✅ Выполнено: {stats['completed']}\n❌ Отклонено: {stats['rejected']}\n💰 Выручка: {stats['revenue']}₽\n⭐ Отзывов: {stats['reviews']}\n🚫 Отклонено оплат: {stats['rejected_payment']}"
    bot.send_message(call.message.chat.id, text, reply_markup=admin_main_keyboard())

@bot.callback_query_handler(func=lambda call: call.data == "admin_reviews")
def admin_reviews(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    reviews = get_reviews()
    if not reviews:
        bot.send_message(call.message.chat.id, "⭐ Нет отзывов", reply_markup=admin_main_keyboard())
    else:
        text = "⭐ ОТЗЫВЫ\n\n"
        for r in reviews[:15]:
            text += f"👤 @{r['username']}\n💬 {r['text'][:80]}\n📅 {r['created_at'][:10]}\n\n"
        bot.send_message(call.message.chat.id, text, reply_markup=admin_main_keyboard())

@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
def admin_broadcast(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    bot.send_message(call.message.chat.id, "📢 Выберите аудиторию:", reply_markup=broadcast_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith("broadcast_"))
def broadcast_target(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    target = call.data.split("_")[1]
    msg = bot.send_message(call.message.chat.id, "📝 Введите текст рассылки:")
    bot.register_next_step_handler(msg, send_broadcast, target, call.message.chat.id)

def send_broadcast(message, target, admin_chat_id):
    if message.from_user.id != ADMIN_ID:
        return
    text = message.text
    conn = get_db()
    if target == "all":
        users = conn.execute("SELECT DISTINCT user_id FROM orders").fetchall()
        count = 0
        for user in users:
            try:
                bot.send_message(user["user_id"], text)
                count += 1
            except:
                pass
        bot.send_message(admin_chat_id, f"✅ Рассылка отправлена {count} пользователям")
    else:
        users = conn.execute("SELECT DISTINCT user_id FROM orders WHERE status='completed'").fetchall()
        count = 0
        for user in users:
            try:
                bot.send_message(user["user_id"], text)
                count += 1
            except:
                pass
        bot.send_message(admin_chat_id, f"✅ Рассылка отправлена {count} покупателям")
    conn.close()

@bot.callback_query_handler(func=lambda call: call.data == "admin_back")
def admin_back(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    bot.edit_message_text("🔐 Админ-панель", call.message.chat.id, call.message.message_id, reply_markup=admin_main_keyboard())

# ================= FLASK =================
@app.route("/")
def home():
    return "BotShop running"

@app.route("/webapp")
def webapp():
    return HTML

@app.route("/create_order", methods=["POST"])
def create_order():
    try:
        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id")
        username = data.get("username", "")
        level = data.get("level")
        screenshot = data.get("screenshot")

        if not user_id or not level:
            return jsonify({"ok": False, "error": "Не хватает данных"})
        if level not in LEVELS:
            return jsonify({"ok": False, "error": "Неверный уровень"})

        rate_ok, rate_msg = check_rate_limit(user_id)
        if not rate_ok:
            return jsonify({"ok": False, "error": rate_msg})

        price = LEVELS[level]["price"]
        if not screenshot:
            return jsonify({"ok": False, "error": "Прикрепите скриншот оплаты"})

        if "," in screenshot:
            img_data = screenshot.split(",")[1]
        else:
            img_data = screenshot
        if len(img_data) > MAX_SCREENSHOT_SIZE * 1.5:
            return jsonify({"ok": False, "error": "Скриншот слишком большой (макс 5MB)"})

        try:
            image_bytes = base64.b64decode(img_data)
        except:
            return jsonify({"ok": False, "error": "Не удалось декодировать изображение"})
        if len(image_bytes) > MAX_SCREENSHOT_SIZE:
            return jsonify({"ok": False, "error": "Скриншот слишком большой (макс 5MB)"})

        screenshot_hash = hashlib.md5(image_bytes).hexdigest()

        is_payment, payment_confidence, payment_reasons = check_payment_screenshot(image_bytes)

        if not is_payment:
            validation_status = f"❌ НЕ ОПЛАТА! {payment_reasons} (уверенность {payment_confidence}%)"
            if ADMIN_ID != 0:
                bot.send_message(ADMIN_ID, f"⚠️ ПОПЫТКА ОБМАНА!\n\n👤 @{username or user_id}\n📦 {level} — {price}₽\n🔍 {validation_status}")
            return jsonify({"ok": False, "error": "❌ Загруженное изображение не похоже на чек оплаты. Пожалуйста, загрузите скриншот из приложения банка с суммой и датой."})

        ocr_text = simple_ocr(image_bytes)
        ocr_amount = extract_amount(ocr_text)
        if ocr_amount and abs(ocr_amount - price) <= 2:
            validation_status = f"✅ Оплата подтверждена! Сумма {ocr_amount}₽ совпадает. {payment_reasons}"
        else:
            validation_status = f"✅ Похоже на оплату, но сумма не распознана. Признаки: {payment_reasons}"

        order_id = add_order(user_id, username, level, screenshot, screenshot_hash,
                             ocr_text, ocr_amount, is_payment, payment_confidence,
                             payment_reasons, validation_status)

        icon = LEVELS[level]["icon"]
        if ADMIN_ID != 0:
            bot.send_message(ADMIN_ID, f"🆕 НОВЫЙ ЗАКАЗ!\n\n📦 #{order_id}\n👤 @{username or user_id}\n{icon} {level} — {price}₽\n\n🔍 {validation_status}")
        bot.send_message(user_id, f"✅ Заказ #{order_id} создан!\n\n{validation_status}\n\nАдминистратор проверит и подтвердит заказ.")
        return jsonify({"ok": True, "order_id": order_id})
    except Exception as e:
        print(f"Create order error: {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/orders")
def orders():
    uid = request.args.get("user_id", type=int)
    rows = get_orders(uid) if uid else []
    return jsonify([{"id": r["id"], "level": r["level"], "price": r["price"],
                     "status": r["status"], "created_at": r["created_at"]} for r in rows])

@app.route("/reviews")
def reviews():
    rows = get_reviews()
    return jsonify([{"username": r["username"], "text": r["text"], "created_at": r["created_at"]} for r in rows])

@app.route("/review", methods=["POST"])
def review():
    d = request.get_json(silent=True) or {}
    add_review(d.get("user_id"), d.get("username"), d.get("text", ""))
    return jsonify({"ok": True})

# ================= HTML (полный, без заглушек) =================
HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,300;14..32,400;14..32,500;14..32,600;14..32,700;14..32,800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:radial-gradient(ellipse at 30% 20%,#0a0a2a,#050510);color:#fff;min-height:100vh;padding:16px}
.glow-bg{position:fixed;top:0;left:0;right:0;bottom:0;overflow:hidden;z-index:0}
.glow-1{position:absolute;top:-20%;left:-20%;width:80%;height:80%;background:radial-gradient(circle,rgba(168,85,247,0.3) 0%,transparent 70%);filter:blur(60px);animation:float 20s ease-in-out infinite}
.glow-2{position:absolute;bottom:-20%;right:-20%;width:80%;height:80%;background:radial-gradient(circle,rgba(59,130,246,0.2) 0%,transparent 70%);filter:blur(60px);animation:float 20s ease-in-out infinite reverse}
@keyframes float{0%,100%{transform:translate(0,0) rotate(0deg)}50%{transform:translate(30px,20px) rotate(5deg)}}
.container{max-width:500px;margin:0 auto;position:relative;z-index:1}
.nav{display:flex;gap:8px;margin-bottom:24px;background:rgba(255,255,255,0.05);backdrop-filter:blur(20px);border-radius:100px;padding:6px;border:1px solid rgba(255,255,255,0.1)}
.nav-btn{flex:1;padding:12px;border:none;border-radius:100px;background:transparent;color:rgba(255,255,255,0.6);font-weight:600;font-size:14px;cursor:pointer;transition:all 0.3s ease}
.nav-btn.active{background:linear-gradient(135deg,#a855f7,#7c3aed);color:#fff;box-shadow:0 4px 15px rgba(168,85,247,0.3)}
.card{background:rgba(20,20,40,0.6);backdrop-filter:blur(20px);border-radius:32px;padding:24px;margin-bottom:16px;border:1px solid rgba(255,255,255,0.1);transition:all 0.3s ease;cursor:pointer}
.card.selected{border:2px solid #a855f7;background:rgba(168,85,247,0.15);transform:scale(1.01)}
.card:hover{transform:translateY(-2px);border-color:rgba(168,85,247,0.5)}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.card-title{font-size:22px;font-weight:700;display:flex;align-items:center;gap:8px}
.card-price{font-size:28px;font-weight:800;background:linear-gradient(135deg,#c084fc,#a855f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.features{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.feature-tag{background:rgba(168,85,247,0.2);border-radius:20px;padding:4px 12px;font-size:11px;color:#c084fc}
.btn{width:100%;padding:16px;border:none;border-radius:60px;font-size:16px;font-weight:600;cursor:pointer;transition:all 0.3s ease}
.btn:active{transform:scale(0.97)}
.btn-primary{background:linear-gradient(135deg,#a855f7,#7c3aed);color:#fff;box-shadow:0 4px 20px rgba(168,85,247,0.3)}
.btn-success{background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;box-shadow:0 4px 20px rgba(34,197,94,0.2)}
.btn-secondary{background:rgba(255,255,255,0.1);color:#fff;border:1px solid rgba(255,255,255,0.1)}
.btn:disabled{opacity:0.5;cursor:not-allowed;transform:none}
.payment-container{background:rgba(20,20,40,0.7);backdrop-filter:blur(20px);border-radius:40px;padding:28px;border:1px solid rgba(255,255,255,0.1);position:relative;overflow:hidden;animation:fadeInUp 0.5s ease}
.payment-container::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;background:linear-gradient(90deg,#a855f7,#22c55e,#a855f7);background-size:200% 100%;animation:gradient 3s ease infinite}
@keyframes gradient{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
@keyframes fadeInUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
.payment-header{text-align:center;margin-bottom:28px}
.payment-header h2{font-size:24px;font-weight:700;margin-bottom:8px}
.payment-price{font-size:56px;font-weight:800;background:linear-gradient(135deg,#c084fc,#a855f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin:12px 0}
.user-badge{display:inline-flex;align-items:center;gap:8px;background:rgba(168,85,247,0.15);border-radius:100px;padding:8px 20px;margin-top:8px;backdrop-filter:blur(10px);border:1px solid rgba(168,85,247,0.3)}
.user-badge span{font-weight:600;font-size:14px}
.requisites{background:rgba(0,0,0,0.3);border-radius:24px;padding:20px;margin:24px 0}
.requisite-row{display:flex;justify-content:space-between;align-items:center;padding:14px 0;border-bottom:1px solid rgba(255,255,255,0.08)}
.requisite-row:last-child{border-bottom:none}
.requisite-label{color:rgba(255,255,255,0.6);font-size:14px}
.requisite-value{font-weight:600;font-size:15px}
.card-number{background:linear-gradient(135deg,#0a0a1a,#0d0d1a);padding:16px;border-radius:20px;text-align:center;font-family:monospace;font-size:22px;letter-spacing:4px;font-weight:600;margin:16px 0;cursor:pointer;transition:all 0.2s;border:1px solid rgba(168,85,247,0.3)}
.card-number:active{background:linear-gradient(135deg,#1a1a2a,#1a1a2a);transform:scale(0.98)}
.copy-hint{text-align:center;font-size:11px;color:rgba(168,85,247,0.6);margin-top:-8px;margin-bottom:16px}
.upload-area{border:2px dashed rgba(168,85,247,0.4);border-radius:24px;padding:24px;text-align:center;margin:20px 0;cursor:pointer;transition:all 0.3s;background:rgba(168,85,247,0.03)}
.upload-area:hover{border-color:#a855f7;background:rgba(168,85,247,0.08)}
.upload-icon{font-size:40px;margin-bottom:12px}
.upload-text{font-size:14px;color:rgba(255,255,255,0.7)}
.upload-hint{font-size:11px;color:rgba(255,255,255,0.4);margin-top:8px}
.preview{margin-top:16px;border-radius:16px;overflow:hidden}
.preview img{max-width:100%;border-radius:16px;border:2px solid #a855f7}
.action-buttons{margin-top:24px;display:flex;flex-direction:column;gap:12px}
.profile-header{text-align:center;margin-bottom:20px}
.avatar{width:80px;height:80px;background:linear-gradient(135deg,#a855f7,#7c3aed);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:40px;margin:0 auto 16px;box-shadow:0 8px 25px rgba(168,85,247,0.3)}
.order-list{background:rgba(0,0,0,0.2);border-radius:20px;padding:16px;max-height:400px;overflow-y:auto}
.order-item{display:flex;justify-content:space-between;align-items:center;padding:12px;border-bottom:1px solid rgba(255,255,255,0.05)}
.status-badge{padding:4px 12px;border-radius:20px;font-size:11px;font-weight:600}
.status-completed{background:rgba(34,197,94,0.2);color:#22c55e}
.status-pending{background:rgba(245,158,11,0.2);color:#f59e0b}
.status-rejected{background:rgba(239,68,68,0.2);color:#ef4444}
.review-card{background:rgba(255,255,255,0.05);border-radius:20px;padding:16px;margin-bottom:12px}
.review-author{display:flex;align-items:center;gap:8px;margin-bottom:8px;font-weight:600}
.review-text{font-size:13px;color:rgba(255,255,255,0.8);line-height:1.4}
.review-date{font-size:10px;color:rgba(255,255,255,0.4);margin-top:8px}
.modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.95);backdrop-filter:blur(10px);justify-content:center;align-items:center;z-index:1000}
.modal-content{background:linear-gradient(135deg,#1a1a2e,#16213e);border-radius:32px;padding:28px;max-width:380px;width:90%;border:1px solid rgba(168,85,247,0.3);animation:fadeInUp 0.3s ease}
.modal-content h3{text-align:center;margin-bottom:20px;font-size:22px}
.modal-content textarea{width:100%;padding:14px;border-radius:16px;background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.1);color:#fff;font-family:inherit;font-size:14px;resize:vertical;margin:15px 0}
.modal-content textarea:focus{outline:none;border-color:#a855f7}
.loading{text-align:center;padding:40px}
.spinner{width:40px;height:40px;border:3px solid rgba(168,85,247,0.3);border-top-color:#a855f7;border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto 15px}
@keyframes spin{to{transform:rotate(360deg)}}
.empty{text-align:center;padding:40px;color:rgba(255,255,255,0.5)}
</style>
</head>
<body>
<div class="glow-bg"><div class="glow-1"></div><div class="glow-2"></div></div>
<div class="container">
    <div class="nav"><button class="nav-btn" id="btn-shop" onclick="switchTab('shop')">🛒 Магазин</button><button class="nav-btn" id="btn-profile" onclick="switchTab('profile')">👤 Профиль</button><button class="nav-btn" id="btn-reviews" onclick="switchTab('reviews')">⭐ Отзывы</button></div>
    <div id="shop-view"></div><div id="profile-view" style="display:none"></div><div id="reviews-view" style="display:none"></div>
</div>
<div id="reviewModal" class="modal"><div class="modal-content"><h3>✍️ Оставить отзыв</h3><textarea id="reviewText" rows="4" placeholder="Поделитесь впечатлениями о боте..."></textarea><button class="btn btn-success" onclick="submitReview()">📤 Отправить отзыв</button><button class="btn btn-secondary" style="margin-top:10px" onclick="closeReviewModal()">Отмена</button></div></div>
<script>
const tg=window.Telegram.WebApp;tg.ready();tg.expand();
let tgUser=tg.initDataUnsafe?.user,selectedLevel=null,currentStep='select',currentTab='shop',selectedScreenshot=null;
const LEVELS={easy:{name:"Лёгкий",icon:"🟢",price:99,features:["Защита от спама","Автоответы","Приветствие","Кнопка меню","Логи 100 сообщ"]},medium:{name:"Средний",icon:"🟡",price:249,features:["Всё из Лёгкого","Рассылка","Админ-панель","Чёрный список","Статистика"]},hard:{name:"Сложный",icon:"🔴",price:499,features:["Всё из Среднего","Авторассылка","Google Sheets","Скидки","Аналитика","1000+ юзеров"]}};
function switchTab(t){currentTab=t;document.getElementById('shop-view').style.display=t==='shop'?'block':'none';document.getElementById('profile-view').style.display=t==='profile'?'block':'none';document.getElementById('reviews-view').style.display=t==='reviews'?'block':'none';document.getElementById('btn-shop').classList.toggle('active',t==='shop');document.getElementById('btn-profile').classList.toggle('active',t==='profile');document.getElementById('btn-reviews').classList.toggle('active',t==='reviews');if(t==='shop')renderShop();else if(t==='profile')loadProfile();else loadReviews();}
function renderShop(){if(currentStep==='payment'){renderPayment();return}let html='';for(let[k,l]of Object.entries(LEVELS)){html+=`<div class="card ${selectedLevel===k?'selected':''}" onclick="selectLevel('${k}')"><div class="card-header"><div class="card-title">${l.icon} ${l.name}</div><div class="card-price">${l.price}₽</div></div><div class="features">${l.features.map(f=>`<span class="feature-tag">${f}</span>`).join('')}</div></div>`}html+=`<button class="btn btn-primary" ${!selectedLevel?'disabled':''} onclick="showPayment()">💎 Заказать бота</button>`;document.getElementById('shop-view').innerHTML=html}
function selectLevel(l){selectedLevel=l;renderShop();tg.HapticFeedback?.impactOccurred('light')}
function showPayment(){if(!tgUser?.id){tg.showAlert('❌ Откройте магазин через бота');return}if(!selectedLevel){tg.showAlert('❌ Сначала выберите уровень бота');return}currentStep='payment';renderPayment()}
function renderPayment(){const l=LEVELS[selectedLevel];const displayName=tgUser?.username?`@${tgUser.username}`:tgUser?.first_name||'Пользователь';document.getElementById('shop-view').innerHTML=`<div class="payment-container"><div class="payment-header"><h2>💳 Оплата заказа</h2><div class="payment-price">${l.price} ₽</div><div class="user-badge"><span>👤</span><span>${displayName}</span></div></div><div class="requisites"><div class="requisite-row"><span class="requisite-label">🏦 Банк</span><span class="requisite-value">Т-Банк</span></div><div class="card-number" onclick="copyCard()">2200 7021 5075 4195</div><div class="copy-hint">📋 Нажмите на номер карты, чтобы скопировать</div><div class="requisite-row"><span class="requisite-label">👤 Получатель</span><span class="requisite-value">Мухаммад А</span></div></div><div class="upload-area" onclick="document.getElementById('screenshotInput').click()"><div class="upload-icon">📸</div><div class="upload-text" id="uploadText">Прикрепить скриншот оплаты</div><div class="upload-hint">PNG, JPG до 5 МБ</div><input type="file" id="screenshotInput" accept="image/*" style="display:none" onchange="handleScreenshot(event)"><div id="preview" class="preview" style="display:none"></div></div><div class="action-buttons"><button class="btn btn-success" id="confirmBtn" onclick="confirmOrder()" disabled>✅ Я оплатил(а)</button><button class="btn btn-secondary" onclick="goBack()">◀ Назад в магазин</button></div></div>`}
function handleScreenshot(e){const f=e.target.files[0];if(f&&f.type.startsWith('image/')){const r=new FileReader();r.onload=function(e){selectedScreenshot=e.target.result;document.getElementById('uploadText').innerHTML='✅ Скриншот выбран';document.getElementById('preview').innerHTML=`<img src="${selectedScreenshot}" style="max-width:100%; border-radius:12px">`;document.getElementById('preview').style.display='block';document.getElementById('confirmBtn').disabled=false};r.readAsDataURL(f)}}
function copyCard(){navigator.clipboard.writeText("2200702150754195");tg.showAlert("✅ Номер карты скопирован");tg.HapticFeedback?.notificationOccurred('success')}
async function confirmOrder(){const btn=document.getElementById('confirmBtn');btn.disabled=true;btn.textContent='⏳ Отправка...';try{const res=await fetch('/create_order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:tgUser.id,username:tgUser.username||tgUser.first_name||'user',level:selectedLevel,screenshot:selectedScreenshot})});const data=await res.json();if(data.ok){tg.showAlert('✅ Заказ создан! Администратор проверит оплату');tg.HapticFeedback?.notificationOccurred('success');currentStep='select';selectedScreenshot=null;renderShop();if(currentTab==='profile')loadProfile()}else{tg.showAlert('❌ Ошибка при создании заказа');btn.disabled=false;btn.textContent='✅ Я оплатил(а)'}}catch(e){tg.showAlert('❌ Ошибка соединения');btn.disabled=false;btn.textContent='✅ Я оплатил(а)'}}
function goBack(){currentStep='select';renderShop()}
async function loadProfile(){if(!tgUser?.id){document.getElementById('profile-view').innerHTML='<div class="empty">❌ Ошибка авторизации</div>';return}document.getElementById('profile-view').innerHTML='<div class="loading"><div class="spinner"></div>Загрузка...</div>';try{const res=await fetch(`/orders?user_id=${tgUser.id}`);const orders=await res.json();let hasCompleted=false;let ordersHtml='';if(orders.length){ordersHtml=orders.map(o=>{if(o.status==='completed')hasCompleted=true;const icon=o.level==='easy'?'🟢':o.level==='medium'?'🟡':'🔴';const name=o.level==='easy'?'Лёгкий':o.level==='medium'?'Средний':'Сложный';const statusClass=`status-${o.status}`;const statusText=o.status==='completed'?'Выполнен':o.status==='pending'?'На проверке':'Отклонён';return`<div class="order-item"><div><span>${icon} ${name}</span><div style="font-size:12px;color:#888">${o.price}₽ • ${o.created_at?.slice(0,16)}</div></div><span class="status-badge ${statusClass}">${statusText}</span></div>`}).join('')}else{ordersHtml='<div class="empty">📭 У вас пока нет заказов</div>'}document.getElementById('profile-view').innerHTML=`<div class="card"><div class="profile-header"><div class="avatar">👤</div><h3>${tgUser.first_name||'Пользователь'}</h3><div style="font-size:12px;color:#888">ID: ${tgUser.id}</div></div><div class="order-list"><h4 style="margin-bottom:15px">📋 История заказов</h4>${ordersHtml}</div>${hasCompleted?'<button class="btn btn-primary" style="margin-top:20px" onclick="openReviewModal()">✍️ Оставить отзыв</button>':''}</div>`}catch(e){document.getElementById('profile-view').innerHTML='<div class="empty">❌ Ошибка загрузки</div>'}}
async function loadReviews(){document.getElementById('reviews-view').innerHTML='<div class="loading"><div class="spinner"></div>Загрузка...</div>';try{const res=await fetch('/reviews');const reviews=await res.json();if(reviews.length){document.getElementById('reviews-view').innerHTML=`<div class="card"><h3 style="margin-bottom:20px">⭐ Отзывы покупателей</h3>${reviews.map(r=>`<div class="review-card"><div class="review-author">👤 ${r.username}</div><div class="review-text">${r.text}</div><div class="review-date">${r.created_at?.slice(0,10)}</div></div>`).join('')}</div>`}else{document.getElementById('reviews-view').innerHTML='<div class="card empty">⭐ Пока нет отзывов. Будьте первым!</div>'}}catch(e){document.getElementById('reviews-view').innerHTML='<div class="empty">❌ Ошибка загрузки</div>'}}
function openReviewModal(){if(!selectedLevel){tg.showAlert('❌ Сначала выберите уровень бота в магазине');return}document.getElementById('reviewModal').style.display='flex';document.getElementById('reviewText').value=''}
function closeReviewModal(){document.getElementById('reviewModal').style.display='none'}
async function submitReview(){const text=document.getElementById('reviewText').value.trim();if(!text){tg.showAlert('❌ Напишите текст отзыва');return}const btn=document.querySelector('#reviewModal .btn-success');btn.disabled=true;btn.textContent='⏳ Отправка...';try{const res=await fetch('/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:tgUser.id,username:tgUser.username||tgUser.first_name||'user',text:text})});const data=await res.json();if(data.ok){tg.showAlert('✅ Спасибо за отзыв!');closeReviewModal();if(currentTab==='reviews')loadReviews()}else{tg.showAlert('❌ Ошибка')}}catch(e){tg.showAlert('❌ Ошибка')}btn.disabled=false;btn.textContent='📤 Отправить отзыв'}
renderShop();
</script>
</body>
</html>
"""

# ================= RUN =================
def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print(f"🤖 Бот запущен! Админ: {ADMIN_ID}")
    print("🔍 Проверка оплаты: только Pillow + pytesseract (без OpenCV)")
    bot.infinity_polling()
