#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import threading
import base64
from flask import Flask, request, jsonify
from datetime import datetime
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

PRICES = {
    "easy": 99,
    "medium": 249,
    "hard": 499
}

# ================= DB =================
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
    conn.commit()
    conn.close()

init()

def save(user_id, username, level, price, screenshot):
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
    INSERT INTO orders(user_id, username, level, price, screenshot, created_at)
    VALUES(?,?,?,?,?,?)
    """, (user_id, username, level, price, screenshot, datetime.now().strftime("%d.%m %H:%M")))

    oid = c.lastrowid
    conn.commit()
    conn.close()
    return oid


# ================= WEB UI =================
HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bot Market</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>
body{
    margin:0;
    font-family:Arial;
    background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);
    color:white;
}

.container{
    padding:15px;
}

.card{
    background:rgba(255,255,255,0.08);
    padding:18px;
    margin:12px 0;
    border-radius:18px;
    backdrop-filter: blur(10px);
    transition:0.2s;
}

.card:active{ transform:scale(0.98); }

.title{
    font-size:22px;
    font-weight:bold;
}

.price{
    float:right;
    color:#a855f7;
    font-weight:bold;
}

.btn{
    width:100%;
    padding:14px;
    border:none;
    border-radius:25px;
    margin-top:10px;
    font-size:16px;
    font-weight:bold;
}

.btn-primary{
    background:linear-gradient(135deg,#a855f7,#7c3aed);
    color:white;
}

.btn-success{
    background:linear-gradient(135deg,#22c55e,#16a34a);
    color:white;
}

.upload{
    border:2px dashed #a855f7;
    padding:15px;
    text-align:center;
    border-radius:16px;
    margin-top:15px;
}

.selected{
    border:2px solid #a855f7;
    background:rgba(168,85,247,0.2);
}
</style>
</head>

<body>
<div class="container">

<h2>🛒 Bot Market</h2>

<div id="easy" class="card" onclick="select('easy')">
🟢 <b>Easy</b>
<span class="price">99₽</span>
</div>

<div id="medium" class="card" onclick="select('medium')">
🟡 <b>Medium</b>
<span class="price">249₽</span>
</div>

<div id="hard" class="card" onclick="select('hard')">
🔴 <b>Hard</b>
<span class="price">499₽</span>
</div>

<button class="btn btn-primary" onclick="nextStep()">
📦 Заказать
</button>

<div id="pay" style="display:none">

<h3>💳 Оплата</h3>
<p>Переведите оплату и прикрепите скрин</p>

<div class="upload">
<input type="file" id="file" accept="image/*" onchange="loadImg(event)">
<p>📸 Загрузить скрин</p>
</div>

<button class="btn btn-success" onclick="send()">
✅ Отправить заказ
</button>

</div>

</div>

<script>
const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

let level = "easy";
let img = null;

function select(l){
    level = l;

    document.querySelectorAll('.card').forEach(c=>c.classList.remove('selected'));
    document.getElementById(l).classList.add('selected');
}

function nextStep(){
    document.getElementById("pay").style.display="block";
}

function loadImg(e){
    let file = e.target.files[0];
    let reader = new FileReader();
    reader.onload = function(){
        img = reader.result;
    }
    reader.readAsDataURL(file);
}

async function send(){
    const user = tg?.initDataUnsafe?.user;

    if(!user){
        alert("Открой через Telegram");
        return;
    }

    const res = await fetch("/create_order",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
            user_id:user.id,
            username:user.username || "",
            level:level,
            screenshot:img
        })
    });

    const data = await res.json();

    if(data.success){
        alert("✅ Заказ отправлен!");
    }else{
        alert("❌ Ошибка");
    }
}
</script>

</body>
</html>
"""


@app.route("/")
def home():
    return HTML


@app.route("/create_order", methods=["POST"])
def create():
    data = request.get_json()

    user_id = data["user_id"]
    username = data.get("username","")
    level = data.get("level","easy")
    screenshot = data.get("screenshot")

    price = PRICES[level]

    display = f"@{username}" if username else f"ID:{user_id}"

    oid = save(user_id, display, level, price, screenshot)

    bot.send_message(
        ADMIN_ID,
        f"🆕 ЗАКАЗ #{oid}\n\n"
        f"👤 {display}\n"
        f"⚙️ {level}\n"
        f"💰 {price}₽"
    )

    return jsonify({"success":True})


@bot.message_handler(commands=['start'])
def start(m):
    url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME','localhost')}/"

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛒 Магазин", web_app=WebAppInfo(url=url)))

    bot.send_message(m.chat.id,"Открой магазин 👇",reply_markup=kb)


def run():
    app.run(host="0.0.0.0", port=PORT)

if __name__=="__main__":
    threading.Thread(target=run).start()
    bot.infinity_polling()
