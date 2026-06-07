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

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ================= DB =================
DB = "shop.db"

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init():
    conn = db()
    c = conn.cursor()

    c.execute("""
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

    c.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
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

# ================= CONFIG SHOP =================
LEVELS = {
    "easy": {"name": "Лёгкий", "price": 99, "icon": "🟢"},
    "medium": {"name": "Средний", "price": 249, "icon": "🟡"},
    "hard": {"name": "Сложный", "price": 499, "icon": "🔴"}
}

CARD = "2200 7000 0000 0000"
BANK = "Т-Банк"
OWNER = "Мухаммад А"

# ================= DB FUNCTIONS =================
def create_order(user_id, username, level, screenshot):
    conn = db()
    c = conn.cursor()
    price = LEVELS[level]["price"]
    c.execute("""
        INSERT INTO orders (user_id, username, level, price, screenshot, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, level, price, screenshot, datetime.now().isoformat()))
    conn.commit()
    oid = c.lastrowid
    conn.close()
    return oid

def get_orders(user_id):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def save_review(user_id, username, level, text):
    conn = db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO reviews (user_id, username, level, text, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, username, level, text, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_reviews():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT 50")
    rows = c.fetchall()
    conn.close()
    return rows

# ================= TELEGRAM =================
@bot.message_handler(commands=["start"])
def start(m):
    url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/"

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton(
            "🛒 Открыть магазин",
            web_app=WebAppInfo(url=url)
        )
    )

    bot.send_message(
        m.chat.id,
        "🤖 BotShop PRO MAX\n\nВыберите магазин:",
        reply_markup=kb
    )

@bot.message_handler(commands=["admin"])
def admin(m):
    if m.from_user.id != ADMIN_ID:
        return bot.send_message(m.chat.id, "⛔ Нет доступа")

    conn = db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM orders")
    total = c.fetchone()[0]

    bot.send_message(m.chat.id, f"📊 Админ\nВсего заказов: {total}")

# ================= FLASK =================
@app.route("/")
def index():
    return open("index.html", encoding="utf-8").read()

@app.route("/create_order", methods=["POST"])
def create():
    data = request.json

    user_id = data.get("user_id")
    username = data.get("username", "")
    level = data.get("level")
    screenshot = data.get("screenshot")

    if not user_id or not level:
        return jsonify({"ok": False})

    order_id = create_order(user_id, username, level, screenshot)

    level_data = LEVELS[level]

    bot.send_message(
        ADMIN_ID,
        f"🆕 Заказ #{order_id}\n"
        f"👤 {username}\n"
        f"{level_data['icon']} {level_data['name']}\n"
        f"💰 {level_data['price']}₽"
    )

    return jsonify({"ok": True})

@app.route("/orders")
def orders():
    uid = request.args.get("user_id")
    rows = get_orders(uid)

    return jsonify([
        {
            "id": r["id"],
            "level": r["level"],
            "price": r["price"],
            "status": r["status"],
            "created_at": r["created_at"]
        } for r in rows
    ])

@app.route("/reviews")
def reviews():
    rows = get_reviews()
    return jsonify([
        {
            "username": r["username"],
            "text": r["text"],
            "level": r["level"]
        } for r in rows
    ])

@app.route("/review", methods=["POST"])
def review():
    d = request.json
    save_review(
        d["user_id"],
        d.get("username", ""),
        d.get("level", ""),
        d.get("text", "")
    )
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
body{margin:0;font-family:sans-serif;background:#0f0f1a;color:white}
.card{margin:10px;padding:15px;background:#1c1c2e;border-radius:15px}
.btn{padding:12px;width:100%;border:none;border-radius:10px;background:#6c5ce7;color:white}
.tab{display:none}
.active{display:block}
</style>
</head>
<body>

<div class="card">
<h2>🛒 BotShop</h2>
<button onclick="tab('shop')" class="btn">Магазин</button>
<button onclick="tab('profile')" class="btn">Профиль</button>
<button onclick="tab('reviews')" class="btn">Отзывы</button>
</div>

<div id="shop" class="tab active">
<div class="card">
<h3>Лёгкий - 99₽</h3>
<button onclick="buy('easy')" class="btn">Купить</button>
</div>

<div class="card">
<h3>Средний - 249₽</h3>
<button onclick="buy('medium')" class="btn">Купить</button>
</div>

<div class="card">
<h3>Сложный - 499₽</h3>
<button onclick="buy('hard')" class="btn">Купить</button>
</div>
</div>

<div id="profile" class="tab"></div>
<div id="reviews" class="tab"></div>

<script>
let tg = window.Telegram.WebApp;
tg.expand();

let user = tg.initDataUnsafe.user;

function tab(id){
 document.querySelectorAll(".tab").forEach(e=>e.classList.remove("active"))
 document.getElementById(id).classList.add("active")
}

async function buy(level){
 let screenshot = prompt("Вставь base64 скрин (или оставь пусто)");

 await fetch("/create_order",{
  method:"POST",
  headers:{"Content-Type":"application/json"},
  body:JSON.stringify({
   user_id:user.id,
   username:user.username,
   level:level,
   screenshot:screenshot
  })
 });

 alert("Заказ отправлен!");
}

</script>

</body>
</html>
"""

@app.route("/webapp.html")
def webapp():
    return HTML

# ================= RUN =================
def run():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run).start()
    bot.infinity_polling()
