#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import threading
import base64
from io import BytesIO
from datetime import datetime

from flask import Flask, request, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

if not BOT_TOKEN:
    raise Exception("BOT_TOKEN missing")

# Реквизиты
CARD_NUMBER = "2200702150754195"
RECIPIENT = "Мухаммад А"
BANK_NAME = "Т-Банк"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ================= DB =================
DB = "shop.db"

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = conn()
    cur = c.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        text TEXT,
        created_at TEXT
    )
    """)

    c.commit()
    c.close()

init()

# ================= SHOP =================
LEVELS = {
    "easy": {"name": "Лёгкий", "price": 99, "icon": "🟢"},
    "medium": {"name": "Средний", "price": 249, "icon": "🟡"},
    "hard": {"name": "Сложный", "price": 499, "icon": "🔴"}
}

# ================= DB FUNCTIONS =================
def add_order(user_id, username, level, screenshot=None):
    c = conn()
    cur = c.cursor()
    price = LEVELS[level]["price"]

    cur.execute("""
        INSERT INTO orders (user_id, username, level, price, screenshot, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, level, price, screenshot, datetime.now().isoformat()))

    c.commit()
    oid = cur.lastrowid
    c.close()
    return oid

def get_orders(user_id):
    c = conn()
    cur = c.cursor()
    cur.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user_id,))
    rows = cur.fetchall()
    c.close()
    return rows

def get_all_orders(status=None):
    c = conn()
    cur = c.cursor()
    if status:
        cur.execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC", (status,))
    else:
        cur.execute("SELECT * FROM orders ORDER BY id DESC")
    rows = cur.fetchall()
    c.close()
    return rows

def update_order_status(order_id, status):
    c = conn()
    cur = c.cursor()
    cur.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    c.commit()
    c.close()

def get_order(order_id):
    c = conn()
    cur = c.cursor()
    cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    c.close()
    return row

def add_review(user_id, username, text):
    c = conn()
    cur = c.cursor()
    cur.execute("""
        INSERT INTO reviews (user_id, username, text, created_at)
        VALUES (?, ?, ?, ?)
    """, (user_id, username, text, datetime.now().isoformat()))
    c.commit()
    c.close()

def get_reviews():
    c = conn()
    cur = c.cursor()
    cur.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT 50")
    rows = cur.fetchall()
    c.close()
    return rows

def get_stats():
    c = conn()
    cur = c.cursor()
    total = cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    pending = cur.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
    completed = cur.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0]
    revenue = cur.execute("SELECT SUM(price) FROM orders WHERE status='completed'").fetchone()[0] or 0
    reviews_count = cur.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    c.close()
    return {"total": total, "pending": pending, "completed": completed, "revenue": revenue, "reviews": reviews_count}

# ================= АДМИН-КЛАВИАТУРЫ =================
def admin_main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📦 Все заказы", callback_data="admin_all"),
        InlineKeyboardButton("⏳ В ожидании", callback_data="admin_pending"),
        InlineKeyboardButton("✅ Выполненные", callback_data="admin_completed"),
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("⭐ Отзывы", callback_data="admin_reviews")
    )
    return kb

def order_action_keyboard(order_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{order_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{order_id}")
    )
    kb.add(InlineKeyboardButton("◀ Назад", callback_data="admin_back"))
    return kb

# ================= TELEGRAM =================
@bot.message_handler(commands=["start"])
def start(m):
    url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webapp"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 Открыть магазин", web_app=WebAppInfo(url=url)))
    bot.send_message(m.chat.id, "🤖 BotShop\n👇 Нажми на кнопку", reply_markup=kb)

@bot.message_handler(commands=["admin"])
def admin(m):
    if m.from_user.id != ADMIN_ID:
        return bot.send_message(m.chat.id, "⛔ Нет доступа")
    bot.send_message(m.chat.id, "🔐 Админ-панель", reply_markup=admin_main_keyboard())

# Админ колбэки
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
    show_orders(call.message, orders, "⏳ В ожидании")

@bot.callback_query_handler(func=lambda call: call.data == "admin_completed")
def admin_completed(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    orders = get_all_orders(status="completed")
    show_orders(call.message, orders, "✅ Выполненные")

def show_orders(message, orders, title):
    if not orders:
        bot.send_message(message.chat.id, f"📭 {title}: нет заказов", reply_markup=admin_main_keyboard())
        return
    text = f"📋 {title} ({len(orders)})\n\n"
    for o in orders[:10]:
        icon = LEVELS[o["level"]]["icon"]
        status = "⏳" if o["status"] == "pending" else "✅" if o["status"] == "completed" else "❌"
        text += f"{status} #{o['id']} {icon} {o['price']}₽\n└ 👤 @{o['username'] or o['user_id']} | {o['created_at'][:16]}\n\n"
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
    status_text = "⏳ На проверке" if order["status"] == "pending" else "✅ Выполнен" if order["status"] == "completed" else "❌ Отклонён"
    text = f"📦 Заказ #{order_id}\n\n{icon} {order['level']} — {order['price']}₽\n👤 @{order['username'] or order['user_id']}\n📅 {order['created_at'][:16]}\n📌 {status_text}"
    
    if order["screenshot"]:
        try:
            img_data = order["screenshot"]
            if "," in img_data:
                img_data = img_data.split(",")[1]
            img = base64.b64decode(img_data)
            bot.send_photo(call.message.chat.id, BytesIO(img), caption=text, reply_markup=order_action_keyboard(order_id))
        except:
            bot.send_message(call.message.chat.id, text + "\n⚠️ Ошибка скриншота", reply_markup=order_action_keyboard(order_id))
    else:
        bot.send_message(call.message.chat.id, text, reply_markup=order_action_keyboard(order_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
def confirm_order(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    order_id = int(call.data.split("_")[1])
    order = get_order(order_id)
    if order:
        update_order_status(order_id, "completed")
        icon = LEVELS[order["level"]]["icon"]
        level_name = LEVELS[order["level"]]["name"]
        bot.send_message(order["user_id"], f"✅ Заказ #{order_id} подтверждён!\n\n{icon} {level_name} — {order['price']}₽\nСпасибо за покупку!")
        bot.answer_callback_query(call.id, "Заказ подтверждён")
        bot.edit_message_caption(f"✅ ЗАКАЗ #{order_id} ПОДТВЕРЖДЁН", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("reject_"))
def reject_order(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    order_id = int(call.data.split("_")[1])
    order = get_order(order_id)
    if order:
        update_order_status(order_id, "rejected")
        bot.send_message(order["user_id"], f"❌ Заказ #{order_id} отклонён\nПожалуйста, оформите заново.")
        bot.answer_callback_query(call.id, "Заказ отклонён")
        bot.edit_message_caption(f"❌ ЗАКАЗ #{order_id} ОТКЛОНЁН", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def admin_stats(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Нет доступа")
    stats = get_stats()
    text = f"📊 СТАТИСТИКА\n\n📦 Всего: {stats['total']}\n⏳ В ожидании: {stats['pending']}\n✅ Выполнено: {stats['completed']}\n💰 Выручка: {stats['revenue']}₽\n⭐ Отзывов: {stats['reviews']}"
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
    data = request.json
    user_id = data.get("user_id")
    username = data.get("username", "")
    level = data.get("level")
    screenshot = data.get("screenshot")
    
    if not user_id or not level:
        return jsonify({"ok": False})
    
    order_id = add_order(user_id, username, level, screenshot)
    icon = LEVELS[level]["icon"]
    
    bot.send_message(ADMIN_ID, f"🆕 НОВЫЙ ЗАКАЗ!\n\n📦 #{order_id}\n👤 @{username or user_id}\n{icon} {level} — {LEVELS[level]['price']}₽")
    
    return jsonify({"ok": True, "order_id": order_id})

@app.route("/orders")
def orders():
    uid = request.args.get("user_id")
    rows = get_orders(uid)
    return jsonify([{"id": r["id"], "level": r["level"], "price": r["price"], "status": r["status"], "created_at": r["created_at"]} for r in rows])

@app.route("/reviews")
def reviews():
    rows = get_reviews()
    return jsonify([{"username": r["username"], "text": r["text"], "created_at": r["created_at"]} for r in rows])

@app.route("/review", methods=["POST"])
def review():
    d = request.json
    add_review(d["user_id"], d["username"], d["text"])
    return jsonify({"ok": True})

# ================= WEBAPP HTML =================
HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:sans-serif;min-height:100vh;padding:15px}
.container{max-width:500px;margin:0 auto}
.nav{display:flex;gap:10px;margin-bottom:20px;background:rgba(255,255,255,0.1);border-radius:50px;padding:5px}
.nav-btn{flex:1;padding:12px;border:none;border-radius:50px;background:transparent;color:white;font-weight:bold;cursor:pointer}
.nav-btn.active{background:#7c3aed}
.card{background:rgba(255,255,255,0.1);backdrop-filter:blur(10px);padding:20px;border-radius:20px;margin:10px 0;cursor:pointer}
.card.selected{border:2px solid #7c3aed}
.price{font-size:24px;color:#ffd700;margin-top:10px}
.btn{padding:14px;border-radius:50px;border:none;width:100%;margin-top:10px;font-weight:bold;cursor:pointer}
.btn-purple{background:#7c3aed;color:white}
.btn-green{background:#22c55e;color:white}
.btn-red{background:#ef4444;color:white}
.btn-gray{background:rgba(255,255,255,0.2);color:white}
input{width:100%;padding:12px;margin:10px 0;border-radius:16px;border:none;background:rgba(0,0,0,0.3);color:white}
.card-number{background:#000;padding:12px;border-radius:12px;text-align:center;font-family:monospace;cursor:pointer}
.preview{max-width:100%;border-radius:12px;margin-top:10px}
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
        <button class="nav-btn" id="btn-shop" onclick="switchTab('shop')">🛒 Магазин</button>
        <button class="nav-btn" id="btn-profile" onclick="switchTab('profile')">👤 Профиль</button>
        <button class="nav-btn" id="btn-reviews" onclick="switchTab('reviews')">⭐ Отзывы</button>
    </div>
    <div id="shop-view"></div>
    <div id="profile-view" style="display:none"></div>
    <div id="reviews-view" style="display:none"></div>
</div>

<div id="reviewModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.95); justify-content:center; align-items:center; z-index:1001">
    <div style="background:#1a1a2e; border-radius:32px; padding:28px; max-width:380px; width:90%">
        <h3>✍️ Оставить отзыв</h3>
        <textarea id="reviewText" rows="4" style="width:100%; padding:12px; margin:15px 0; border-radius:16px; background:rgba(255,255,255,0.1); border:none; color:white"></textarea>
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
    document.getElementById('btn-shop').classList.toggle('active', tab === 'shop');
    document.getElementById('btn-profile').classList.toggle('active', tab === 'profile');
    document.getElementById('btn-reviews').classList.toggle('active', tab === 'reviews');
    if (tab === 'shop') renderShop();
    else if (tab === 'profile') loadProfile();
    else loadReviews();
}

function renderShop() {
    if (currentStep === 'payment') { renderPayment(); return; }
    let html = '';
    for (let [k, l] of Object.entries(LEVELS)) {
        html += `<div class="card ${selectedLevel === k ? 'selected' : ''}" onclick="selectLevel('${k}')">
            <h3>${l.icon} ${l.name}</h3>
            <div class="price">${l.price}₽</div>
            <small>${l.desc}</small>
        </div>`;
    }
    html += `<button class="btn btn-purple" ${!selectedLevel ? 'disabled' : ''} onclick="showPayment()">💎 Заказать</button>`;
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
            <h3>💳 Оплата Т-Банк</h3>
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

function copyCard() { 
    navigator.clipboard.writeText("2200702150754195"); 
    tg.showAlert("✅ Карта скопирована");
}

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
        if (data.ok) {
            tg.showAlert('✅ Заказ создан! Админ проверит оплату');
            currentStep = 'select';
            selectedScreenshot = null;
            renderShop();
            if (currentTab === 'profile') loadProfile();
        } else {
            tg.showAlert('❌ Ошибка');
            btn.disabled = false;
            btn.textContent = '✅ Я оплатил';
        }
    } catch(e) { 
        tg.showAlert('❌ Ошибка'); 
        btn.disabled = false; 
        btn.textContent = '✅ Я оплатил';
    }
}

function goBack() { currentStep = 'select'; renderShop(); }

async function loadProfile() {
    if (!tgUser?.id) { 
        document.getElementById('profile-view').innerHTML = '<div class="loading">❌ Ошибка авторизации</div>'; 
        return; 
    }
    document.getElementById('profile-view').innerHTML = '<div class="loading"><div class="spinner"></div>Загрузка...</div>';
    try {
        const res = await fetch(`/orders?user_id=${tgUser.id}`);
        const orders = await res.json();
        let hasCompleted = false;
        let ordersHtml = '';
        if (orders.length) {
            ordersHtml = orders.map(o => {
                if (o.status === 'completed') hasCompleted = true;
                const icon = o.level === 'easy' ? '🟢' : o.level === 'medium' ? '🟡' : '🔴';
                const name = o.level === 'easy' ? 'Лёгкий' : o.level === 'medium' ? 'Средний' : 'Сложный';
                const statusClass = `status-${o.status}`;
                const statusText = o.status === 'completed' ? '✅ Выполнен' : o.status === 'pending' ? '⏳ На проверке' : '❌ Отклонён';
                return `<div class="order-item"><span>${icon} ${name} — ${o.price}₽</span><span class="${statusClass}">${statusText}</span></div>`;
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
    } catch(e) { 
        document.getElementById('profile-view').innerHTML = '<div class="loading">❌ Ошибка</div>';
    }
}

async function loadReviews() {
    document.getElementById('reviews-view').innerHTML = '<div class="loading"><div class="spinner"></div>Загрузка...</div>';
    try {
        const res = await fetch('/reviews');
        const reviews = await res.json();
        if (reviews.length) {
            document.getElementById('reviews-view').innerHTML = `<div class="card"><h3>⭐ Отзывы</h3>${reviews.map(r => `
                <div class="review-item">
                    <b>👤 ${r.username}</b>
                    <div>${r.text}</div>
                    <small>${r.created_at?.slice(0,10)}</small>
                </div>
            `).join('')}</div>`;
        } else {
            document.getElementById('reviews-view').innerHTML = '<div class="card">⭐ Пока нет отзывов</div>';
        }
    } catch(e) { 
        document.getElementById('reviews-view').innerHTML = '<div class="loading">❌ Ошибка</div>';
    }
}

function openReviewModal() {
    if (!selectedLevel) { 
        tg.showAlert('❌ Сначала выберите бота в магазине'); 
        return; 
    }
    document.getElementById('reviewModal').style.display = 'flex';
    document.getElementById('reviewText').value = '';
}

function closeReviewModal() { 
    document.getElementById('reviewModal').style.display = 'none'; 
}

async function submitReview() {
    const text = document.getElementById('reviewText').value.trim();
    if (!text) { 
        tg.showAlert('❌ Напишите отзыв'); 
        return; 
    }
    const btn = document.querySelector('#reviewModal .btn-green');
    btn.disabled = true;
    btn.textContent = '⏳...';
    try {
        const res = await fetch('/review', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                user_id: tgUser.id,
                username: tgUser.username || tgUser.first_name || 'user',
                text: text
            })
        });
        const data = await res.json();
        if (data.ok) {
            tg.showAlert('✅ Спасибо за отзыв!');
            closeReviewModal();
            if (currentTab === 'reviews') loadReviews();
        } else {
            tg.showAlert('❌ Ошибка');
        }
    } catch(e) { 
        tg.showAlert('❌ Ошибка'); 
    }
    btn.disabled = false;
    btn.textContent = '📤 Отправить';
}

renderShop();
</script>
</body>
</html>
"""

# ================= RUN =================
def run():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run).start()
    print(f"🤖 Бот запущен! Админ: {ADMIN_ID}")
    bot.infinity_polling()
