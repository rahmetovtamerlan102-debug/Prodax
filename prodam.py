#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import threading
import logging
import base64
from io import BytesIO
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

PRICES = {"easy": 99, "medium": 249, "hard": 499}

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
            screenshot_base64 TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
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
    conn.commit()
    conn.close()

init_db()

def save_order(user_id, username, complexity, price, screenshot_base64=None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO orders (user_id, username, complexity, price, screenshot_base64) VALUES (?,?,?,?,?)",
                (user_id, username, complexity, price, screenshot_base64))
    order_id = cur.lastrowid
    conn.commit()
    conn.close()
    return order_id

def update_order_status(order_id, status):
    conn = sqlite3.connect(DB_NAME)
    completed_at = datetime.now().isoformat() if status == 'completed' else None
    conn.execute("UPDATE orders SET status = ?, completed_at = ? WHERE id = ?", (status, completed_at, order_id))
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

def get_all_orders(status=None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if status:
        rows = cur.execute("SELECT * FROM orders WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
    else:
        rows = cur.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    return rows

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    rows = cur.execute("SELECT DISTINCT user_id, username FROM orders").fetchall()
    conn.close()
    return rows

def save_review(user_id, username, complexity, review):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO reviews (user_id, username, complexity, review) VALUES (?,?,?,?)",
                (user_id, username, complexity, review))
    conn.commit()
    conn.close()

def get_reviews(limit=50):
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

def get_stats():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    total_orders = cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    completed_orders = cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'completed'").fetchone()[0]
    total_revenue = cur.execute("SELECT SUM(price) FROM orders WHERE status = 'completed'").fetchone()[0] or 0
    total_reviews = cur.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    pending_orders = cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'").fetchone()[0]
    conn.close()
    return {
        "total_orders": total_orders,
        "completed_orders": completed_orders,
        "pending_orders": pending_orders,
        "total_revenue": total_revenue,
        "total_reviews": total_reviews
    }

# ==================== АДМИН-КЛАВИАТУРЫ ====================
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

# ==================== HTML MINI APP ====================
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>BotMarket</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); min-height: 100vh; padding: 16px; color: #fff; }
        .container { max-width: 500px; margin: 0 auto; padding-bottom: 30px; }
        .nav { display: flex; gap: 8px; margin-bottom: 24px; background: rgba(255,255,255,0.08); backdrop-filter: blur(20px); border-radius: 60px; padding: 6px; }
        .nav-btn { flex: 1; background: transparent; border: none; border-radius: 50px; padding: 12px; font-size: 14px; font-weight: 600; color: rgba(255,255,255,0.6); cursor: pointer; transition: all 0.3s; }
        .nav-btn.active { background: linear-gradient(135deg, #a855f7, #7c3aed); color: white; }
        .level-card { background: rgba(255,255,255,0.06); backdrop-filter: blur(10px); border-radius: 24px; padding: 20px; margin-bottom: 16px; border: 1px solid rgba(255,255,255,0.1); cursor: pointer; transition: all 0.3s; }
        .level-card.selected { border: 2px solid #a855f7; background: rgba(168,85,247,0.15); }
        .level-card:active { transform: scale(0.98); }
        .level-header { display: flex; justify-content: space-between; align-items: center; }
        .level-name { font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
        .level-price { font-size: 24px; font-weight: 800; background: linear-gradient(135deg, #c084fc, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .level-desc { color: rgba(255,255,255,0.7); font-size: 13px; margin: 8px 0; }
        .features { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
        .feature-tag { background: rgba(168,85,247,0.2); border-radius: 20px; padding: 4px 12px; font-size: 11px; color: #c084fc; }
        .btn { width: 100%; border: none; border-radius: 60px; padding: 16px; font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.3s; }
        .btn:active { transform: scale(0.97); }
        .btn-primary { background: linear-gradient(135deg, #a855f7, #7c3aed); color: white; }
        .btn-success { background: linear-gradient(135deg, #22c55e, #16a34a); color: white; }
        .btn-secondary { background: rgba(255,255,255,0.1); color: white; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .payment-panel { background: rgba(255,255,255,0.06); backdrop-filter: blur(20px); border-radius: 28px; padding: 24px; }
        .payment-price { font-size: 48px; font-weight: 800; text-align: center; color: #a855f7; margin: 16px 0; }
        .payment-details { background: rgba(0,0,0,0.3); border-radius: 20px; padding: 20px; margin: 20px 0; }
        .payment-row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .card-number { font-family: monospace; font-size: 18px; letter-spacing: 2px; background: rgba(0,0,0,0.5); padding: 12px; border-radius: 12px; text-align: center; margin: 12px 0; cursor: pointer; }
        .upload-area { border: 2px dashed rgba(168,85,247,0.5); border-radius: 20px; padding: 20px; text-align: center; margin: 20px 0; cursor: pointer; }
        .upload-label { display: flex; flex-direction: column; align-items: center; gap: 8px; cursor: pointer; }
        .screenshot-preview { margin-top: 15px; max-width: 100%; border-radius: 16px; overflow: hidden; }
        .screenshot-preview img { width: 100%; border-radius: 16px; border: 2px solid #a855f7; }
        .profile-card { background: rgba(255,255,255,0.06); backdrop-filter: blur(20px); border-radius: 28px; padding: 24px; text-align: center; }
        .avatar { width: 80px; height: 80px; background: linear-gradient(135deg, #a855f7, #7c3aed); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 40px; margin: 0 auto 16px; }
        .order-history { background: rgba(0,0,0,0.2); border-radius: 20px; padding: 16px; margin-top: 20px; max-height: 400px; overflow-y: auto; }
        .order-item { display: flex; justify-content: space-between; align-items: center; padding: 12px; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .status-badge { padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: 600; }
        .status-completed { background: rgba(34,197,94,0.2); color: #22c55e; }
        .status-pending { background: rgba(245,158,11,0.2); color: #f59e0b; }
        .status-rejected { background: rgba(239,68,68,0.2); color: #ef4444; }
        .review-item { background: rgba(255,255,255,0.06); border-radius: 20px; padding: 15px; margin-bottom: 12px; }
        .review-text { font-size: 13px; color: rgba(255,255,255,0.8); margin-top: 8px; }
        .loading { text-align: center; padding: 40px; }
        .spinner { width: 40px; height: 40px; border: 3px solid rgba(168,85,247,0.3); border-top-color: #a855f7; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 15px; }
        @keyframes spin { to { transform: rotate(360deg); } }
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
    <div id="reviewModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.95); justify-content:center; align-items:center; z-index:1001;">
        <div style="background:#1a1a2e; border-radius:32px; padding:28px; max-width:380px; width:90%;">
            <h3 style="text-align:center;">✍️ Оставить отзыв</h3>
            <div id="reviewBotInfo" style="text-align:center; font-size:14px; color:#aaa; margin:10px 0;"></div>
            <textarea id="reviewText" rows="4" style="width:100%; background:rgba(255,255,255,0.1); border:none; border-radius:16px; padding:12px; color:white; margin:15px 0; font-family:inherit;"></textarea>
            <button class="btn btn-success" onclick="submitReview()">📤 Отправить</button>
            <button class="btn btn-secondary" style="margin-top:10px;" onclick="closeReviewModal()">◀ Отмена</button>
        </div>
    </div>
    <script>
        const LEVELS = {
            easy: { name: "Лёгкий", icon: "🟢", price: 99, desc: "Базовый функционал", features: ["Базовая защита", "Простая настройка"] },
            medium: { name: "Средний", icon: "🟡", price: 249, desc: "Расширенный функционал", features: ["Доп. модули", "Гибкая настройка"] },
            hard: { name: "Сложный", icon: "🔴", price: 499, desc: "Полный функционал", features: ["ML модель", "API доступ"] }
        };
        let selectedLevel = null, tgUser = null, currentStep = 'select', currentTab = 'shop', selectedScreenshot = null;
        const tg = window.Telegram?.WebApp;
        if (tg) {
            tg.ready();
            tg.expand();
            tgUser = tg.initDataUnsafe?.user;
        }
        function switchTab(tab) {
            currentTab = tab;
            document.getElementById('shop-view').style.display = tab === 'shop' ? 'block' : 'none';
            document.getElementById('profile-view').style.display = tab === 'profile' ? 'block' : 'none';
            document.getElementById('reviews-view').style.display = tab === 'reviews' ? 'block' : 'none';
            document.querySelectorAll('.nav-btn').forEach((btn,i)=>btn.classList.toggle('active', (tab==='shop'&&i===0)||(tab==='profile'&&i===1)||(tab==='reviews'&&i===2)));
            if (tab === 'shop') renderShop();
            else if (tab === 'profile') loadProfile();
            else loadReviews();
        }
        function renderShop() {
            if (currentStep === 'payment') { renderPayment(); return; }
            document.getElementById('shop-view').innerHTML = Object.entries(LEVELS).map(([k,l])=>`<div class="level-card ${selectedLevel===k?'selected':''}" onclick="selectLevel('${k}')"><div class="level-header"><div class="level-name">${l.icon} ${l.name}</div><div class="level-price">${l.price}₽</div></div><div class="level-desc">${l.desc}</div><div class="features">${l.features.map(f=>`<span class="feature-tag">✓ ${f}</span>`).join('')}</div></div>`).join('')+`<button class="btn btn-primary" ${!selectedLevel?'disabled':''} onclick="showPayment()">💎 Заказать бота</button>`;
        }
        function selectLevel(l) { selectedLevel = l; renderShop(); tg?.HapticFeedback?.impactOccurred('light'); }
        function showPayment() { 
            if (!tgUser?.id) {
                tg?.showAlert('❌ Откройте магазин через Telegram бота');
                return;
            }
            currentStep = 'payment'; renderPayment(); 
        }
        function renderPayment() {
            const l = LEVELS[selectedLevel];
            document.getElementById('shop-view').innerHTML = `<div class="payment-panel"><div class="payment-price">${l.price} ₽</div><div class="payment-details"><div class="payment-row"><span>🤖 Бот:</span><span>${l.name}</span></div><div class="payment-row"><span>🏦 Банк:</span><span>${BANK_NAME}</span></div><div class="card-number" onclick="copyCard()">${CARD_NUMBER}</div><div class="payment-row"><span>👤 Получатель:</span><span>${RECIPIENT_NAME}</span></div></div><div class="upload-area" onclick="document.getElementById('screenshotInput').click()"><div class="upload-label">📸 <span id="uploadText">Прикрепить скриншот оплаты</span></div><input type="file" id="screenshotInput" accept="image/*" style="display:none" onchange="handleScreenshot(event)"><div id="screenshotPreview" class="screenshot-preview" style="display:none"></div></div><button class="btn btn-success" id="confirmBtn" onclick="confirmOrder()" disabled>✅ Я оплатил(а)</button><button class="btn btn-secondary" style="margin-top:10px;" onclick="goBack()">◀ Назад</button></div>`;
        }
        function handleScreenshot(event) {
            const file = event.target.files[0];
            if (file && file.type.startsWith('image/')) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    selectedScreenshot = e.target.result;
                    document.getElementById('uploadText').innerHTML = '✅ Скриншот выбран';
                    document.getElementById('screenshotPreview').innerHTML = `<img src="${e.target.result}">`;
                    document.getElementById('screenshotPreview').style.display = 'block';
                    document.getElementById('confirmBtn').disabled = false;
                };
                reader.readAsDataURL(file);
            }
        }
        function copyCard() { navigator.clipboard.writeText(CARD_NUMBER); tg?.showAlert('✅ Карта скопирована!'); }
        async function confirmOrder() {
            if (!tgUser?.id) {
                tg?.showAlert('❌ Ошибка авторизации. Откройте магазин через бота.');
                return;
            }
            const btn = document.getElementById('confirmBtn');
            btn.disabled = true; btn.textContent = '⏳ Отправка...';
            try {
                const res = await fetch('/create_order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ complexity: selectedLevel, user_id: tgUser.id, username: tgUser.username || '', screenshot: selectedScreenshot }) });
                const data = await res.json();
                if (data.success) { tg?.showAlert('✅ Заказ создан! Админ проверит оплату.'); currentStep = 'select'; selectedScreenshot = null; renderShop(); if (currentTab === 'profile') loadProfile(); }
                else { tg?.showAlert('❌ ' + data.error); btn.disabled = false; btn.textContent = '✅ Я оплатил(а)'; }
            } catch (error) { tg?.showAlert('❌ Ошибка'); btn.disabled = false; btn.textContent = '✅ Я оплатил(а)'; }
        }
        function goBack() { currentStep = 'select'; renderShop(); }
        async function loadProfile() {
            if (!tgUser?.id) { document.getElementById('profile-view').innerHTML = '<div class="loading">❌ Ошибка авторизации</div>'; return; }
            document.getElementById('profile-view').innerHTML = '<div class="loading"><div class="spinner"></div>Загрузка...</div>';
            try {
                const res = await fetch(`/get_orders?user_id=${tgUser.id}`);
                const data = await res.json();
                const hasCompleted = data.orders?.some(o => o.status === 'completed');
                document.getElementById('profile-view').innerHTML = `<div class="profile-card"><div class="avatar">👤</div><div class="user-name">${tgUser.first_name || 'Пользователь'}</div><div class="user-id">ID: ${tgUser.id}</div><div class="order-history"><h3 style="margin-bottom:15px;">📋 История заказов</h3>${data.orders?.length ? data.orders.map(o => `<div class="order-item"><div><span>${o.complexity === 'easy' ? '🟢' : o.complexity === 'medium' ? '🟡' : '🔴'} ${o.complexity === 'easy' ? 'Лёгкий' : o.complexity === 'medium' ? 'Средний' : 'Сложный'}</span><div style="font-size:12px; color:#888;">${o.price}₽ • ${o.created_at?.slice(0,16)}</div></div><span class="status-badge status-${o.status}">${o.status === 'completed' ? '✅ Выполнен' : o.status === 'pending' ? '⏳ На проверке' : '❌ Отклонён'}</span></div>`).join('') : '<div style="text-align:center; padding:20px;">📭 У вас пока нет заказов</div>'}</div>${hasCompleted ? '<button class="btn btn-primary" style="margin-top:15px;" onclick="openReviewModal()">✍️ Оставить отзыв</button>' : ''}</div>`;
            } catch (error) { document.getElementById('profile-view').innerHTML = '<div class="loading">❌ Ошибка загрузки</div>'; }
        }
        async function loadReviews() {
            document.getElementById('reviews-view').innerHTML = '<div class="loading"><div class="spinner"></div>Загрузка...</div>';
            try {
                const res = await fetch('/get_reviews');
                const data = await res.json();
                document.getElementById('reviews-view').innerHTML = `<h3 style="margin-bottom:15px;">⭐ Отзывы покупателей</h3>${data.reviews?.length ? data.reviews.map(r => `<div class="review-item"><div style="display:flex; justify-content:space-between;"><span>👤 ${r.username}</span><span>${r.complexity === 'easy' ? '🟢' : r.complexity === 'medium' ? '🟡' : '🔴'}</span></div><div class="review-text">${r.review}</div><div style="font-size:10px; color:#666; margin-top:8px;">${r.created_at?.slice(0,10)}</div></div>`).join('') : '<div class="loading">⭐ Пока нет отзывов. Будьте первым!</div>'}`;
            } catch (error) { document.getElementById('reviews-view').innerHTML = '<div class="loading">❌ Ошибка загрузки</div>'; }
        }
        function openReviewModal() {
            if (!selectedLevel) { tg?.showAlert('❌ Сначала выберите уровень бота в магазине'); return; }
            document.getElementById('reviewBotInfo').innerHTML = `${LEVELS[selectedLevel].icon} ${LEVELS[selectedLevel].name}`;
            document.getElementById('reviewModal').style.display = 'flex';
            document.getElementById('reviewText').value = '';
        }
        function closeReviewModal() { document.getElementById('reviewModal').style.display = 'none'; }
        async function submitReview() {
            if (!tgUser?.id) { tg?.showAlert('❌ Ошибка авторизации'); return; }
            const text = document.getElementById('reviewText').value.trim();
            if (!text) { tg?.showAlert('❌ Напишите текст отзыва'); return; }
            const btn = document.querySelector('#reviewModal .btn-success');
            btn.disabled = true; btn.textContent = '⏳ Отправка...';
            try {
                const res = await fetch('/submit_review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: tgUser.id, username: tgUser.username || tgUser.first_name || 'user', complexity: selectedLevel, review: text }) });
                const data = await res.json();
                if (data.success) { tg?.showAlert('✅ Спасибо за отзыв!'); closeReviewModal(); if (currentTab === 'reviews') loadReviews(); }
                else { tg?.showAlert('❌ ' + data.error); }
            } catch (error) { tg?.showAlert('❌ Ошибка'); }
            btn.disabled = false; btn.textContent = '📤 Отправить';
        }
        renderShop();
    </script>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
</body>
</html>
'''

# ==================== FLASK ЭНДПОИНТЫ ====================
@flask_app.route('/')
def webapp():
    html = HTML_TEMPLATE.replace('${BANK_NAME}', BANK_NAME).replace('${CARD_NUMBER}', CARD_NUMBER).replace('${RECIPIENT_NAME}', RECIPIENT_NAME)
    return html

@flask_app.route('/create_order', methods=['POST'])
def create_order():
    try:
        data = request.get_json()
        complexity = data.get('complexity')
        user_id = data.get('user_id', 0)
        if user_id == 0:
            return jsonify({"success": False, "error": "Не удалось определить пользователя. Откройте магазин через Telegram."})
        
        price = PRICES.get(complexity, 99)
        screenshot = data.get('screenshot')
        raw_username = data.get('username', '')
        
        if raw_username and raw_username != 'user':
            display_username = f"@{raw_username}"
        else:
            display_username = f"tg://user?id={user_id}"
        
        order_id = save_order(user_id, display_username, complexity, price, screenshot)
        
        level_icon = "🟢" if complexity == "easy" else "🟡" if complexity == "medium" else "🔴"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📦 Посмотреть заказ", callback_data=f"admin_view_{order_id}"))
        
        bot.send_message(
            ADMIN_ID,
            f"🆕 *НОВЫЙ ЗАКАЗ!*\n\n📦 Заказ #{order_id}\n👤 {display_username}\n{level_icon} {complexity.upper()} — {price}₽\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@flask_app.route('/get_orders', methods=['GET'])
def get_orders():
    try:
        user_id = int(request.args.get('user_id', 0))
        orders = get_user_orders(user_id)
        orders_list = [{"id": o[0], "complexity": o[3], "price": o[4], "status": o[6], "created_at": o[7]} for o in orders]
        return jsonify({"orders": orders_list})
    except Exception as e:
        return jsonify({"orders": [], "error": str(e)})

@flask_app.route('/get_reviews', methods=['GET'])
def get_reviews_endpoint():
    try:
        reviews = get_reviews()
        reviews_list = [{"id": r[0], "username": r[2], "complexity": r[3], "review": r[4], "created_at": r[5]} for r in reviews]
        return jsonify({"reviews": reviews_list})
    except Exception as e:
        return jsonify({"reviews": [], "error": str(e)})

@flask_app.route('/submit_review', methods=['POST'])
def submit_review():
    try:
        data = request.get_json()
        user_id = data.get('user_id', 0)
        username = data.get('username', '')
        complexity = data.get('complexity')
        review = data.get('review', '').strip()
        if not review:
            return jsonify({"success": False, "error": "Текст отзыва не может быть пустым"})
        if not user_has_completed_order(user_id):
            return jsonify({"success": False, "error": "Вы можете оставить отзыв только после выполненного заказа"})
        save_review(user_id, username, complexity, review)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== ТЕЛЕГРАМ БОТ ====================
@bot.message_handler(commands=['start'])
def start(message):
    web_app_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 Открыть магазин", web_app=WebAppInfo(url=web_app_url)))
    bot.send_message(message.chat.id, "🤖 *BotMarket* — магазин Telegram ботов\n\n👇 Нажмите на кнопку, чтобы открыть магазин", parse_mode="Markdown", reply_markup=kb)

@bot.message_handler(commands=['id'])
def get_id(message):
    bot.reply_to(message, f"Ваш ID: {message.from_user.id}")

# ==================== АДМИН-ПАНЕЛЬ ====================
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "❌ Доступ запрещён")
        return
    bot.send_message(message.chat.id, "🔐 *Админ-панель*", parse_mode="Markdown", reply_markup=admin_main_keyboard())

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
        text += f"{status_emoji} #{o[0]} {level_icon} {o[4]}₽\n└ 👤 {o[2]} | {o[7][:16]}\n\n"
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
    text = f"📦 *Заказ #{order_id}*\n\n{level_icon} Уровень: {order[3].upper()}\n💰 Сумма: {order[4]}₽\n👤 Пользователь: {order[2]}\n📅 Создан: {order[7][:16]}\n📌 Статус: {status_text}\n"
    if order[5]:
        try:
            base64_data = order[5]
            if ',' in base64_data:
                base64_data = base64_data.split(',')[1]
            image_bytes = base64.b64decode(base64_data)
            photo = BytesIO(image_bytes)
            bot.send_photo(call.message.chat.id, photo, caption=text, parse_mode="Markdown", reply_markup=order_action_keyboard(order_id))
        except Exception as e:
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
        bot.send_message(order[1], f"✅ *Ваш заказ #{order_id} подтверждён!*\n\n{level_icon} {level_name} бот\n💰 Сумма: {order[4]}₽\n\nСпасибо за покупку! Вы можете оставить отзыв в профиле.", parse_mode="Markdown")
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
    bot.send_message(call.message.chat.id, "📢 *Выберите аудиторию для рассылки:*", parse_mode="Markdown", reply_markup=broadcast_keyboard())
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
        bot.send_message(admin_chat_id, f"📢 Начинаю рассылку для {len(users)} пользователей...")
        for user in users:
            try:
                bot.send_message(user[0], broadcast_text, parse_mode="Markdown")
            except:
                pass
        bot.send_message(admin_chat_id, f"✅ Рассылка завершена! Отправлено {len(users)} сообщений.")
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
        bot.send_message(admin_chat_id, f"✅ Рассылка завершена! Отправлено {len(users_sent)} сообщений.")

@bot.callback_query_handler(func=lambda call: call.data == "admin_back")
def admin_back(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    bot.edit_message_text("🔐 *Админ-панель*", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=admin_main_keyboard())
    bot.answer_callback_query(call.id)

# ==================== ЗАПУСК ====================
def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    print(f"🤖 Бот запущен! Админ: {ADMIN_ID}")
    print("Для проверки своего ID отправьте боту команду /id")
    bot.infinity_polling()
