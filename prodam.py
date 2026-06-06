#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import threading
import logging
from datetime import datetime
from flask import Flask, request, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")

ADMIN_ID = int(os.getenv("ADMIN_ID", "8276815852"))
PORT = int(os.getenv("PORT", "8080"))

CARD_NUMBER = "2200702150754195"
RECIPIENT_NAME = "Мухаммад А"
BANK_NAME = "Т-Банк"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN)
flask_app = Flask(__name__)

# ==================== БАЗА ДАННЫХ ====================
DB_NAME = "shop.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            complexity TEXT,
            price INTEGER,
            screenshot_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            complexity TEXT,
            review TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT,
            order_id INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def save_order(user_id, username, complexity, price):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO orders (user_id, username, complexity, price) VALUES (?,?,?,?)",
                (user_id, username, complexity, price))
    order_id = cur.lastrowid
    conn.commit()
    conn.close()
    return order_id

def update_order_screenshot(order_id, file_id):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("UPDATE orders SET screenshot_id = ? WHERE id = ?", (file_id, order_id))
    conn.commit()
    conn.close()

def update_order_status(order_id, status):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    return row

def get_user_orders(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
    conn.close()
    return rows

def get_all_orders():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    return rows

def get_pending_order_for_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM orders WHERE user_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    conn.close()
    return row

def save_review(user_id, username, complexity, review):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO reviews (user_id, username, complexity, review) VALUES (?,?,?,?)",
                (user_id, username, complexity, review))
    conn.commit()
    conn.close()

def get_reviews(limit=30):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows

def user_has_completed_order(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    row = cur.execute("SELECT 1 FROM orders WHERE user_id = ? AND status = 'completed' LIMIT 1", (user_id,)).fetchone()
    conn.close()
    return row is not None

def set_user_state(user_id, state, order_id=None):
    conn = sqlite3.connect(DB_NAME)
    if order_id:
        conn.execute("INSERT OR REPLACE INTO user_states (user_id, state, order_id) VALUES (?,?,?)",
                     (user_id, state, order_id))
    else:
        conn.execute("INSERT OR REPLACE INTO user_states (user_id, state) VALUES (?,?)",
                     (user_id, state))
    conn.commit()
    conn.close()

def get_user_state(user_id):
    conn = sqlite3.connect(DB_NAME)
    row = conn.execute("SELECT state, order_id FROM user_states WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row

def clear_user_state(user_id):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ==================== HTML MINI APP ====================
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>BotMarket</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: radial-gradient(ellipse at 20% 30%, #0a0a2a, #050510); min-height: 100vh; padding: 20px; color: #fff; }
        .container { max-width: 500px; margin: 0 auto; }
        .nav { display: flex; gap: 10px; margin-bottom: 30px; background: rgba(20,20,40,0.5); backdrop-filter: blur(20px); border-radius: 60px; padding: 6px; }
        .nav-btn { flex: 1; background: transparent; border: none; border-radius: 50px; padding: 12px; font-size: 14px; font-weight: 600; color: #8b8ca0; cursor: pointer; }
        .nav-btn.active { background: linear-gradient(135deg, #a855f7, #7c3aed); color: white; }
        .header { text-align: center; margin-bottom: 30px; }
        .logo { font-size: 48px; margin-bottom: 10px; }
        h1 { font-size: 28px; background: linear-gradient(135deg, #fff, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .level-card { background: rgba(20,20,40,0.6); backdrop-filter: blur(20px); border-radius: 28px; padding: 20px; margin-bottom: 16px; cursor: pointer; border: 1px solid rgba(255,255,255,0.1); }
        .level-card.selected { border: 2px solid #a855f7; background: rgba(168,85,247,0.15); }
        .level-header { display: flex; justify-content: space-between; align-items: center; }
        .level-name { font-size: 22px; font-weight: 700; }
        .level-price { font-size: 28px; font-weight: 800; color: #a855f7; }
        .features { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
        .feature-tag { background: rgba(255,255,255,0.05); border-radius: 20px; padding: 4px 10px; font-size: 11px; }
        .order-btn { width: 100%; background: linear-gradient(135deg, #a855f7, #7c3aed); border: none; border-radius: 60px; padding: 16px; font-size: 16px; font-weight: 600; color: white; margin-top: 20px; cursor: pointer; }
        .payment-panel { background: rgba(20,20,40,0.8); backdrop-filter: blur(20px); border-radius: 28px; padding: 24px; text-align: center; }
        .payment-price { font-size: 42px; font-weight: 800; color: #a855f7; margin: 15px 0; }
        .payment-details { background: rgba(255,255,255,0.05); border-radius: 20px; padding: 20px; margin: 20px 0; text-align: left; }
        .card-number { font-family: monospace; font-size: 18px; background: rgba(0,0,0,0.3); padding: 12px; border-radius: 12px; text-align: center; cursor: pointer; }
        .confirm-btn { width: 100%; background: #22c55e; border: none; border-radius: 60px; padding: 16px; color: white; font-weight: 600; margin-top: 15px; cursor: pointer; }
        .back-btn { width: 100%; background: rgba(255,255,255,0.1); border: none; border-radius: 60px; padding: 14px; color: white; margin-top: 10px; cursor: pointer; }
        .profile-card { background: rgba(20,20,40,0.6); backdrop-filter: blur(20px); border-radius: 28px; padding: 24px; text-align: center; }
        .order-item { display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .status-pending { color: #f59e0b; }
        .status-completed { color: #22c55e; }
        .review-item { background: rgba(255,255,255,0.05); border-radius: 20px; padding: 15px; margin-bottom: 12px; }
        .review-text { font-size: 13px; color: #ccc; margin-top: 8px; }
        .write-review-btn { background: linear-gradient(135deg, #a855f7, #7c3aed); border: none; border-radius: 60px; padding: 12px; color: white; width: 100%; margin-top: 15px; cursor: pointer; }
        .loading { text-align: center; padding: 40px; }
        .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.8); padding: 12px 20px; border-radius: 50px; z-index: 2000; animation: fadeOut 2s; }
        @keyframes fadeOut { 0% { opacity: 1; } 70% { opacity: 1; } 100% { opacity: 0; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="nav">
            <button class="nav-btn" id="nav-shop" onclick="switchTab('shop')">🛒 Магазин</button>
            <button class="nav-btn" id="nav-profile" onclick="switchTab('profile')">👤 Профиль</button>
            <button class="nav-btn" id="nav-reviews" onclick="switchTab('reviews')">⭐ Отзывы</button>
        </div>
        <div id="shop-view"></div>
        <div id="profile-view" style="display:none;"></div>
        <div id="reviews-view" style="display:none;"></div>
    </div>
    <div id="reviewModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.9); justify-content:center; align-items:center; z-index:1001;">
        <div style="background:#1a1a2e; border-radius:32px; padding:28px; max-width:380px; width:90%;">
            <h3 style="text-align:center;">✍️ Оставить отзыв</h3>
            <div id="reviewBotInfo" style="text-align:center; font-size:14px; color:#aaa; margin:10px 0;"></div>
            <textarea id="reviewText" rows="4" style="width:100%; background:rgba(255,255,255,0.1); border:none; border-radius:16px; padding:12px; color:white; margin:15px 0;"></textarea>
            <button class="confirm-btn" onclick="submitReview()">📤 Отправить</button>
            <button class="back-btn" onclick="closeReviewModal()">◀ Отмена</button>
        </div>
    </div>
    <script>
        const LEVELS = { easy: { name: "Лёгкий", icon: "🟢", price: 99, features: ["Базовая защита", "Быстрая установка"] }, medium: { name: "Средний", icon: "🟡", price: 249, features: ["Доп. модули", "Гибкая настройка"] }, hard: { name: "Сложный", icon: "🔴", price: 499, features: ["ML модель", "API доступ"] } };
        let selectedLevel = null, tgUser = null, currentStep = 'select', currentTab = 'shop';
        const tg = window.Telegram?.WebApp;
        if (tg) { tg.ready(); tg.expand(); tgUser = tg.initDataUnsafe?.user; }
        function switchTab(tab) {
            currentTab = tab;
            document.getElementById('shop-view').style.display = tab === 'shop' ? 'block' : 'none';
            document.getElementById('profile-view').style.display = tab === 'profile' ? 'block' : 'none';
            document.getElementById('reviews-view').style.display = tab === 'reviews' ? 'block' : 'none';
            document.querySelectorAll('.nav-btn').forEach((btn, i) => btn.classList.toggle('active', [tab === 'shop', tab === 'profile', tab === 'reviews'][i]));
            if (tab === 'shop') renderShop();
            else if (tab === 'profile') loadProfile();
            else loadReviews();
        }
        function renderShop() {
            if (currentStep === 'payment') { renderPayment(); return; }
            document.getElementById('shop-view').innerHTML = Object.entries(LEVELS).map(([k, l]) => `<div class="level-card ${selectedLevel===k?'selected':''}" onclick="selectLevel('${k}')"><div class="level-header"><div class="level-name">${l.icon} ${l.name}</div><div class="level-price">${l.price}₽</div></div><div class="features">${l.features.map(f=>`<span class="feature-tag">✓ ${f}</span>`).join('')}</div></div>`).join('') + `<button class="order-btn" ${!selectedLevel?'disabled':''} onclick="showPayment()">💎 Заказать</button>`;
        }
        function selectLevel(l) { selectedLevel = l; renderShop(); }
        function showPayment() { currentStep = 'payment'; renderPayment(); }
        function renderPayment() {
            const l = LEVELS[selectedLevel];
            document.getElementById('shop-view').innerHTML = `<div class="payment-panel"><div class="payment-price">${l.price}₽</div><div class="payment-details"><p>🏦 Банк: ${BANK_NAME}</p><div class="card-number" onclick="copyCard()">${CARD_NUMBER}</div><p>👤 Получатель: ${RECIPIENT_NAME}</p></div><button class="confirm-btn" onclick="confirmOrder()">✅ Я оплатил(а)</button><button class="back-btn" onclick="goBack()">◀ Назад</button></div>`;
        }
        function copyCard() { navigator.clipboard.writeText(CARD_NUMBER); tg?.showAlert('Карта скопирована!'); }
        async function confirmOrder() {
            const btn = document.querySelector('.confirm-btn');
            btn.disabled = true; btn.textContent = '⏳...';
            const res = await fetch('/create_order', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ complexity: selectedLevel, user_id: tgUser?.id||0, username: tgUser?.username||'' }) });
            const data = await res.json();
            if (data.success) { tg?.showAlert('✅ Заказ создан! Отправьте скриншот командой /pay'); currentStep = 'select'; renderShop(); }
            else tg?.showAlert('❌ Ошибка');
        }
        function goBack() { currentStep = 'select'; renderShop(); }
        async function loadProfile() {
            document.getElementById('profile-view').innerHTML = '<div class="loading">⏳ Загрузка...</div>';
            const res = await fetch(`/get_orders?user_id=${tgUser?.id||0}`);
            const data = await res.json();
            const hasCompleted = data.orders?.some(o => o.status === 'completed');
            document.getElementById('profile-view').innerHTML = `<div class="profile-card"><div class="logo">👤</div><h3>${tgUser?.first_name || 'Пользователь'}</h3><div class="order-history"><h3>📋 Заказы</h3>${data.orders?.length ? data.orders.map(o => `<div class="order-item"><span>${o.complexity==='easy'?'🟢':o.complexity==='medium'?'🟡':'🔴'} ${o.price}₽</span><span class="status-${o.status}">${o.status==='completed'?'✅ Выполнен':o.status==='pending'?'⏳ Ожидает':'❌ Отклонён'}</span></div>`).join('') : '<p>Нет заказов</p>'}</div>${hasCompleted ? '<button class="write-review-btn" onclick="openReviewModal()">✍️ Оставить отзыв</button>' : ''}</div>`;
        }
        async function loadReviews() {
            document.getElementById('reviews-view').innerHTML = '<div class="loading">⏳ Загрузка...</div>';
            const res = await fetch('/get_reviews');
            const data = await res.json();
            document.getElementById('reviews-view').innerHTML = `<h3>⭐ Отзывы</h3>${data.reviews?.length ? data.reviews.map(r => `<div class="review-item"><div>👤 ${r.username}</div><div class="review-text">${r.review}</div><div style="font-size:10px;color:#666;">${r.created_at?.slice(0,10)}</div></div>`).join('') : '<p>Пока нет отзывов</p>'}`;
        }
        function openReviewModal() {
            if (!selectedLevel) { tg?.showAlert('Сначала выберите бота'); return; }
            document.getElementById('reviewBotInfo').innerHTML = `${LEVELS[selectedLevel].icon} ${LEVELS[selectedLevel].name}`;
            document.getElementById('reviewModal').style.display = 'flex';
        }
        function closeReviewModal() { document.getElementById('reviewModal').style.display = 'none'; }
        async function submitReview() {
            const text = document.getElementById('reviewText').value.trim();
            if (!text) { tg?.showAlert('Напишите отзыв'); return; }
            const res = await fetch('/submit_review', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ user_id: tgUser?.id||0, username: tgUser?.username||tgUser?.first_name||'user', complexity: selectedLevel, review: text }) });
            const data = await res.json();
            if (data.success) { tg?.showAlert('Спасибо за отзыв!'); closeReviewModal(); if(currentTab==='reviews') loadReviews(); }
            else tg?.showAlert('Ошибка');
        }
        renderShop();
    </script>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
</body>
</html>
'''

# ==================== FLASK ENDPOINTS ====================
@flask_app.route('/')
def webapp():
    html = HTML_TEMPLATE.replace('${BANK_NAME}', BANK_NAME).replace('${CARD_NUMBER}', CARD_NUMBER).replace('${RECIPIENT_NAME}', RECIPIENT_NAME)
    return html

@flask_app.route('/create_order', methods=['POST'])
def create_order():
    try:
        data = request.get_json()
        prices = {"easy": 99, "medium": 249, "hard": 499}
        order_id = save_order(data.get('user_id', 0), data.get('username', ''), data.get('complexity'), prices.get(data.get('complexity'), 99))
        bot.send_message(ADMIN_ID, f"🆕 Новый заказ #{order_id}\n👤 {data.get('username')}\n💰 {prices.get(data.get('complexity'))}₽")
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@flask_app.route('/get_orders', methods=['GET'])
def get_orders():
    orders = get_user_orders(int(request.args.get('user_id', 0)))
    return jsonify({"orders": [{"id": o[0], "complexity": o[3], "price": o[4], "status": o[6]} for o in orders]})

@flask_app.route('/get_reviews', methods=['GET'])
def get_reviews_endpoint():
    return jsonify({"reviews": [{"id": r[0], "username": r[2], "review": r[4], "created_at": r[5]} for r in get_reviews()]})

@flask_app.route('/submit_review', methods=['POST'])
def submit_review():
    data = request.get_json()
    if not user_has_completed_order(data.get('user_id', 0)):
        return jsonify({"success": False, "error": "Нужен выполненный заказ"})
    save_review(data.get('user_id'), data.get('username'), data.get('complexity'), data.get('review'))
    bot.send_message(ADMIN_ID, f"⭐ Новый отзыв от @{data.get('username')}\n📝 {data.get('review')[:100]}")
    return jsonify({"success": True})

# ==================== TELEGRAM HANDLERS ====================
@bot.message_handler(commands=['start'])
def start(message):
    web_app_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 Открыть магазин", web_app=WebAppInfo(url=web_app_url)))
    kb.add(InlineKeyboardButton("📦 Мои заказы", callback_data="my_orders"))
    kb.add(InlineKeyboardButton("⭐ Отзывы", callback_data="show_reviews"))
    bot.send_message(message.chat.id, "🤖 Добро пожаловать в BotMarket!\n💰 Лёгкий 99₽, Средний 249₽, Сложный 499₽", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data == "my_orders")
def my_orders(call):
    orders = get_user_orders(call.from_user.id)
    if not orders:
        bot.send_message(call.message.chat.id, "📭 Нет заказов")
    else:
        text = "📦 Ваши заказы:\n\n" + "\n".join([f"{'✅' if o[6]=='completed' else '⏳'} #{o[0]} | {['🟢','🟡','🔴'][['easy','medium','hard'].index(o[3])]} | {o[4]}₽" for o in orders])
        bot.send_message(call.message.chat.id, text)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "show_reviews")
def show_reviews(call):
    reviews = get_reviews(5)
    if not reviews:
        bot.send_message(call.message.chat.id, "⭐ Пока нет отзывов")
    else:
        text = "⭐ Последние отзывы:\n\n" + "\n".join([f"👤 @{r[2]}\n💬 {r[4][:100]}\n📅 {r[5][:10]}\n" for r in reviews])
        bot.send_message(call.message.chat.id, text)
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['pay'])
def pay_command(message):
    pending = get_pending_order_for_user(message.from_user.id)
    if not pending:
        bot.send_message(message.chat.id, "❌ Нет заказов на оплату")
        return
    set_user_state(message.from_user.id, "waiting_screenshot", pending[0])
    bot.send_message(message.chat.id, f"💳 Оплата заказа #{pending[0]} — {pending[4]}₽\n\n🏦 {BANK_NAME}\n💳 {CARD_NUMBER}\n👤 {RECIPIENT_NAME}\n\n📸 Отправьте скриншот чека")

@bot.message_handler(content_types=['photo'])
def handle_screenshot(message):
    state = get_user_state(message.from_user.id)
    if not state or state[0] != "waiting_screenshot":
        bot.send_message(message.chat.id, "❌ У вас нет активной оплаты. Сначала /pay")
        return
    order_id = state[1]
    order = get_order(order_id)
    if not order or order[1] != message.from_user.id:
        bot.send_message(message.chat.id, "❌ Ошибка")
        clear_user_state(message.from_user.id)
        return
    file_id = message.photo[-1].file_id
    update_order_screenshot(order_id, file_id)
    clear_user_state(message.from_user.id)
    bot.send_message(message.chat.id, "✅ Скриншот получен! Администратор проверит оплату.")
    
    level_icon = "🟢" if order[3] == "easy" else "🟡" if order[3] == "medium" else "🔴"
    msg = bot.send_photo(ADMIN_ID, file_id, caption=f"📸 Чек для заказа #{order_id}\n👤 @{message.from_user.username or message.from_user.id}\n{level_icon} {order[3]} — {order[4]}₽", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{order_id}"), InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{order_id}")))
    try:
        bot.pin_chat_message(ADMIN_ID, msg.message_id)
    except:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
def confirm_order(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    order_id = int(call.data.split("_")[1])
    update_order_status(order_id, "completed")
    order = get_order(order_id)
    if order:
        bot.send_message(order[1], f"✅ Заказ #{order_id} подтверждён!\nСпасибо за покупку!")
    bot.edit_message_text(f"✅ Заказ #{order_id} подтверждён", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "Подтверждено")

@bot.callback_query_handler(func=lambda call: call.data.startswith("reject_"))
def reject_order(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    order_id = int(call.data.split("_")[1])
    update_order_status(order_id, "rejected")
    order = get_order(order_id)
    if order:
        bot.send_message(order[1], f"❌ Заказ #{order_id} отклонён")
    bot.edit_message_text(f"❌ Заказ #{order_id} отклонён", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "Отклонено")

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID:
        return
    orders = get_all_orders()
    if not orders:
        bot.send_message(message.chat.id, "📭 Нет заказов")
        return
    text = "📋 Все заказы:\n\n" + "\n".join([f"{'🟡' if o[6]=='pending' else '✅' if o[6]=='completed' else '❌'} #{o[0]} | @{o[2] or o[1]} | {o[4]}₽ | {o[7][:10]}" for o in orders])
    bot.send_message(message.chat.id, text)

# ==================== RUN ====================
def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot.infinity_polling()
