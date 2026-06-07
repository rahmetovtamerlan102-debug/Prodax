#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import threading
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
def add_order(user_id, username, level):
    c = conn()
    cur = c.cursor()
    price = LEVELS[level]["price"]

    cur.execute("""
        INSERT INTO orders (user_id, username, level, price, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, username, level, price, datetime.now().isoformat()))

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

# ================= TELEGRAM =================
@bot.message_handler(commands=["start"])
def start(m):
    url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webapp"

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("🛒 Открыть магазин", web_app=WebAppInfo(url=url))
    )

    bot.send_message(
        m.chat.id,
        "🤖 BotShop PRO MAX\nНажми кнопку ниже 👇",
        reply_markup=kb
    )

@bot.message_handler(commands=["admin"])
def admin(m):
    if m.from_user.id != ADMIN_ID:
        return bot.send_message(m.chat.id, "⛔ Нет доступа")

    c = conn()
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM orders")
    total = cur.fetchone()[0]
    c.close()

    bot.send_message(m.chat.id, f"📊 Всего заказов: {total}")

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

    if not user_id or not level:
        return jsonify({"ok": False})

    order_id = add_order(user_id, username, level)

    bot.send_message(
        ADMIN_ID,
        f"🆕 Заказ #{order_id}\n👤 {username}\n📦 {level}"
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
            "text": r["text"]
        } for r in rows
    ])

@app.route("/review", methods=["POST"])
def review():
    d = request.json
    add_review(d["user_id"], d["username"], d["text"])
    return jsonify({"ok": True})

# ================= WEBAPP =================
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
.btn{padding:12px;width:100%;border:none;border-radius:10px;background:#6c5ce7;color:white;margin-top:5px}
</style>
</head>
<body>

<div class="card">
<h2>🛒 BotShop</h2>
<button class="btn" onclick="buy('easy')">Лёгкий 99₽</button>
<button class="btn" onclick="buy('medium')">Средний 249₽</button>
<button class="btn" onclick="buy('hard')">Сложный 499₽</button>
</div>

<script>
let tg = window.Telegram.WebApp;
tg.expand();

let user = tg.initDataUnsafe.user;

async function buy(level){
    let r = await fetch("/create_order", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
            user_id:user.id,
            username:user.username,
            level:level
        })
    });

    let d = await r.json();

    if(d.ok){
        tg.showAlert("Заказ создан!");
    } else {
        tg.showAlert("Ошибка");
    }
}
</script>

</body>
</html>
"""

# ================= RUN =================
def run():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run).start()
    bot.infinity_polling()
