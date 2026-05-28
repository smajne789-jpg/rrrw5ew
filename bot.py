import asyncio
import logging
import random
import sqlite3
from datetime import datetime, timedelta

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTO_TOKEN = os.getenv("CRYPTO_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
BOT_USERNAME = os.getenv("BOT_USERNAME")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher(storage=MemoryStorage())

logging.basicConfig(level=logging.INFO)

db = sqlite3.connect("database.db")
cursor = db.cursor()

# ================= DB =================

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance REAL DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS giveaways(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    prize REAL,
    ticket_price REAL,
    end_time TEXT,
    active INTEGER DEFAULT 1,
    message_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tickets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    giveaway_id INTEGER,
    user_id INTEGER,
    ticket_number INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS invoices(
    invoice_id TEXT,
    user_id INTEGER,
    giveaway_id INTEGER,
    amount REAL,
    ticket_count INTEGER,
    paid INTEGER DEFAULT 0
)
""")

db.commit()

# ================= KEYBOARDS =================

main_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="profile")
        ],
        [
            InlineKeyboardButton(text="💰 Пополнить", callback_data="deposit"),
            InlineKeyboardButton(text="📤 Вывод", callback_data="withdraw")
        ]
    ]
)

admin_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🎁 Создать розыгрыш", callback_data="create_give")
        ],
        [
            InlineKeyboardButton(text="➕ Начислить баланс", callback_data="add_balance")
        ],
        [
            InlineKeyboardButton(text="➖ Снять баланс", callback_data="remove_balance")
        ]
    ]
)

# ================= FUNCTIONS =================

def create_user(user_id, username):
    cursor.execute(
        "INSERT OR IGNORE INTO users(user_id, username) VALUES(?, ?)",
        (user_id, username)
    )
    db.commit()

def get_balance(user_id):
    cursor.execute(
        "SELECT balance FROM users WHERE user_id=?",
        (user_id,)
    )
    result = cursor.fetchone()

    if result:
        return result[0]

    return 0

def update_balance(user_id, amount):
    cursor.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (amount, user_id)
    )
    db.commit()

async def create_invoice(amount):
    url = "https://pay.crypt.bot/api/createInvoice"

    headers = {
        "Crypto-Pay-API-Token": CRYPTO_TOKEN
    }

    payload = {
        "asset": "USDT",
        "amount": amount
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            return data

async def check_invoice(invoice_id):
    url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"

    headers = {
        "Crypto-Pay-API-Token": CRYPTO_TOKEN
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return data

# ================= START =================

@dp.message(CommandStart())
async def start(message: Message):
    create_user(message.from_user.id, message.from_user.username)

    text = f"""
<b>🎁 Giveaway Bot</b>

Добро пожаловать в лучшего бота для розыгрышей.

💎 Покупай билеты
💰 Пополняй баланс
🏆 Выигрывай призы

Ваш ID: <code>{message.from_user.id}</code>
"""

    if message.from_user.id == ADMIN_ID:
        await message.answer(text, reply_markup=admin_kb)
    else:
        await message.answer(text, reply_markup=main_kb)

# ================= PROFILE =================

@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    balance = get_balance(callback.from_user.id)

    text = f"""
👤 <b>Ваш профиль</b>

🆔 ID: <code>{callback.from_user.id}</code>
💰 Баланс: <b>{balance}$</b>
"""

    await callback.message.answer(text)

# ================= DEPOSIT =================

@dp.callback_query(F.data == "deposit")
async def deposit(callback: CallbackQuery):
    await callback.message.answer(
        "💰 Введите сумму пополнения\n\nМинимум: 0.1$"
    )

    dp.message.register(process_deposit)

async def process_deposit(message: Message):
    try:
        amount = float(message.text)

        if amount < 0.1:
            return await message.answer("❌ Минимум 0.1$")

        invoice = await create_invoice(amount)

        if not invoice["ok"]:
            return await message.answer("❌ Ошибка создания счета")

        pay_url = invoice["result"]["pay_url"]
        invoice_id = invoice["result"]["invoice_id"]

        await message.answer(
            f"""
💰 Счет создан

Сумма: <b>{amount}$</b>

Оплатите:
{pay_url}
"""
        )

        while True:
            await asyncio.sleep(5)

            status = await check_invoice(invoice_id)

            try:
                paid = status["result"]["items"][0]["status"]

                if paid == "paid":
                    update_balance(message.from_user.id, amount)

                    await message.answer(
                        f"✅ Баланс пополнен на {amount}$"
                    )
                    break

            except:
                pass

    except:
        await message.answer("❌ Ошибка")

# ================= WITHDRAW =================

@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: CallbackQuery):
    await callback.message.answer(
        "📤 Введите сумму вывода"
    )

    dp.message.register(process_withdraw)

async def process_withdraw(message: Message):
    try:
        amount = float(message.text)

        balance = get_balance(message.from_user.id)

        if amount > balance:
            return await message.answer("❌ Недостаточно средств")

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить вывод",
                        callback_data=f"confirm_withdraw:{message.from_user.id}:{amount}"
                    )
                ]
            ]
        )

        await bot.send_message(
            ADMIN_ID,
            f"""
📤 Новая заявка на вывод

👤 @{message.from_user.username}
🆔 <code>{message.from_user.id}</code>

💰 Сумма: {amount}$
""",
            reply_markup=kb
        )

        await message.answer(
            "⏳ Заявка отправлена администратору"
        )

    except:
        await message.answer("❌ Ошибка")

@dp.callback_query(F.data.startswith("confirm_withdraw"))
async def confirm_withdraw(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    _, user_id, amount = callback.data.split(":")

    user_id = int(user_id)
    amount = float(amount)

    update_balance(user_id, -amount)

    await bot.send_message(
        user_id,
        f"✅ Вывод {amount}$ успешно обработан"
    )

    await callback.message.edit_text(
        "✅ Вывод подтвержден"
    )

# ================= CREATE GIVEAWAY =================

@dp.callback_query(F.data == "create_give")
async def create_give(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    await callback.message.answer(
        """
🎁 Отправьте данные:

Название
Сумма приза
Время в минутах

Пример:
iPhone 15
100
60
"""
    )

    dp.message.register(process_giveaway)

async def process_giveaway(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        lines = message.text.split("\n")

        title = lines[0]
        prize = float(lines[1])
        minutes = int(lines[2])

        end_time = datetime.now() + timedelta(minutes=minutes)

        cursor.execute("""
        INSERT INTO giveaways(title, prize, ticket_price, end_time)
        VALUES(?, ?, ?, ?)
        """, (
            title,
            prize,
            0.1,
            end_time.strftime("%Y-%m-%d %H:%M:%S")
        ))

        db.commit()

        give_id = cursor.lastrowid

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎟 Участвовать",
                        url=f"https://t.me/{BOT_USERNAME}?start=give_{give_id}"
                    )
                ]
            ]
        )

        msg = await bot.send_message(
            CHANNEL_ID,
            f"""
🎁 <b>Новый розыгрыш!</b>

🏆 Приз: <b>{title}</b>
💰 Сумма: <b>{prize}$ + банк</b>

🎟 Цена билета: <b>0.1$</b>

⏳ Конец:
<code>{end_time}</code>
""",
            reply_markup=kb
        )

        cursor.execute(
            "UPDATE giveaways SET message_id=? WHERE id=?",
            (msg.message_id, give_id)
        )

        db.commit()

        await message.answer("✅ Розыгрыш создан")

    except Exception as e:
        await message.answer(f"❌ Ошибка\n{e}")

# ================= BUY TICKETS =================

@dp.message(F.text.startswith("/start give_"))
async def give_start(message: Message):
    give_id = int(message.text.split("_")[1])

    await message.answer(
        """
🎟 Введите количество билетов

1 билет = 0.1$
Минимум: 5 билетов
"""
    )

    dp.message.register(
        lambda m: process_buy(m, give_id)
    )

async def process_buy(message: Message, give_id):
    try:
        count = int(message.text)

        if count < 5:
            return await message.answer(
                "❌ Минимум 5 билетов"
            )

        amount = round(count * 0.1, 2)

        invoice = await create_invoice(amount)

        pay_url = invoice["result"]["pay_url"]
        invoice_id = invoice["result"]["invoice_id"]

        cursor.execute("""
        INSERT INTO invoices(invoice_id, user_id, giveaway_id, amount, ticket_count)
        VALUES(?, ?, ?, ?, ?)
        """, (
            invoice_id,
            message.from_user.id,
            give_id,
            amount,
            count
        ))

        db.commit()

        await message.answer(
            f"""
💰 Счет на оплату создан

🎟 Билетов: {count}
💵 Сумма: {amount}$

Оплатить:
{pay_url}
"""
        )

        while True:
            await asyncio.sleep(5)

            status = await check_invoice(invoice_id)

            try:
                paid = status["result"]["items"][0]["status"]

                if paid == "paid":

                    ticket_numbers = []

                    for _ in range(count):
                        num = random.randint(100000, 999999)

                        ticket_numbers.append(str(num))

                        cursor.execute("""
                        INSERT INTO tickets(giveaway_id, user_id, ticket_number)
                        VALUES(?, ?, ?)
                        """, (
                            give_id,
                            message.from_user.id,
                            num
                        ))

                    db.commit()

                    await message.answer(
                        f"""
✅ Оплата успешна

🎟 Ваши билеты:

<code>{", ".join(ticket_numbers)}</code>
"""
                    )

                    await bot.send_message(
                        CHANNEL_ID,
                        f"""
🎟 Пользователь аноним купил {count} билетов

Номера:
<code>{", ".join(ticket_numbers)}</code>
"""
                    )

                    break

            except:
                pass

    except:
        await message.answer("❌ Ошибка")

# ================= GIVEAWAY CHECKER =================

async def giveaway_checker():
    while True:
        await asyncio.sleep(10)

        cursor.execute("""
        SELECT id, title, prize, end_time
        FROM giveaways
        WHERE active=1
        """)

        gives = cursor.fetchall()

        for give in gives:
            give_id, title, prize, end_time = give

            end_time_obj = datetime.strptime(
                end_time,
                "%Y-%m-%d %H:%M:%S"
            )

            if datetime.now() >= end_time_obj:

                cursor.execute("""
                SELECT user_id, ticket_number
                FROM tickets
                WHERE giveaway_id=?
                """, (give_id,))

                tickets = cursor.fetchall()

                if not tickets:
                    continue

                winner = random.choice(tickets)

                winner_id = winner[0]
                ticket = winner[1]

                total_bank = len(tickets) * 0.1
                total_win = total_bank + prize

                await bot.send_message(
                    CHANNEL_ID,
                    f"""
🏆 Розыгрыш завершен

🎁 Приз: {title}

👑 Победитель:
<code>{winner_id}</code>

🎟 Билет:
<code>{ticket}</code>

💰 Выигрыш:
<b>{total_win}$</b>
"""
                )

                cursor.execute("""
                UPDATE giveaways
                SET active=0
                WHERE id=?
                """, (give_id,))

                db.commit()

# ================= RUN =================

async def main():
    asyncio.create_task(giveaway_checker())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
