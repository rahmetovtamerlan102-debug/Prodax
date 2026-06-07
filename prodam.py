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

# =========================
# ⚙️ CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8276815852"))
PORT = int(os.getenv("PORT", "8080"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# 💳 T-BANK REQUISITES
BANK_NAME = "Т-Банк"
CARD_NUMBER = "2200702150754195"
RECIPIENT = "Мухаммад А"

# 💰 PRICES
PRICES = {
    "easy": 99,
    "medium": 249,
    "hard": 499
}

LEVEL_INFO = {
    "easy": {
        "title": "🟢 EASY",
        "desc": "Базовый бот без нагрузки",
        "features": ["Простой функционал", "Быстрая сборка"]
    },
    "medium": {
        "title": "🟡 MEDIUM",
        "desc": "Расширенный функционал",
        "features": ["База данных", "Админка"]
    },
    "hard": {
        "title": "🔴 HARD",
        "desc": "PRO система уровня SaaS",
        "features": ["WebApp UI", "Статистика", "Отзывы", "Панель"]
    }
}

# =========================
# 🗄 DATABASE
# =========================

DB = "shop.db"


def db():
    return sqlite3.connect(DB)


def init_db():
    conn = db()
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
        text TEXT,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()


init_db()

# =========================
# 📦 DB FUNCTIONS
# =========================

def create_order(user_id, username, level, screenshot):
    conn = db()
    c = conn.cursor()

    price = PRICES[level]

    c.execute("""
    INSERT INTO orders(user_id, username, level, price, screenshot, status, created_at)
    VALUES(?,?,?,?,?,'pending',?)
    """, (user_id, username, level, price, screenshot, datetime.now().isoformat()))

    conn.commit()
    oid = c.lastrowid
    conn.close()
    return oid


def get_orders(user_id):
    conn = db()
    c = conn.cursor()
    rows = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
    conn.close()
    return rows


def get_order(order_id):
    conn = db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    conn.close()
    return row


def set_status(order_id, status):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    conn.commit()
    conn.close()


def add_review(uid, username, text):
    conn = db()
    c = conn.cursor()
    c.execute("""
    INSERT INTO reviews(user_id, username, text, created_at)
    VALUES(?,?,?,?)
    """, (uid, username, text, datetime.now().isoformat()))
    conn.commit()
    conn.close()


# =========================
# 🌐 WEB APP (UI)
# =========================

HTML = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>Pro Market</title>

<style>
body {{
    margin:0;
    font-family:sans-serif;
    background: linear-gradient(135deg,#0f0c29,#302b63,#24243e);
    color:white;
}}

.container {{ padding:15px; }}

.card {{
    background: rgba(255,255,255,0.06);
    backdrop-filter: blur(10px);
    border-radius:20px;
    padding:15px;
    margin:10px 0;
    transition:0.2s;
}}

.card:hover {{
    transform: scale(1.02);
}}

.btn {{
    width:100%;
    padding:12px;
    border:none;
    border-radius:12px;
    margin-top:10px;
}}

.primary {{
    background:#7c3aed;
    color:white;
}}

.success {{
    background:#22c55e;
    color:white;
}}

.danger {{
    background:#ef4444;
    color:white;
}}

.price {{
    font-size:22px;
    color:#ffd700;
}}

.payment {{
    display:none;
}}

.code {{
    background:black;
    padding:10px;
    border-radius:10px;
    text-align:center;
    cursor:pointer;
}}

img {{
    width:100%;
    border-radius:10px;
    margin-top:10px;
}}
</style>

</head>

<body>

<div class="container">

<h2>🛒 PRO MARKET</h2>

<div id="shop"></div>

<div id="payment" class="payment">

<div class="card">

<h3>💳 Оплата Т-Банк</h3>

<div class="price" id="price"></div>

<div class="code" onclick="copyCard()">
{CARD_NUMBER}
</div>

<p>👤 {RECIPIENT}</p>
<p>🏦 {BANK_NAME}</p>

<input type="file" id="file" accept="image/*" onchange="loadImg()">

<div id="preview"></div>

<button class="btn success" id="payBtn" disabled onclick="send()">Я оплатил</button>
<button class="btn danger" onclick="back()">Назад</button>

</div>

</div>

</div>

<script>
const tg = window.Telegram.WebApp;
tg.ready();

let level=null;
let screenshot=null;

const levels = {PRICES};

function render(){{
    let html="";
    for(let k in levels){{
        html+=`
        <div class="card">
            <h3>{'{'}k{'}'}</h3>
            <div class="price">${{levels[k]}}₽</div>
            <button class="btn primary" onclick="buy('${{k}}')">Выбрать</button>
        </div>`;
    }}
    document.getElementById("shop").innerHTML=html;
}}

function buy(l){{
    level=l;
    document.getElementById("shop").style.display="none";
    document.getElementById("payment").style.display="block";
    document.getElementById("price").innerText=levels[l]+"₽";
}}

function back(){{
    document.getElementById("shop").style.display="block";
    document.getElementById("payment").style.display="none";
}}

function copyCard(){{
    navigator.clipboard.writeText("{CARD_NUMBER}");
    tg.showAlert("Скопировано");
}}

function loadImg(){{
    let f=document.getElementById("file").files[0];
    let r=new FileReader();
    r.onload=()=>{
        screenshot=r.result;
        document.getElementById("preview").innerHTML=
        "<img src='"+screenshot+"'>";
        document.getElementById("payBtn").disabled=false;
    }
    r.readAsDataURL(f);
}}

function send(){{
    let u=tg.initDataUnsafe.user;

    fetch("/create_order", {{
        method:"POST",
        headers:{{"Content-Type":"application/json"}},
        body:JSON.stringify({{
            user_id:u.id,
            username:u.username,
            level:level,
            screenshot:screenshot
        }})
    }});

    tg.showAlert("Заказ отправлен");
    back();
}}

render();
</script>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

</body>
</html>
"""

# =========================
# 🌍 ROUTES
# =========================

@app.route("/")
def home():
    return HTML


@app.route("/create_order", methods=["POST"])
def create():
    data = request.json

    oid = create_order(
        data["user_id"],
        data.get("username","user"),
        data["level"],
        data.get("screenshot")
    )

    bot.send_message(
        ADMIN_ID,
        f"🆕 ORDER #{oid}\n👤 {data.get('username')}\n📦 {data['level']}"
    )

    return jsonify({"ok":True})


@app.route("/get_orders")
def orders():
    uid = int(request.args.get("user_id"))
    return jsonify(get_orders(uid))


# =========================
# 🤖 TELEGRAM
# =========================

@bot.message_handler(commands=["start"])
def start(m):
    url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/"

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 OPEN SHOP", web_app=WebAppInfo(url=url)))

    bot.send_message(m.chat.id, "PRO MARKET READY", reply_markup=kb)


# =========================
# 🚀 RUN
# =========================

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

threading.Thread(target=run_flask).start()
bot.infinity_polling()
