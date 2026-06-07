#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import base64
import threading
from io import BytesIO
from datetime import datetime

from flask import Flask, request, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")

ADMIN_ID = int(os.getenv("ADMIN_ID", "8276815852"))
PORT = int(os.getenv("PORT", "8080"))

# 💳 Т-БАНК РЕКВИЗИТЫ
CARD_NUMBER = "2200702150754195"
RECIPIENT = "Мухаммад А"
BANK = "Т-Банк"

PRICES = {
    "easy": 99,
    "medium": 249,
    "hard": 499
}

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ================== DB ==================
DB = "shop.db"

def init():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        level TEXT,
        price INTEGER,
        screenshot TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        level TEXT,
        text TEXT,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()

init()

# ================== DB FUNCS ==================
def save_order(user_id, username, level, screenshot):
    price = PRICES[level]
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
    INSERT INTO orders(user_id, username, level, price, screenshot, status, created_at)
    VALUES(?,?,?,?,?,'pending',?)
    """, (user_id, username, level, price, screenshot, datetime.now().isoformat()))
    conn.commit()
    oid = c.lastrowid
    conn.close()
    return oid

def get_user_orders(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    rows = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
    conn.close()
    return rows

def get_all_orders(status=None):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    if status:
        rows = c.execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC", (status,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    return rows

def update_order_status(order_id, status):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    conn.close()
    return row

def save_review(user_id, username, level, text):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT INTO reviews(user_id, username, level, text, created_at) VALUES(?,?,?,?,?)",
              (user_id, username, level, text, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_reviews(limit=50):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    rows = c.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows

def get_all_users():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    rows = c.execute("SELECT DISTINCT user_id, username FROM orders").fetchall()
    conn.close()
    return rows

def user_has_completed_order(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    row = c.execute("SELECT 1 FROM orders WHERE user_id=? AND status='completed' LIMIT 1", (user_id,)).fetchone()
    conn.close()
    return row is not None

def get_stats():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    total_orders = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    completed_orders = c.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0]
    total_revenue = c.execute("SELECT SUM(price) FROM orders WHERE status='completed'").fetchone()[0] or 0
    total_reviews = c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    pending_orders = c.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
    conn.close()
    return {
        "total_orders": total_orders,
        "completed_orders": completed_orders,
        "pending_orders": pending_orders,
        "total_revenue": total_revenue,
        "total_reviews": total_reviews
    }

# ================== АДМИН-КЛАВИАТУРЫ ==================
def admin_main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📦 Все заказы", callback_data="admin_orders_all"),
        InlineKeyboardButton("⏳ В ожидании", callback_data="admin_orders_pending"),
        InlineKeyboardButton("✅ Выполненные", callback_data="admin_orders_completed"),
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"),
        InlineKeyboardButton("⭐ Отзывы", callback_data="admin_reviews")
    )
    return kb

def order_action_keyboard(order_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"admin_confirm_{order_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"admin_reject_{order_id}")
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

# ================== WEB APP HTML ==================
HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BotShop</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:sans-serif;padding:15px;min-height:100vh}
.container{max-width:500px;margin:0 auto}
.nav{display:flex;gap:10px;margin-bottom:20px;background:rgba(255,255,255,0.1);border-radius:50px;padding:5px}
.nav-btn{flex:1;padding:12px;border:none;border-radius:50px;background:transparent;color:white;font-weight:bold}
.nav-btn.active{background:#7c3aed}
.card{background:rgba(255,255,255,0.1);backdrop-filter:blur(10px);padding:20px;border-radius:20px;margin:10px 0;cursor:pointer}
.card.selected{border:2px solid #7c3aed;background:rgba(124,58,237,0.2)}
.price{font-size:24px;color:#ffd700;margin-top:10px}
.btn{padding:14px;border-radius:50px;border:none;width:100%;margin-top:10px;font-weight:bold;cursor:pointer}
.btn-green{background:#22c55e;color:white}
.btn-purple{background:#7c3aed;color:white}
.btn-red{background:#ef4444;color:white}
.btn-gray{background:rgba(255,255,255,0.2);color:white}
input,textarea{width:100%;padding:12px;margin-top:10px;border-radius:16px;border:none;background:rgba(0,0,0,0.3);color:white}
.card-number{background:#000;padding:12px;border-radius:12px;text-align:center;font-family:monospace;margin:10px 0;cursor:pointer}
.order-item{display:flex;justify-content:space-between;padding:10px;border-bottom:1px solid rgba(255,255,255,0.1)}
.status-pending{color:#f59e0b}
.status-completed{color:#22c55e}
.status-rejected{color:#ef4444}
.review-item{background:rgba(255,255,255,0.05);border-radius:16px;padding:12px;margin-bottom:10px}
.loading{text-align:center;padding:40px}
.spinner{width:40px;height:40px;border:3px solid rgba(124,58,237,0.3);border-top-color:#7c3aed;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto}
@keyframes spin{to{transform:rotate(360deg)}}
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
    <div id="profile-view" style="display:none"></div>
    <div id="reviews-view" style="display:none"></div>
</div>

<div id="reviewModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.95); justify-content:center; align-items:center; z-index:1001;">
    <div style="background:#1a1a2e; border-radius:32px; padding:28px; max-width:380px; width:90%">
        <h3 style="text-align:center">✍️ Оставить отзыв</h3>
        <textarea id="reviewText" rows="4" style="width:100%; margin:15px 0" placeholder="Ваш отзыв..."></textarea>
        <button class="btn btn-green" onclick="submitReview()">📤 Отправить</button>
        <button class="btn btn-gray" onclick="closeReviewModal()">Отмена</button>
    </div>
</div>

<script>
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

let tgUser = tg.initDataUnsafe?.user;
let selectedLevel = null;
let currentStep = 'select';
let currentTab = 'shop';
let selectedScreenshot = null;

const LEVELS = {
    easy: { name: "Лёгкий", icon: "🟢", price: 99, desc: "Базовый функционал" },
    medium: { name: "Средний", icon: "🟡", price: 249, desc: "Расширенный функционал" },
    hard: { name: "Сложный", icon: "🔴", price: 499, desc: "Полный функционал" }
};

function switchTab(tab) {
    currentTab = tab;
    document.getElementById('shop-view').style.display = tab === 'shop' ? 'block' : 'none';
    document.getElementById('profile-view').style.display = tab === 'profile' ? 'block' : 'none';
    document.getElementById('reviews-view').style.display = tab === 'reviews' ? 'block' : 'none';
    document.querySelectorAll('.nav-btn').forEach((btn,i) => {
        btn.classList.toggle('active', (tab==='shop'&&i===0) || (tab==='profile'&&i===1) || (tab==='reviews'&&i===2));
    });
    if (tab === 'shop') renderShop();
    else if (tab === 'profile') loadProfile();
    else loadReviews();
}

function renderShop() {
    if (currentStep === 'payment') { renderPayment(); return; }
    let html = '';
    for (let [k,l] of Object.entries(LEVELS)) {
        html += `<div class="card ${selectedLevel===k?'selected':''}" onclick="selectLevel('${k}')">
            <h3>${l.icon} ${l.name}</h3>
            <div class="price">${l.price}₽</div>
            <small>${l.desc}</small>
        </div>`;
    }
    html += `<button class="btn btn-purple" ${!selectedLevel?'disabled':''} onclick="showPayment()">💎 Заказать</button>`;
    document.getElementById('shop-view').innerHTML = html;
}

function selectLevel(l) { selectedLevel = l; renderShop(); }

function showPayment() {
    if (!tgUser?.id) { tg.showAlert('❌ Откройте через бота'); return; }
    currentStep = 'payment';
    renderPayment();
}

function renderPayment() {
    const l = LEVELS[selectedLevel];
    const displayName = tgUser?.username ? `@${tgUser.username}` : tgUser?.first_name || 'Пользователь';
    document.getElementById('shop-view').innerHTML = `
        <div class="card">
            <h3>💳 Оплата</h3>
            <div class="price">${l.price}₽</div>
            <div style="background:rgba(124,58,237,0.2); padding:12px; border-radius:12px; margin:10px 0">
                👤 <b>${displayName}</b>
            </div>
            <div class="card-number" onclick="copyCard()">2200702150754195</div>
            <p>👤 Получатель: Мухаммад А</p>
            <p>🏦 Т-Банк</p>
            <input type="file" id="screenshotInput" accept="image/*" onchange="handleScreenshot(event)">
            <div id="preview"></div>
            <button class="btn btn-green" id="confirmBtn" onclick="confirmOrder()" disabled>✅ Я оплатил</button>
            <button class="btn btn-gray" onclick="goBack()">◀ Назад</button>
        </div>
    `;
}

function handleScreenshot(event) {
    const file = event.target.files[0];
    if (file && file.type.startsWith('image/')) {
        const reader = new FileReader();
        reader.onload = function(e) {
            selectedScreenshot = e.target.result;
            document.getElementById('preview').innerHTML = `<img src="${selectedScreenshot}" style="max-width:100%; border-radius:12px; margin-top:10px">`;
            document.getElementById('confirmBtn').disabled = false;
        };
        reader.readAsDataURL(file);
    }
}

function copyCard() { navigator.clipboard.writeText("2200702150754195"); tg.showAlert("✅ Карта скопирована"); }

async function confirmOrder() {
    const btn = document.getElementById('confirmBtn');
    btn.disabled = true;
    btn.textContent = '⏳ Отправка...';
    try {
        const res = await fetch('/create_order', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                user_id: tgUser.id,
                username: tgUser.username || tgUser.first_name || 'user',
                level: selectedLevel,
                screenshot: selectedScreenshot
            })
        });
        const data = await res.json();
        if (data.success) {
            tg.showAlert('✅ Заказ создан! Админ проверит оплату.');
            currentStep = 'select';
            selectedScreenshot = null;
            renderShop();
            if (currentTab === 'profile') loadProfile();
        } else {
            tg.showAlert('❌ ' + data.error);
            btn.disabled = false;
            btn.textContent = '✅ Я оплатил';
        }
    } catch(e) { tg.showAlert('❌ Ошибка'); btn.disabled = false; btn.textContent = '✅ Я оплатил'; }
}

function goBack() { currentStep = 'select'; renderShop(); }

async function loadProfile() {
    if (!tgUser?.id) { document.getElementById('profile-view').innerHTML = '<div class="loading">❌ Ошибка</div>'; return; }
    document.getElementById('profile-view').innerHTML = '<div class="loading"><div class="spinner"></div>Загрузка...</div>';
    try {
        const res = await fetch(`/get_orders?user_id=${tgUser.id}`);
        const data = await res.json();
        let hasCompleted = false;
        let ordersHtml = '';
        if (data.orders?.length) {
            ordersHtml = data.orders.map(o => {
                if (o.status === 'completed') hasCompleted = true;
                const levelIcon = o.level === 'easy' ? '🟢' : o.level === 'medium' ? '🟡' : '🔴';
                const levelName = o.level === 'easy' ? 'Лёгкий' : o.level === 'medium' ? 'Средний' : 'Сложный';
                const statusClass = o.status === 'completed' ? 'status-completed' : o.status === 'pending' ? 'status-pending' : 'status-rejected';
                const statusText = o.status === 'completed' ? '✅ Выполнен' : o.status === 'pending' ? '⏳ На проверке' : '❌ Отклонён';
                return `<div class="order-item"><span>${levelIcon} ${levelName} — ${o.price}₽</span><span class="${statusClass}">${statusText}</span></div>`;
            }).join('');
        } else {
            ordersHtml = '<div style="text-align:center; padding:20px">📭 Нет заказов</div>';
        }
        document.getElementById('profile-view').innerHTML = `
            <div class="card">
                <h3>👤 ${tgUser.first_name || 'Пользователь'}</h3>
                <div style="margin-top:15px">
                    <h4>📋 История заказов</h4>
                    ${ordersHtml}
                </div>
                ${hasCompleted ? '<button class="btn btn-purple" style="margin-top:15px" onclick="openReviewModal()">✍️ Оставить отзыв</button>' : ''}
            </div>
        `;
    } catch(e) { document.getElementById('profile-view').innerHTML = '<div class="loading">❌ Ошибка</div>'; }
}

async function loadReviews() {
    document.getElementById('reviews-view').innerHTML = '<div class="loading"><div class="spinner"></div>Загрузка...</div>';
    try {
        const res = await fetch('/get_reviews');
        const data = await res.json();
        if (data.reviews?.length) {
            document.getElementById('reviews-view').innerHTML = `<div class="card"><h3>⭐ Отзывы</h3>${data.reviews.map(r => `
                <div class="review-item">
                    <b>👤 ${r.username}</b> <span>${r.level === 'easy' ? '🟢' : r.level === 'medium' ? '🟡' : '🔴'}</span>
                    <div>${r.text}</div>
                    <small>${r.created_at?.slice(0,10)}</small>
                </div>
            `).join('')}</div>`;
        } else {
            document.getElementById('reviews-view').innerHTML = '<div class="card">⭐ Пока нет отзывов</div>';
        }
    } catch(e) { document.getElementById('reviews-view').innerHTML = '<div class="loading">❌ Ошибка</div>'; }
}

function openReviewModal() {
    if (!selectedLevel) { tg.showAlert('❌ Сначала выберите бота'); return; }
    document.getElementById('reviewModal').style.display = 'flex';
}

function closeReviewModal() { document.getElementById('reviewModal').style.display = 'none'; }

async function submitReview() {
    const text = document.getElementById('reviewText').value.trim();
    if (!text) { tg.showAlert('❌ Напишите отзыв'); return; }
    const btn = document.querySelector('#reviewModal .btn-green');
    btn.disabled = true;
    btn.textContent = '⏳...';
    try {
        const res = await fetch('/submit_review', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                user_id: tgUser.id,
                username: tgUser.username || tgUser.first_name || 'user',
                level: selectedLevel,
                text: text
            })
        });
        const data = await res.json();
        if (data.success) {
            tg.showAlert('✅ Спасибо за отзыв!');
            closeReviewModal();
            if (currentTab === 'reviews') loadReviews();
        } else { tg.showAlert('❌ ' + data.error); }
    } catch(e) { tg.showAlert('❌ Ошибка'); }
    btn.disabled = false;
    btn.textContent = '📤 Отправить';
}

renderShop();
</script>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
</body>
</html>
"""

# ================== FLASK ROUTES ==================
@app.route("/")
def index():
    return HTML

@app.route("/create_order", methods=["POST"])
def create_order():
    try:
        data = request.json
        user_id = data.get("user_id")
        username = data.get("username", "user")
        level = data.get("level")
        screenshot = data.get("screenshot")
        
        if not user_id:
            return jsonify({"success": False, "error": "Ошибка авторизации"})
        
        order_id = save_order(user_id, username, level, screenshot)
        
        level_icon = "🟢" if level == "easy" else "🟡" if level == "medium" else "🔴"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📦 Посмотреть заказ", callback_data=f"admin_view_{order_id}"))
        
        bot.send_message(
            ADMIN_ID,
            f"🆕 *НОВЫЙ ЗАКАЗ!*\n\n📦 Заказ #{order_id}\n👤 @{username if username != 'user' else user_id}\n{level_icon} {level.upper()} — {PRICES[level]}₽\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/get_orders")
def get_orders():
    try:
        user_id = int(request.args.get("user_id", 0))
        orders = get_user_orders(user_id)
        orders_list = [{"id": o[0], "level": o[3], "price": o[4], "status": o[6], "created_at": o[7]} for o in orders]
        return jsonify({"orders": orders_list})
    except Exception as e:
        return jsonify({"orders": [], "error": str(e)})

@app.route("/get_reviews")
def get_reviews_endpoint():
    try:
        reviews = get_reviews()
        reviews_list = [{"id": r[0], "username": r[2], "level": r[3], "text": r[4], "created_at": r[5]} for r in reviews]
        return jsonify({"reviews": reviews_list})
    except Exception as e:
        return jsonify({"reviews": [], "error": str(e)})

@app.route("/submit_review", methods=["POST"])
def submit_review_route():
    try:
        data = request.json
        user_id = data.get("user_id")
        username = data.get("username")
        level = data.get("level")
        text = data.get("text", "").strip()
        
        if not text:
            return jsonify({"success": False, "error": "Текст отзыва не может быть пустым"})
        
        if not user_has_completed_order(user_id):
            return jsonify({"success": False, "error": "Оставить отзыв можно только после выполненного заказа"})
        
        save_review(user_id, username, level, text)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ================== TELEGRAM BOT HANDLERS ==================
@bot.message_handler(commands=["start"])
def start(m):
    url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 Открыть магазин", web_app=WebAppInfo(url=url)))
    bot.send_message(m.chat.id, "🤖 *BotMarket* — магазин ботов\n\n👇 Нажмите на кнопку", parse_mode="Markdown", reply_markup=kb)

@bot.message_handler(commands=["id"])
def get_id(m):
    bot.reply_to(m, f"Ваш ID: {m.from_user.id}")

@bot.message_handler(commands=["admin"])
def admin_panel(m):
    if m.from_user.id != ADMIN_ID:
        bot.send_message(m.chat.id, "❌ Доступ запрещён")
        return
    bot.send_message(m.chat.id, "🔐 *Админ-панель*", parse_mode="Markdown", reply_markup=admin_main_keyboard())

# ================== АДМИН КОЛБЭКИ ==================
@bot.callback_query_handler(func=lambda call: call.data == "admin_orders_all")
def admin_orders_all(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    orders = get_all_orders()
    show_orders_list(call.message, orders, "Все заказы")

@bot.callback_query_handler(func=lambda call: call.data == "admin_orders_pending")
def admin_orders_pending(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    orders = get_all_orders(status="pending")
    show_orders_list(call.message, orders, "⏳ Заказы в ожидании")

@bot.callback_query_handler(func=lambda call: call.data == "admin_orders_completed")
def admin_orders_completed(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    orders = get_all_orders(status="completed")
    show_orders_list(call.message, orders, "✅ Выполненные заказы")

def show_orders_list(message, orders, title):
    if not orders:
        bot.send_message(message.chat.id, f"📭 {title}: нет заказов", reply_markup=admin_main_keyboard())
        return
    text = f"📋 *{title}* ({len(orders)})\n\n"
    for o in orders[:10]:
        level_icon = "🟢" if o[3] == "easy" else "🟡" if o[3] == "medium" else "🔴"
        status_emoji = "⏳" if o[6] == "pending" else "✅" if o[6] == "completed" else "❌"
        display_name = f"@{o[2]}" if o[2] and o[2] != 'user' else f"ID:{o[1]}"
        text += f"{status_emoji} #{o[0]} {level_icon} {o[4]}₽\n└ 👤 {display_name} | {o[7][:16]}\n\n"
    if len(orders) > 10:
        text += f"\n*Показаны первые 10 из {len(orders)}*"
    kb = InlineKeyboardMarkup()
    for o in orders[:5]:
        kb.add(InlineKeyboardButton(f"📦 Заказ #{o[0]}", callback_data=f"admin_view_{o[0]}"))
    kb.add(InlineKeyboardButton("◀ Назад", callback_data="admin_back"))
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_view_"))
def admin_view_order(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    order_id = int(call.data.split("_")[2])
    order = get_order(order_id)
    if not order:
        bot.send_message(call.message.chat.id, "❌ Заказ не найден")
        return
    level_icon = "🟢" if order[3] == "easy" else "🟡" if order[3] == "medium" else "🔴"
    status_text = "⏳ На проверке" if order[6] == "pending" else "✅ Выполнен" if order[6] == "completed" else "❌ Отклонён"
    display_name = f"@{order[2]}" if order[2] and order[2] != 'user' else f"ID:{order[1]}"
    text = f"📦 *Заказ #{order_id}*\n\n{level_icon} Уровень: {order[3].upper()}\n💰 Сумма: {order[4]}₽\n👤 {display_name}\n📅 Создан: {order[7][:16]}\n📌 Статус: {status_text}\n"
    if order[5]:
        try:
            base64_data = order[5]
            if ',' in base64_data:
                base64_data = base64_data.split(',')[1]
            image_bytes = base64.b64decode(base64_data)
            photo = BytesIO(image_bytes)
            bot.send_photo(call.message.chat.id, photo, caption=text, parse_mode="Markdown", reply_markup=order_action_keyboard(order_id))
        except:
            bot.send_message(call.message.chat.id, text + "\n⚠️ Не удалось загрузить скриншот", parse_mode="Markdown", reply_markup=order_action_keyboard(order_id))
    else:
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=order_action_keyboard(order_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_confirm_"))
def admin_confirm_order(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    order_id = int(call.data.split("_")[2])
    order = get_order(order_id)
    if order:
        update_order_status(order_id, "completed")
        level_icon = "🟢" if order[3] == "easy" else "🟡" if order[3] == "medium" else "🔴"
        level_name = "Лёгкий" if order[3] == "easy" else "Средний" if order[3] == "medium" else "Сложный"
        bot.send_message(order[1], f"✅ *Ваш заказ #{order_id} подтверждён!*\n\n{level_icon} {level_name} бот — {order[4]}₽\n\nСпасибо за покупку! Можете оставить отзыв в профиле.", parse_mode="Markdown")
        bot.answer_callback_query(call.id, "Заказ подтверждён")
        bot.edit_message_caption(f"✅ ЗАКАЗ #{order_id} ПОДТВЕРЖДЁН", call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "Ошибка")

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_reject_"))
def admin_reject_order(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    order_id = int(call.data.split("_")[2])
    order = get_order(order_id)
    if order:
        update_order_status(order_id, "rejected")
        bot.send_message(order[1], f"❌ *Ваш заказ #{order_id} отклонён*\n\nПожалуйста, оформите заказ заново.", parse_mode="Markdown")
        bot.answer_callback_query(call.id, "Заказ отклонён")
        bot.edit_message_caption(f"❌ ЗАКАЗ #{order_id} ОТКЛОНЁН", call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "Ошибка")

@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def admin_stats(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    stats = get_stats()
    text = f"📊 *СТАТИСТИКА*\n\n📦 Всего заказов: {stats['total_orders']}\n⏳ В ожидании: {stats['pending_orders']}\n✅ Выполнено: {stats['completed_orders']}\n💰 Выручка: {stats['total_revenue']}₽\n⭐ Отзывов: {stats['total_reviews']}"
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=admin_main_keyboard())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_reviews")
def admin_reviews(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    reviews = get_reviews(20)
    if not reviews:
        bot.send_message(call.message.chat.id, "⭐ Нет отзывов", reply_markup=admin_main_keyboard())
    else:
        text = "⭐ *ОТЗЫВЫ*\n\n"
        for r in reviews[:15]:
            level_icon = "🟢" if r[3] == "easy" else "🟡" if r[3] == "medium" else "🔴"
            text += f"{level_icon} {r[2]}\n💬 {r[4][:80]}\n📅 {r[5][:10]}\n\n"
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=admin_main_keyboard())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
def admin_broadcast(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    bot.send_message(call.message.chat.id, "📢 *Выберите аудиторию:*", parse_mode="Markdown", reply_markup=broadcast_keyboard())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("broadcast_"))
def broadcast_target(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    target = call.data.split("_")[1]
    msg = bot.send_message(call.message.chat.id, "📝 *Введите текст рассылки:*", parse_mode="Markdown")
    bot.register_next_step_handler(msg, send_broadcast, target, call.message.chat.id)
    bot.answer_callback_query(call.id)

def send_broadcast(message, target, admin_chat_id):
    if message.from_user.id != ADMIN_ID:
        return
    broadcast_text = message.text
    if target == "all":
        users = get_all_users()
        bot.send_message(admin_chat_id, f"📢 Рассылка для {len(users)} пользователей...")
        for user in users:
            try:
                bot.send_message(user[0], broadcast_text, parse_mode="Markdown")
            except:
                pass
        bot.send_message(admin_chat_id, f"✅ Отправлено {len(users)} сообщений")
    else:
        orders = get_all_orders()
        users_sent = set()
        for order in orders:
            if order[1] not in users_sent:
                users_sent.add(order[1])
                try:
                    bot.send_message(order[1], broadcast_text, parse_mode="Markdown")
                except:
                    pass
        bot.send_message(admin_chat_id, f"✅ Отправлено {len(users_sent)} сообщений")

@bot.callback_query_handler(func=lambda call: call.data == "admin_back")
def admin_back(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    bot.edit_message_text("🔐 *Админ-панель*", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=admin_main_keyboard())
    bot.answer_callback_query(call.id)

# ================== RUN ==================
def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print(f"🤖 Бот запущен! Админ: {ADMIN_ID}")
    print("Для проверки своего ID отправьте боту команду /id")
    bot.infinity_polling()
