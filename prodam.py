#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import asyncio
import threading
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")

ADMIN_ID = int(os.getenv("ADMIN_ID", "8276815852"))
PORT = int(os.getenv("PORT", "8080"))

# Реквизиты
CARD_NUMBER = "2200702150754195"
RECIPIENT_NAME = "Мухаммад А"
BANK_NAME = "Т-Банк"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
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

def get_reviews(complexity=None, limit=30):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if complexity:
        rows = cur.execute("SELECT * FROM reviews WHERE complexity = ? ORDER BY id DESC LIMIT ?", (complexity, limit)).fetchall()
    else:
        rows = cur.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows

def user_has_completed_order(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    row = cur.execute("SELECT 1 FROM orders WHERE user_id = ? AND status = 'completed' LIMIT 1", (user_id,)).fetchone()
    conn.close()
    return row is not None

# ==================== FSM ====================
class PaymentForm(StatesGroup):
    waiting_for_screenshot = State()

# ==================== HTML МИНИ-ПРИЛОЖЕНИЯ ====================
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>BotMarket | Магазин ботов</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: radial-gradient(ellipse at 20% 30%, #0a0a2a, #050510); min-height: 100vh; padding: 20px; color: #fff; position: relative; overflow-x: hidden; }
        .bg-shapes { position: fixed; top: 0; left: 0; width: 100%; height: 100%; overflow: hidden; z-index: 0; }
        .shape { position: absolute; border-radius: 50%; filter: blur(60px); opacity: 0.4; animation: float 20s infinite ease-in-out; }
        .shape-1 { width: 300px; height: 300px; background: #a855f7; top: -100px; left: -100px; }
        .shape-2 { width: 400px; height: 400px; background: #3b82f6; bottom: -150px; right: -150px; animation-delay: -5s; }
        .shape-3 { width: 250px; height: 250px; background: #06b6d4; top: 50%; left: 50%; transform: translate(-50%, -50%); animation-delay: -10s; }
        @keyframes float { 0%, 100% { transform: translate(0, 0) rotate(0deg); } 50% { transform: translate(30px, 20px) rotate(10deg); } }
        .container { position: relative; z-index: 1; max-width: 500px; margin: 0 auto; }
        .nav { display: flex; gap: 10px; margin-bottom: 30px; background: rgba(20, 20, 40, 0.5); backdrop-filter: blur(20px); border-radius: 60px; padding: 6px; }
        .nav-btn { flex: 1; background: transparent; border: none; border-radius: 50px; padding: 12px; font-size: 14px; font-weight: 600; color: #8b8ca0; cursor: pointer; transition: all 0.3s; }
        .nav-btn.active { background: linear-gradient(135deg, #a855f7, #7c3aed); color: white; box-shadow: 0 4px 15px rgba(168,85,247,0.3); }
        .header { text-align: center; margin-bottom: 30px; padding: 10px 0; }
        .logo { font-size: 48px; margin-bottom: 10px; }
        .header h1 { font-size: 28px; background: linear-gradient(135deg, #ffffff, #c084fc, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 5px; }
        .header p { color: #8b8ca0; font-size: 13px; }
        .badge { display: inline-block; background: rgba(168,85,247,0.2); border: 1px solid rgba(168,85,247,0.5); border-radius: 50px; padding: 4px 12px; font-size: 11px; color: #c084fc; margin-top: 8px; }
        .level-card { background: rgba(20, 20, 40, 0.6); backdrop-filter: blur(20px); border-radius: 28px; padding: 20px; margin-bottom: 16px; border: 1px solid rgba(255,255,255,0.1); transition: all 0.4s; cursor: pointer; }
        .level-card.selected { border: 2px solid #a855f7; background: rgba(168,85,247,0.15); box-shadow: 0 0 30px rgba(168,85,247,0.3); }
        .level-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .level-name { font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
        .level-price { font-size: 28px; font-weight: 800; background: linear-gradient(135deg, #c084fc, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .level-price small { font-size: 11px; color: #6b6b8a; }
        .level-desc { color: #b0b0c8; font-size: 13px; line-height: 1.4; margin-bottom: 12px; }
        .features { display: flex; flex-wrap: wrap; gap: 8px; }
        .feature-tag { background: rgba(255,255,255,0.05); border-radius: 20px; padding: 4px 10px; font-size: 11px; }
        .payment-panel { background: rgba(20, 20, 40, 0.8); backdrop-filter: blur(20px); border-radius: 28px; padding: 24px; margin-top: 20px; text-align: center; }
        .payment-price { font-size: 42px; font-weight: 800; color: #a855f7; margin: 15px 0; }
        .payment-details { background: rgba(255,255,255,0.05); border-radius: 20px; padding: 20px; margin: 20px 0; text-align: left; }
        .payment-details p { margin: 12px 0; display: flex; justify-content: space-between; align-items: center; }
        .card-number { font-family: monospace; font-size: 18px; letter-spacing: 2px; background: rgba(0,0,0,0.3); padding: 12px; border-radius: 12px; text-align: center; margin-top: 10px; cursor: pointer; }
        .screenshot-upload { margin: 15px 0; text-align: center; }
        .screenshot-label { display: inline-block; background: linear-gradient(135deg, #a855f7, #7c3aed); border-radius: 60px; padding: 14px 24px; font-size: 14px; font-weight: 600; color: white; cursor: pointer; transition: all 0.3s; }
        .screenshot-label:hover { opacity: 0.9; transform: scale(1.02); }
        .screenshot-preview { margin-top: 15px; max-width: 100%; border-radius: 16px; display: none; }
        .screenshot-preview.active { display: block; }
        .screenshot-preview img { max-width: 100%; border-radius: 16px; border: 2px solid #a855f7; }
        .screenshot-name { font-size: 12px; color: #8b8ca0; margin-top: 8px; }
        .confirm-btn { width: 100%; background: linear-gradient(135deg, #22c55e, #16a34a); border: none; border-radius: 60px; padding: 16px; font-size: 16px; font-weight: 600; color: white; margin-top: 15px; cursor: pointer; transition: all 0.3s; }
        .confirm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .back-btn { width: 100%; background: rgba(255,255,255,0.1); border: none; border-radius: 60px; padding: 14px; font-size: 14px; color: white; margin-top: 10px; cursor: pointer; }
        .order-btn { width: 100%; background: linear-gradient(135deg, #a855f7, #7c3aed); border: none; border-radius: 60px; padding: 16px; font-size: 16px; font-weight: 600; color: white; margin-top: 20px; cursor: pointer; transition: all 0.3s; }
        .order-btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .profile-card { background: rgba(20, 20, 40, 0.6); backdrop-filter: blur(20px); border-radius: 28px; padding: 24px; margin-bottom: 20px; text-align: center; }
        .avatar { font-size: 64px; margin-bottom: 10px; }
        .user-name { font-size: 22px; font-weight: 600; margin-bottom: 5px; }
        .user-id { color: #8b8ca0; font-size: 13px; margin-bottom: 15px; }
        .order-history { background: rgba(0,0,0,0.3); border-radius: 20px; padding: 16px; margin-top: 10px; max-height: 400px; overflow-y: auto; }
        .order-item { display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .order-status { padding: 4px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; }
        .status-pending { background: rgba(245,158,11,0.2); color: #f59e0b; }
        .status-completed { background: rgba(34,197,94,0.2); color: #22c55e; }
        .status-rejected { background: rgba(239,68,68,0.2); color: #ef4444; }
        .empty-orders { text-align: center; padding: 30px; color: #6b6b8a; }
        .loading { text-align: center; padding: 40px; }
        .spinner { width: 40px; height: 40px; border: 3px solid rgba(168,85,247,0.3); border-top-color: #a855f7; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 15px; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .success-text { color: #22c55e; margin-top: 20px; text-align: center; }
        .reviews-section { margin-top: 30px; }
        .review-item { background: rgba(255,255,255,0.05); border-radius: 20px; padding: 15px; margin-bottom: 12px; }
        .review-header { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 12px; color: #8b8ca0; }
        .review-text { font-size: 13px; line-height: 1.4; color: #ccc; }
        .write-review-btn { background: linear-gradient(135deg, #a855f7, #7c3aed); border: none; border-radius: 60px; padding: 12px; font-size: 14px; font-weight: 600; color: white; width: 100%; margin-top: 15px; cursor: pointer; }
        .review-modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.9); z-index: 1001; justify-content: center; align-items: center; }
        .review-modal.active { display: flex; }
        .review-modal-content { background: linear-gradient(135deg, #1a1a2e, #16213e); border-radius: 32px; padding: 28px; max-width: 380px; width: 90%; }
        .review-textarea { width: 100%; background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); border-radius: 16px; padding: 12px; color: white; font-size: 14px; margin: 15px 0; resize: none; font-family: inherit; }
        .review-textarea:focus { outline: none; border-color: #a855f7; }
        .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.8); backdrop-filter: blur(10px); padding: 12px 20px; border-radius: 50px; font-size: 14px; z-index: 2000; animation: fadeInOut 2s ease; }
        @keyframes fadeInOut { 0% { opacity: 0; transform: translateX(-50%) translateY(20px); } 15% { opacity: 1; transform: translateX(-50%) translateY(0); } 85% { opacity: 1; } 100% { opacity: 0; } }
    </style>
</head>
<body>
    <div class="bg-shapes">
        <div class="shape shape-1"></div>
        <div class="shape shape-2"></div>
        <div class="shape shape-3"></div>
    </div>
    <div class="container">
        <div class="nav">
            <button class="nav-btn" id="nav-shop" onclick="switchTab('shop')">🛒 Магазин</button>
            <button class="nav-btn" id="nav-profile" onclick="switchTab('profile')">👤 Профиль</button>
            <button class="nav-btn" id="nav-reviews" onclick="switchTab('reviews')">⭐ Отзывы</button>
        </div>
        <div id="shop-view" style="display: block;">
            <div class="header">
                <div class="logo">🤖</div>
                <h1>BotMarket</h1>
                <p>Премиум Telegram боты</p>
                <div class="badge">⚡ Мгновенное создание</div>
            </div>
            <div id="shop-content"></div>
        </div>
        <div id="profile-view" style="display: none;">
            <div id="profile-content"></div>
        </div>
        <div id="reviews-view" style="display: none;">
            <div id="reviews-content"></div>
        </div>
    </div>

    <!-- Модальное окно отзыва -->
    <div id="reviewModal" class="review-modal">
        <div class="review-modal-content">
            <h3 style="text-align:center; margin-bottom:15px;">✍️ Оставить отзыв</h3>
            <div id="reviewBotInfo" style="text-align:center; font-size:14px; color:#aaa; margin-bottom:10px;"></div>
            <textarea id="reviewText" class="review-textarea" rows="4" placeholder="Поделитесь впечатлениями о боте..."></textarea>
            <button class="confirm-btn" onclick="submitReview()">📤 Отправить отзыв</button>
            <button class="back-btn" onclick="closeReviewModal()">◀ Отмена</button>
        </div>
    </div>

    <script>
        const LEVELS = {
            easy: { name: "Лёгкий", icon: "🟢", price: 99, desc: "Базовый функционал, простая настройка", features: ["Базовая защита", "Простая настройка", "Быстрая установка"] },
            medium: { name: "Средний", icon: "🟡", price: 249, desc: "Расширенный функционал, доп. модули", features: ["Всё из лёгкого", "Дополнительные модули", "Гибкая настройка", "Приоритетная поддержка"] },
            hard: { name: "Сложный", icon: "🔴", price: 499, desc: "Полный функционал, ML модель", features: ["Всё из среднего", "ML модель", "Полная админ-панель", "Приоритетная поддержка", "API доступ"] }
        };
        let selectedLevel = null;
        let tgUser = null;
        let currentStep = 'select';
        let selectedScreenshot = null;
        
        // Инициализация Telegram WebApp
        const tg = window.Telegram?.WebApp;
        if (tg) {
            tg.ready();
            tg.expand();
            tg.enableClosingConfirmation();
            tg.setHeaderColor('bg_color');
            tg.setBackgroundColor('#0a0a2a');
            tgUser = tg.initDataUnsafe?.user;
        }
        
        function switchTab(tab) {
            if (currentStep !== 'select') { currentStep = 'select'; selectedLevel = null; selectedScreenshot = null; }
            const shopView = document.getElementById('shop-view');
            const profileView = document.getElementById('profile-view');
            const reviewsView = document.getElementById('reviews-view');
            const navShop = document.getElementById('nav-shop');
            const navProfile = document.getElementById('nav-profile');
            const navReviews = document.getElementById('nav-reviews');
            
            shopView.style.display = 'none';
            profileView.style.display = 'none';
            reviewsView.style.display = 'none';
            navShop.classList.remove('active');
            navProfile.classList.remove('active');
            navReviews.classList.remove('active');
            
            if (tab === 'shop') {
                shopView.style.display = 'block';
                navShop.classList.add('active');
                renderShop();
            } else if (tab === 'profile') {
                profileView.style.display = 'block';
                navProfile.classList.add('active');
                loadProfile();
            } else {
                reviewsView.style.display = 'block';
                navReviews.classList.add('active');
                loadReviews();
            }
        }
        
        function renderShop() {
            if (currentStep === 'payment') { renderPayment(); return; }
            if (currentStep === 'success') { renderSuccess(); return; }
            const container = document.getElementById('shop-content');
            container.innerHTML = Object.entries(LEVELS).map(([key, level]) => `<div class="level-card ${selectedLevel === key ? 'selected' : ''}" onclick="selectLevel('${key}')"><div class="level-header"><div class="level-name"><span>${level.icon}</span> ${level.name}</div><div class="level-price">${level.price}<small>₽</small></div></div><div class="level-desc">${level.desc}</div><div class="features">${level.features.map(f => `<span class="feature-tag">✓ ${f}</span>`).join('')}</div></div>`).join('') + `<button class="order-btn" ${!selectedLevel ? 'disabled' : ''} onclick="showPayment()">${!selectedLevel ? 'Выберите уровень' : '💎 Заказать бота'}</button>`;
        }
        
        function selectLevel(level) { selectedLevel = level; renderShop(); if (tg?.HapticFeedback) tg.HapticFeedback.impactOccurred('light'); }
        
        function showPayment() {
            if (!selectedLevel) return;
            currentStep = 'payment';
            selectedScreenshot = null;
            renderPayment();
        }
        
        function renderPayment() {
            const level = LEVELS[selectedLevel];
            const container = document.getElementById('shop-content');
            container.innerHTML = `
                <div class="payment-panel">
                    <h3>📋 Ваш заказ</h3>
                    <div class="payment-price">${level.price} ₽</div>
                    <div class="payment-details">
                        <p><span>🤖 Бот:</span> <span>${level.name}</span></p>
                        <p><span>📦 Уровень:</span> <span>${level.desc}</span></p>
                    </div>
                    <div class="payment-details">
                        <p><span>🏦 Банк:</span> <span>${BANK_NAME}</span></p>
                        <p><span>💳 Карта:</span></p>
                        <div class="card-number" onclick="copyCard()">${CARD_NUMBER}</div>
                        <p><span>👤 Получатель:</span> <span>${RECIPIENT_NAME}</span></p>
                    </div>
                    <div class="screenshot-upload">
                        <label class="screenshot-label" onclick="uploadScreenshot()">📸 Прикрепить скриншот оплаты</label>
                        <div id="screenshotPreview" class="screenshot-preview"></div>
                        <div id="screenshotName" class="screenshot-name"></div>
                    </div>
                    <button class="confirm-btn" id="confirmBtn" onclick="confirmOrder()" disabled>✅ Я оплатил(а)</button>
                    <button class="back-btn" onclick="goBack()">◀ Назад</button>
                </div>
            `;
        }
        
        function uploadScreenshot() {
            if (!tg) {
                showToast('❌ Telegram WebApp не обнаружен');
                return;
            }
            tg.showPopup({
                title: 'Прикрепить скриншот',
                message: 'Отправьте скриншот чека из приложения банка',
                buttons: [{type: 'ok'}, {type: 'cancel'}]
            }, (buttonId) => {
                if (buttonId === 0) {
                    tg.showAlert('📸 Отправьте скриншот в этот чат с ботом командой /pay');
                }
            });
        }
        
        function setScreenshotPreview(file) {
            if (file && file.type.startsWith('image/')) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    const preview = document.getElementById('screenshotPreview');
                    preview.innerHTML = `<img src="${e.target.result}" alt="Скриншот">`;
                    preview.classList.add('active');
                    document.getElementById('screenshotName').innerHTML = `✅ ${file.name}`;
                    selectedScreenshot = file;
                    const btn = document.getElementById('confirmBtn');
                    if (btn) btn.disabled = false;
                };
                reader.readAsDataURL(file);
            } else {
                showToast('❌ Пожалуйста, выберите изображение');
            }
        }
        
        function copyCard() {
            navigator.clipboard.writeText(CARD_NUMBER);
            if (tg) tg.showAlert('✅ Номер карты скопирован!');
            else showToast('✅ Номер карты скопирован!');
        }
        
        async function confirmOrder() {
            const btn = document.getElementById('confirmBtn');
            btn.disabled = true;
            btn.textContent = '⏳ Отправка...';
            
            try {
                const response = await fetch('/create_order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        complexity: selectedLevel, 
                        user_id: tgUser?.id || 0, 
                        username: tgUser?.username || tgUser?.first_name || 'user'
                    })
                });
                const data = await response.json();
                if (data.success) {
                    if (tg) tg.showAlert('✅ Заказ оформлен! Отправьте скриншот оплаты боту командой /pay');
                    else showToast('✅ Заказ оформлен!');
                    currentStep = 'success';
                    renderSuccess();
                } else {
                    btn.disabled = false;
                    btn.textContent = '✅ Я оплатил(а)';
                    if (tg) tg.showAlert('❌ Ошибка: ' + data.error);
                    else showToast('❌ Ошибка: ' + data.error);
                }
            } catch (error) {
                btn.disabled = false;
                btn.textContent = '✅ Я оплатил(а)';
                if (tg) tg.showAlert('❌ Ошибка: ' + error);
                else showToast('❌ Ошибка: ' + error);
            }
        }
        
        function renderSuccess() {
            const container = document.getElementById('shop-content');
            container.innerHTML = `
                <div class="payment-panel">
                    <div class="success-text">
                        <div style="font-size: 64px;">✅</div>
                        <h3>Заказ оформлен!</h3>
                        <p style="margin-top: 15px;">Отправьте скриншот оплаты боту командой <code style="background:#333;padding:4px 8px;border-radius:8px;">/pay</code></p>
                        <button class="back-btn" style="margin-top: 30px;" onclick="goBack()">◀ Вернуться в магазин</button>
                    </div>
                </div>
            `;
        }
        
        function goBack() {
            currentStep = 'select';
            selectedLevel = null;
            selectedScreenshot = null;
            renderShop();
        }
        
        async function loadProfile() {
            const container = document.getElementById('profile-content');
            container.innerHTML = '<div class="loading"><div class="spinner"></div>Загрузка...</div>';
            try {
                const user_id = tgUser?.id || 0;
                const response = await fetch(`/get_orders?user_id=${user_id}`);
                const data = await response.json();
                const userName = tgUser?.first_name || 'Пользователь';
                const fullName = `${tgUser?.first_name || ''} ${tgUser?.last_name || ''}`.trim() || userName;
                let ordersHtml = '';
                let hasCompleted = false;
                if (data.orders && data.orders.length > 0) {
                    ordersHtml = data.orders.map(order => {
                        if (order.status === 'completed') hasCompleted = true;
                        let statusText = '', statusClass = '';
                        if (order.status === 'completed') { statusText = 'Выполнен'; statusClass = 'status-completed'; }
                        else if (order.status === 'pending') { statusText = 'Ожидает оплаты'; statusClass = 'status-pending'; }
                        else { statusText = 'Отклонён'; statusClass = 'status-rejected'; }
                        const levelIcon = order.complexity === 'easy' ? '🟢' : order.complexity === 'medium' ? '🟡' : '🔴';
                        const levelName = order.complexity === 'easy' ? 'Лёгкий' : order.complexity === 'medium' ? 'Средний' : 'Сложный';
                        return `<div class="order-item"><div><div>${levelIcon} ${levelName} — ${order.price}₽</div><div style="font-size:11px;color:#6b6b8a;">Заказ #${order.id}</div></div><div class="order-status ${statusClass}">${statusText}</div></div>`;
                    }).join('');
                } else { ordersHtml = '<div class="empty-orders">📭 У вас пока нет заказов</div>'; }
                const reviewBtn = hasCompleted ? '<button class="write-review-btn" onclick="openReviewModal()">✍️ Оставить отзыв</button>' : '';
                container.innerHTML = `<div class="profile-card"><div class="avatar">${fullName.charAt(0).toUpperCase()}</div><div class="user-name">${fullName}</div><div class="user-id">ID: ${tgUser?.id || 'Неизвестно'}</div><div class="order-history"><h3 style="margin-bottom:15px;font-size:16px;">📋 История заказов</h3>${ordersHtml}${reviewBtn}</div></div>`;
            } catch (error) { container.innerHTML = '<div class="empty-orders">❌ Ошибка загрузки профиля</div>'; }
        }
        
        async function loadReviews() {
            const container = document.getElementById('reviews-content');
            container.innerHTML = '<div class="loading"><div class="spinner"></div>Загрузка...</div>';
            try {
                const response = await fetch('/get_reviews');
                const data = await response.json();
                if (data.reviews && data.reviews.length > 0) {
                    container.innerHTML = `<h3 style="margin-bottom:15px;">⭐ Отзывы покупателей</h3>` + data.reviews.map(r => {
                        const levelIcon = r.complexity === 'easy' ? '🟢' : r.complexity === 'medium' ? '🟡' : '🔴';
                        const levelName = r.complexity === 'easy' ? 'Лёгкий' : r.complexity === 'medium' ? 'Средний' : 'Сложный';
                        return `<div class="review-item"><div class="review-header"><span>👤 ${r.username || 'Пользователь'}</span><span>${levelIcon} ${levelName}</span></div><div class="review-text">${r.review}</div><div style="font-size:10px; color:#555; margin-top:8px;">${r.created_at.slice(0,10)}</div></div>`;
                    }).join('');
                } else {
                    container.innerHTML = '<div class="empty-orders">⭐ Пока нет отзывов. Будьте первым!</div>';
                }
            } catch (error) {
                container.innerHTML = '<div class="empty-orders">❌ Ошибка загрузки отзывов</div>';
            }
        }
        
        function openReviewModal() {
            if (!selectedLevel && currentStep === 'select') {
                showToast('❌ Сначала выберите уровень бота');
                return;
            }
            const level = LEVELS[selectedLevel];
            document.getElementById('reviewBotInfo').innerHTML = `${level.icon} ${level.name} бот`;
            document.getElementById('reviewModal').classList.add('active');
            document.getElementById('reviewText').value = '';
        }
        
        function closeReviewModal() {
            document.getElementById('reviewModal').classList.remove('active');
        }
        
        async function submitReview() {
            const reviewText = document.getElementById('reviewText').value.trim();
            if (!reviewText) {
                showToast('❌ Напишите текст отзыва!');
                return;
            }
            const btn = document.querySelector('#reviewModal .confirm-btn');
            btn.disabled = true;
            btn.textContent = '⏳ Отправка...';
            try {
                const response = await fetch('/submit_review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        user_id: tgUser?.id || 0,
                        username: tgUser?.username || tgUser?.first_name || 'user',
                        complexity: selectedLevel,
                        review: reviewText
                    })
                });
                const data = await response.json();
                if (data.success) {
                    closeReviewModal();
                    if (tg) tg.showAlert('✅ Спасибо за отзыв!');
                    else showToast('✅ Спасибо за отзыв!');
                    if (currentTab === 'reviews') loadReviews();
                } else {
                    btn.disabled = false;
                    btn.textContent = '📤 Отправить отзыв';
                    showToast('❌ Ошибка: ' + data.error);
                }
            } catch (error) {
                btn.disabled = false;
                btn.textContent = '📤 Отправить отзыв';
                showToast('❌ Ошибка: ' + error);
            }
        }
        
        function showToast(message) {
            const toast = document.createElement('div');
            toast.className = 'toast';
            toast.textContent = message;
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 2500);
        }
        
        renderShop();
    </script>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
</body>
</html>
'''

# Значения для подстановки в HTML
BANK_NAME_FOR_HTML = BANK_NAME
CARD_NUMBER_FOR_HTML = CARD_NUMBER
RECIPIENT_NAME_FOR_HTML = RECIPIENT_NAME

# ==================== FLASK ЭНДПОИНТЫ ====================
@flask_app.route('/')
def webapp():
    # Подставляем значения в HTML
    html = HTML_TEMPLATE.replace('${BANK_NAME}', BANK_NAME)
    html = html.replace('${CARD_NUMBER}', CARD_NUMBER)
    html = html.replace('${RECIPIENT_NAME}', RECIPIENT_NAME)
    return html

@flask_app.route('/create_order', methods=['POST'])
def create_order():
    try:
        data = request.get_json()
        complexity = data.get('complexity')
        user_id = data.get('user_id', 0)
        username = data.get('username', '')
        prices = {"easy": 99, "medium": 249, "hard": 499}
        price = prices.get(complexity, 99)
        order_id = save_order(user_id, username or "webapp_user", complexity, price)
        
        asyncio.run_coroutine_threadsafe(
            bot.send_message(
                ADMIN_ID,
                f"🆕 *Новый заказ!*\n\n"
                f"📦 Заказ #{order_id}\n"
                f"👤 Пользователь: {username or user_id}\n"
                f"🎚️ Уровень: {complexity}\n"
                f"💰 Сумма: {price}₽\n"
                f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                f"📌 Ожидайте скриншот от пользователя",
                parse_mode="Markdown"
            ),
            asyncio.get_event_loop()
        )
        
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@flask_app.route('/get_orders', methods=['GET'])
def get_orders():
    try:
        user_id = request.args.get('user_id', 0, type=int)
        orders = get_user_orders(user_id)
        orders_list = [{"id": o[0], "complexity": o[3], "price": o[4], "status": o[6], "created_at": o[7]} for o in orders]
        return jsonify({"success": True, "orders": orders_list})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@flask_app.route('/get_reviews', methods=['GET'])
def get_reviews_endpoint():
    try:
        complexity = request.args.get('complexity')
        reviews = get_reviews(complexity)
        reviews_list = [{"id": r[0], "username": r[2], "complexity": r[3], "review": r[4], "created_at": r[5]} for r in reviews]
        return jsonify({"success": True, "reviews": reviews_list})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

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
        
        level_name = "Лёгкий" if complexity == "easy" else "Средний" if complexity == "medium" else "Сложный"
        level_icon = "🟢" if complexity == "easy" else "🟡" if complexity == "medium" else "🔴"
        
        asyncio.run_coroutine_threadsafe(
            bot.send_message(
                ADMIN_ID,
                f"⭐ *Новый отзыв!*\n\n"
                f"👤 Пользователь: @{username or user_id}\n"
                f"{level_icon} {level_name} бот\n"
                f"📝 Отзыв: {review[:200]}",
                parse_mode="Markdown"
            ),
            asyncio.get_event_loop()
        )
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== КОМАНДЫ БОТА ====================
@dp.message(Command("start"))
async def start(message: types.Message):
    # Получаем внешний URL для WebApp
    web_app_url = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not web_app_url:
        web_app_url = "https://" + request.host if hasattr(flask_app, 'request_context') else "https://your-domain.onrender.com"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Открыть магазин", web_app=WebAppInfo(url=f"https://{web_app_url}/" if "onrender" in web_app_url else web_app_url))],
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton(text="⭐ Отзывы", callback_data="show_reviews")]
    ])
    await message.answer(
        "🤖 *BotMarket* — магазин Telegram ботов\n\n"
        "💰 *Цены:*\n"
        "🟢 Лёгкий — 99₽\n"
        "🟡 Средний — 249₽\n"
        "🔴 Сложный — 499₽\n\n"
        "👇 Нажмите на кнопку ниже, чтобы открыть магазин",
        parse_mode="Markdown",
        reply_markup=kb
    )

@dp.callback_query(F.data == "my_orders")
async def my_orders(callback: types.CallbackQuery):
    orders = get_user_orders(callback.from_user.id)
    if not orders:
        await callback.message.answer("📭 У вас пока нет заказов")
    else:
        text = "📦 *Ваши заказы*\n\n"
        for order in orders:
            status_emoji = "✅" if order[6] == "completed" else "⏳" if order[6] == "pending" else "❌"
            level_name = "Лёгкий" if order[3] == "easy" else "Средний" if order[3] == "medium" else "Сложный"
            text += f"{status_emoji} #{order[0]} | {level_name} | {order[4]}₽ | {order[7][:16]}\n"
        await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "show_reviews")
async def show_reviews(callback: types.CallbackQuery):
    reviews = get_reviews(limit=10)
    if not reviews:
        await callback.message.answer("⭐ Пока нет отзывов. Будьте первым!")
    else:
        text = "⭐ *Последние отзывы:*\n\n"
        for r in reviews:
            level_icon = "🟢" if r[3] == "easy" else "🟡" if r[3] == "medium" else "🔴"
            level_name = "Лёгкий" if r[3] == "easy" else "Средний" if r[3] == "medium" else "Сложный"
            text += f"{level_icon} *{level_name}* — @{r[2] or r[1]}\n"
            text += f"💬 {r[4][:100]}\n"
            text += f"📅 {r[5][:10]}\n\n"
        await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

# ==================== ОТПРАВКА СКРИНШОТА ====================
@dp.message(Command("pay"))
async def pay_command(message: types.Message, state: FSMContext):
    pending_order = get_pending_order_for_user(message.from_user.id)
    if not pending_order:
        await message.answer("❌ У вас нет заказов на оплату. Сначала оформите заказ в магазине.")
        return
    
    order_id = pending_order[0]
    price = pending_order[4]
    complexity = pending_order[3]
    level_name = "Лёгкий" if complexity == "easy" else "Средний" if complexity == "medium" else "Сложный"
    level_icon = "🟢" if complexity == "easy" else "🟡" if complexity == "medium" else "🔴"
    
    await state.update_data(order_id=order_id)
    await state.set_state(PaymentForm.waiting_for_screenshot)
    
    await message.answer(
        f"💳 *Оплата заказа #{order_id}*\n\n"
        f"{level_icon} *{level_name}* бот — *{price}*₽\n\n"
        f"📌 *Реквизиты для оплаты:*\n"
        f"🏦 Банк: `{BANK_NAME}`\n"
        f"💳 Карта: `{CARD_NUMBER}`\n"
        f"👤 Получатель: `{RECIPIENT_NAME}`\n\n"
        f"📸 *После оплаты отправьте СКРИНШОТ чека сюда* (фото из приложения Т-Банка)\n\n"
        f"✅ Скриншот должен быть четким, видна сумма и дата перевода.",
        parse_mode="Markdown"
    )

@dp.message(StateFilter(PaymentForm.waiting_for_screenshot), F.photo)
async def handle_screenshot(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get('order_id')
    
    if not order_id:
        pending_order = get_pending_order_for_user(message.from_user.id)
        if not pending_order:
            await message.answer("❌ У вас нет заказов на оплату.")
            await state.clear()
            return
        order_id = pending_order[0]
    
    order = get_order(order_id)
    if not order:
        await message.answer("❌ Заказ не найден.")
        await state.clear()
        return
    
    if order[1] != message.from_user.id:
        await message.answer("❌ Это не ваш заказ.")
        await state.clear()
        return
    
    file_id = message.photo[-1].file_id
    update_order_screenshot(order_id, file_id)
    
    complexity = order[3]
    price = order[4]
    level_name = "Лёгкий" if complexity == "easy" else "Средний" if complexity == "medium" else "Сложный"
    level_icon = "🟢" if complexity == "easy" else "🟡" if complexity == "medium" else "🔴"
    
    await message.answer(
        f"📸 *Скриншот для заказа #{order_id} получен!*\n\n"
        f"{level_icon} {level_name} — {price}₽\n\n"
        f"✅ Администратор проверит оплату в ближайшее время.\n"
        f"Статус заказа можно отслеживать в разделе «Профиль».",
        parse_mode="Markdown"
    )
    
    # Отправляем админу с закреплением сообщения
    admin_msg = await bot.send_photo(
        ADMIN_ID,
        photo=file_id,
        caption=f"🆕 *Поступил чек для заказа #{order_id}*\n\n"
                f"👤 Пользователь: @{message.from_user.username or message.from_user.id}\n"
                f"{level_icon} {level_name} — {price}₽\n\n"
                f"📌 Ожидаемая карта: `{CARD_NUMBER}`\n"
                f"👤 Ожидаемый получатель: `{RECIPIENT_NAME}`\n\n"
                f"✅ Подтвердить оплату?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{order_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{order_id}")]
        ])
    )
    
    # Закрепляем сообщение с чеком
    try:
        await bot.pin_chat_message(ADMIN_ID, admin_msg.message_id)
    except Exception as e:
        logger.error(f"Не удалось закрепить сообщение: {e}")
    
    await state.clear()

@dp.message(StateFilter(PaymentForm.waiting_for_screenshot))
async def wrong_input(message: types.Message):
    await message.answer("❌ Пожалуйста, отправьте ФОТО (скриншот чека).")

# ==================== АДМИН-ПАНЕЛЬ ====================
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён")
        return
    
    orders = get_all_orders()
    
    if not orders:
        await message.answer("📭 Нет заказов")
        return
    
    text = "📋 *Все заказы:*\n\n"
    for o in orders:
        status_emoji = "🟡" if o[6] == "pending" else "✅" if o[6] == "completed" else "❌"
        level_name = "Лёгкий" if o[3] == "easy" else "Средний" if o[3] == "medium" else "Сложный"
        status_text = "На проверке" if o[6] == "pending" else "Выполнен" if o[6] == "completed" else "Отклонён"
        screenshot_status = "📸 Есть" if o[5] else "📭 Нет"
        text += f"{status_emoji} *#{o[0]}* | {level_name} | {o[4]}₽ | {status_text} | {screenshot_status}\n"
        text += f"└ 👤 @{o[2] or o[1]} | {o[7][:16]}\n\n"
    
    await message.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_orders")]
    ]))

@dp.callback_query(F.data == "admin_orders")
async def admin_orders_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    
    orders = get_all_orders()
    
    text = "📋 *Все заказы:*\n\n"
    for o in orders:
        status_emoji = "🟡" if o[6] == "pending" else "✅" if o[6] == "completed" else "❌"
        level_name = "Лёгкий" if o[3] == "easy" else "Средний" if o[3] == "medium" else "Сложный"
        status_text = "На проверке" if o[6] == "pending" else "Выполнен" if o[6] == "completed" else "Отклонён"
        screenshot_status = "📸 Есть" if o[5] else "📭 Нет"
        text += f"{status_emoji} *#{o[0]}* | {level_name} | {o[4]}₽ | {status_text} | {screenshot_status}\n"
        text += f"└ 👤 @{o[2] or o[1]} | {o[7][:16]}\n\n"
    
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_orders")]
    ]))
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_order(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    
    order_id = int(callback.data.split("_")[1])
    update_order_status(order_id, "completed")
    order = get_order(order_id)
    
    if order:
        user_id = order[1]
        level_name = "Лёгкий" if order[3] == "easy" else "Средний" if order[3] == "medium" else "Сложный"
        level_icon = "🟢" if order[3] == "easy" else "🟡" if order[3] == "medium" else "🔴"
        
        await bot.send_message(
            user_id,
            f"✅ *Ваш заказ #{order_id} подтверждён!*\n\n"
            f"{level_icon} {level_name} бот — {order[4]}₽\n\n"
            f"Спасибо за покупку! Администратор свяжется с вами в ближайшее время.\n\n"
            f"✍️ Вы можете оставить отзыв в разделе «Профиль».",
            parse_mode="Markdown"
        )
    
    await callback.message.edit_text(f"✅ Заказ #{order_id} подтверждён!\n\nПользователь уведомлён.")
    await callback.answer("Заказ подтверждён", show_alert=True)

@dp.callback_query(F.data.startswith("reject_"))
async def reject_order(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    
    order_id = int(callback.data.split("_")[1])
    update_order_status(order_id, "rejected")
    order = get_order(order_id)
    
    if order:
        user_id = order[1]
        await bot.send_message(
            user_id,
            f"❌ *Ваш заказ #{order_id} отклонён*\n\n"
            f"Причина: чек не соответствует оплате или нечитаем.\n"
            f"Пожалуйста, оформите заказ заново в магазине.",
            parse_mode="Markdown"
        )
    
    await callback.message.edit_text(f"❌ Заказ #{order_id} отклонён.\n\nПользователь уведомлён.")
    await callback.answer("Заказ отклонён", show_alert=True)

# ==================== ЗАПУСК ====================
async def main():
    # Запускаем Flask в отдельном потоке
    def run_flask():
        flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
    
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Запускаем бота
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info(f"🤖 Бот магазина запущен! Админ: {ADMIN_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
