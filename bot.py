# Telegram Raffle Bot (AIogram 3 + CryptoBot + SQLite)

Полностью готовый Telegram-бот для розыгрышей с:

* Созданием розыгрышей
import asyncio
import random
import aiohttp
import aiosqlite
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

DB_NAME = "raffle.db"

TICKET_PRICE = 0.1
MIN_TICKETS = 5


# =========================
# DATABASE
# =========================

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS raffles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prize REAL,
            end_time TEXT,
            active INTEGER DEFAULT 1,
            message_id INTEGER
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raffle_id INTEGER,
            user_id INTEGER,
            ticket_number INTEGER
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT,
            user_id INTEGER,
            raffle_id INTEGER,
            tickets_count INTEGER,
            amount REAL,
            type TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            status TEXT DEFAULT 'pending'
        )
        """)

        await db.commit()


# =========================
# HELPERS
# =========================

async def get_user(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,)
        )
        return await cur.fetchone()


async def add_user(user_id, username):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, username) VALUES(?, ?)",
            (user_id, username)
        )
        await db.commit()


async def get_balance(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (user_id,)
        )
        row = await cur.fetchone()

        if row:
            return row[0]

        return 0


async def update_balance(user_id, amount):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (amount, user_id)
        )
        await db.commit()


async def create_invoice(amount, payload="raffle"):
    url = "https://pay.crypt.bot/api/createInvoice"

    headers = {
        "Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN
    }

    data = {
        "asset": "USDT",
        "amount": str(amount),
        "description": payload,
        "paid_btn_name": "openBot",
        "paid_btn_url": f"https://t.me/{BOT_USERNAME}"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            result = await response.json()

            print(result)

            if result.get("ok"):
                return result["result"]

    return None


async def check_invoice(invoice_id):
    url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"

    headers = {
        "Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            result = await response.json()

            if result["ok"]:
                items = result["result"]["items"]

                if items:
                    return items[0]["status"]

    return None


async def get_active_raffle(raffle_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT * FROM raffles WHERE id=? AND active=1",
            (raffle_id,)
        )
        return await cur.fetchone()


async def count_tickets(raffle_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM tickets WHERE raffle_id=?",
            (raffle_id,)
        )
        row = await cur.fetchone()
        return row[0]


async def give_tickets(user_id, raffle_id, count):
    ticket_numbers = []

    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT MAX(ticket_number) FROM tickets WHERE raffle_id=?",
            (raffle_id,)
        )

        row = await cur.fetchone()

        last = row[0] or 0

        for i in range(count):
            number = last + i + 1

            await db.execute(
                "INSERT INTO tickets(raffle_id, user_id, ticket_number) VALUES(?, ?, ?)",
                (raffle_id, user_id, number)
            )

            ticket_numbers.append(number)

        await db.commit()

    return ticket_numbers


# =========================
# STATES
# =========================

class CreateRaffle(StatesGroup):
    prize = State()
    hours = State()


class BuyTickets(StatesGroup):
    amount = State()


class Deposit(StatesGroup):
    amount = State()


class Withdraw(StatesGroup):
    amount = State()


class AdminBalance(StatesGroup):
    username = State()
    amount = State()


# =========================
# START
# =========================

@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await add_user(
        message.from_user.id,
        message.from_user.username or "none"
    )

    args = message.text.split()

    if len(args) > 1:

        if args[1].startswith("raffle_"):

            raffle_id = int(args[1].split("_")[1])

            raffle = await get_active_raffle(raffle_id)

            if not raffle:
                return await message.answer("❌ Розыгрыш не найден")

            await state.update_data(raffle_id=raffle_id)

            await state.set_state(BuyTickets.amount)

            return await message.answer(
                f"🎟 Введите количество билетов\n\n"
                f"1 билет = {TICKET_PRICE}$\n"
                f"Минимум: {MIN_TICKETS}"
            )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="profile")
        ]
    ])

    await message.answer(
        "🎟 <b>Добро пожаловать в Raffle Bot</b>\n\n"
        "Участвуй в розыгрышах и выигрывай!",
        reply_markup=kb
    )


# =========================
# PROFILE
# =========================

@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    balance = await get_balance(callback.from_user.id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Пополнить", callback_data="deposit")
        ],
        [
            InlineKeyboardButton(text="📤 Вывод", callback_data="withdraw")
        ]
    ])

    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"💵 Баланс: <b>{balance:.2f}$</b>",
        reply_markup=kb
    )


# =========================
# DEPOSIT
# =========================

@dp.callback_query(F.data == "deposit")
async def deposit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Deposit.amount)

    await callback.message.answer(
        "💰 Введите сумму пополнения\n\nМинимум: 0.1$"
    )


@dp.message(Deposit.amount)
async def deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text)

        if amount < 0.1:
            return await message.answer("❌ Минимум 0.1$")

        invoice = await create_invoice(amount, "Deposit")

        if not invoice:
            return await message.answer("❌ Ошибка создания счета")

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO invoices VALUES(?,?,?,?,?,?)",
                (
                    str(invoice['invoice_id']),
                    message.from_user.id,
                    0,
                    0,
                    amount,
                    "deposit"
                )
            )
            await db.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Оплатить",
                    url=invoice['pay_url']
                )
            ]
        ])

        await message.answer(
            f"💳 Счет на {amount}$ создан",
            reply_markup=kb
        )

    except:
        await message.answer("❌ Ошибка")

    await state.clear()


# =========================
# WITHDRAW
# =========================

@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Withdraw.amount)

    await callback.message.answer(
        "📤 Введите сумму вывода"
    )


@dp.message(Withdraw.amount)
async def withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text)

        balance = await get_balance(message.from_user.id)

        if amount > balance:
            return await message.answer("❌ Недостаточно средств")

        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute(
                "INSERT INTO withdrawals(user_id, amount) VALUES(?, ?)",
                (message.from_user.id, amount)
            )

            withdraw_id = cur.lastrowid

            await db.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"confirm_withdraw:{withdraw_id}"
                )
            ]
        ])

        await bot.send_message(
            ADMIN_ID,
            f"📤 Новая заявка на вывод\n\n"
            f"👤 Пользователь: @{message.from_user.username}\n"
            f"💵 Сумма: {amount}$",
            reply_markup=kb
        )

        await message.answer(
            "✅ Заявка отправлена админу"
        )

    except:
        await message.answer("❌ Ошибка")

    await state.clear()


@dp.callback_query(F.data.startswith("confirm_withdraw:"))
async def confirm_withdraw(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    withdraw_id = int(callback.data.split(":")[1])

    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT user_id, amount FROM withdrawals WHERE id=?",
            (withdraw_id,)
        )

        row = await cur.fetchone()

        if not row:
            return

        user_id, amount = row

        await update_balance(user_id, -amount)

        await db.execute(
            "UPDATE withdrawals SET status='done' WHERE id=?",
            (withdraw_id,)
        )

        await db.commit()

    await bot.send_message(
        user_id,
        f"✅ Вывод {amount}$ успешно обработан"
    )

    await callback.message.edit_text(
        "✅ Вывод подтвержден"
    )


# =========================
# ADMIN CREATE RAFFLE
# =========================

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    text = (
        "⚙️ Админ панель\n\n"
        "🎁 /createraffle - создать розыгрыш\n"
        "💰 /addbalance username сумма\n"
        "❌ /removebalance username сумма"
    )

    await message.answer(text)


@dp.callback_query(F.data == "create_raffle")
async def create_raffle(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    await state.set_state(CreateRaffle.prize)

    await callback.message.answer(
        "🎁 Введите сумму приза"
    )


@dp.message(CreateRaffle.prize)
async def raffle_prize(message: Message, state: FSMContext):
    await state.update_data(prize=float(message.text))

    await state.set_state(CreateRaffle.hours)

    await message.answer(
        "⏰ Введите длительность в часах"
    )


@dp.message(CreateRaffle.hours)
async def raffle_hours(message: Message, state: FSMContext):
    data = await state.get_data()

    prize = data['prize']
    hours = int(message.text)

    end_time = datetime.now() + timedelta(hours=hours)

    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO raffles(prize, end_time) VALUES(?, ?)",
            (prize, end_time.isoformat())
        )

        raffle_id = cur.lastrowid

        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎟 Участвовать",
                url=f"https://t.me/{BOT_USERNAME}?start=raffle_{raffle_id}"
            )
        ]
    ])

    msg = await bot.send_message(
        CHANNEL_ID,
        f"🎁 <b>НОВЫЙ РОЗЫГРЫШ</b>\n\n"
        f"💰 Стартовый приз: <b>{prize}$</b>\n"
        f"➕ + все деньги с купленных билетов\n\n"
        f"🎟 Цена билета: {TICKET_PRICE}$\n"
        f"📦 Минимум: {MIN_TICKETS} билетов\n\n"
        f"⏰ Конец: {end_time.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"🔥 Успей принять участие!",
        reply_markup=kb
    )

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE raffles SET message_id=? WHERE id=?",
            (msg.message_id, raffle_id)
        )
        await db.commit()

    await message.answer(
        "✅ Розыгрыш опубликован"
    )

    await state.clear()


# =========================
# BUY TICKETS
# =========================

@dp.message(F.text.startswith('/start raffle_'))
async def join_raffle(message: Message, state: FSMContext):
    raffle_id = int(message.text.split('_')[1])

    raffle = await get_active_raffle(raffle_id)

    if not raffle:
        return await message.answer("❌ Розыгрыш не найден")

    await state.update_data(raffle_id=raffle_id)

    await state.set_state(BuyTickets.amount)

    await message.answer(
        f"🎟 Введите количество билетов\n\n"
        f"1 билет = {TICKET_PRICE}$\n"
        f"Минимум: {MIN_TICKETS}"
    )


@dp.message(BuyTickets.amount)
async def buy_tickets(message: Message, state: FSMContext):
    try:
        count = int(message.text)

        if count < MIN_TICKETS:
            return await message.answer(
                f"❌ Минимум {MIN_TICKETS} билетов"
            )

        data = await state.get_data()

        raffle_id = data['raffle_id']

        amount = round(count * TICKET_PRICE, 2)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Оплатить CryptoBot",
                    callback_data=f"pay_crypto:{raffle_id}:{count}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Оплатить балансом",
                    callback_data=f"pay_balance:{raffle_id}:{count}"
                )
            ]
        ])

        await message.answer(
            f"💵 К оплате: {amount}$",
            reply_markup=kb
        )

    except:
        await message.answer("❌ Ошибка")

    await state.clear()


@dp.callback_query(F.data.startswith("pay_balance:"))
async def pay_balance(callback: CallbackQuery):
    _, raffle_id, count = callback.data.split(":")

    raffle_id = int(raffle_id)
    count = int(count)

    amount = round(count * TICKET_PRICE, 2)

    balance = await get_balance(callback.from_user.id)

    if balance < amount:
        return await callback.answer(
            "Недостаточно средств",
            show_alert=True
        )

    await update_balance(callback.from_user.id, -amount)

    tickets = await give_tickets(
        callback.from_user.id,
        raffle_id,
        count
    )

    await callback.message.answer(
        f"✅ Оплата прошла успешно\n\n"
        f"🎟 Ваши билеты:\n"
        f"<code>{', '.join(map(str, tickets))}</code>"
    )

    await bot.send_message(
        CHANNEL_ID,
        f"🎟 Пользователь аноним купил {count} билетов\n"
        f"🎫 Номера: {', '.join(map(str, tickets))}"
    )


@dp.callback_query(F.data.startswith("pay_crypto:"))
async def pay_crypto(callback: CallbackQuery):
    _, raffle_id, count = callback.data.split(":")

    raffle_id = int(raffle_id)
    count = int(count)

    amount = round(count * TICKET_PRICE, 2)

    invoice = await create_invoice(
        amount,
        f"Raffle #{raffle_id}"
    )

    if not invoice:
        return await callback.answer(
            "Ошибка создания счета",
            show_alert=True
        )

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO invoices VALUES(?,?,?,?,?,?)",
            (
                str(invoice['invoice_id']),
                callback.from_user.id,
                raffle_id,
                count,
                amount,
                'raffle'
            )
        )
        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💳 Оплатить",
                url=invoice['pay_url']
            )
        ]
    ])

    await callback.message.answer(
        f"💳 Счет на {amount}$ создан",
        reply_markup=kb
    )


# =========================
# PAYMENT CHECKER
# =========================

async def payment_checker():
    while True:
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute(
                "SELECT rowid, * FROM invoices"
            )

            invoices = await cur.fetchall()

            for invoice in invoices:
                rowid = invoice[0]
                invoice_id = invoice[1]
                user_id = invoice[2]
                raffle_id = invoice[3]
                tickets_count = invoice[4]
                amount = invoice[5]
                inv_type = invoice[6]

                status = await check_invoice(invoice_id)

                if status == "paid":

                    if inv_type == "deposit":
                        await update_balance(user_id, amount)

                        await bot.send_message(
                            user_id,
                            f"✅ Баланс пополнен на {amount}$"
                        )

                    else:
                        tickets = await give_tickets(
                            user_id,
                            raffle_id,
                            tickets_count
                        )

                        await bot.send_message(
                            user_id,
                            f"✅ Оплата прошла успешно\n\n"
                            f"🎟 Ваши билеты:\n"
                            f"<code>{', '.join(map(str, tickets))}</code>"
                        )

                        await bot.send_message(
                            CHANNEL_ID,
                            f"🎟 Пользователь аноним купил {tickets_count} билетов\n"
                            f"🎫 Номера: {', '.join(map(str, tickets))}"
                        )

                    await db.execute(
                        "DELETE FROM invoices WHERE rowid=?",
                        (rowid,)
                    )

            await db.commit()

        await asyncio.sleep(10)


# =========================
# RAFFLE CHECKER
# =========================

async def raffle_checker():
    while True:
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute(
                "SELECT id, prize, end_time FROM raffles WHERE active=1"
            )

            raffles = await cur.fetchall()

            for raffle in raffles:
                raffle_id, prize, end_time = raffle

                end = datetime.fromisoformat(end_time)

                if datetime.now() >= end:

                    cur2 = await db.execute(
                        "SELECT user_id, ticket_number FROM tickets WHERE raffle_id=?",
                        (raffle_id,)
                    )

                    tickets = await cur2.fetchall()

                    if not tickets:
                        await bot.send_message(
                            CHANNEL_ID,
                            f"❌ Розыгрыш #{raffle_id} завершен без участников"
                        )

                    else:
                        winner = random.choice(tickets)

                        winner_id = winner[0]
                        ticket_number = winner[1]

                        total_tickets = len(tickets)
                        bank = prize + (total_tickets * TICKET_PRICE)

                        await bot.send_message(
                            CHANNEL_ID,
                            f"🏆 РОЗЫГРЫШ ЗАВЕРШЕН\n\n"
                            f"🎉 Победитель: <a href='tg://user?id={winner_id}'>Пользователь</a>\n"
                            f"🎟 Победный билет: #{ticket_number}\n"
                            f"💰 Выигрыш: {bank}$"
                        )

                    await db.execute(
                        "UPDATE raffles SET active=0 WHERE id=?",
                        (raffle_id,)
                    )

            await db.commit()

        await asyncio.sleep(15)


# =========================
# ADMIN BALANCE
# =========================

@dp.message(Command("addbalance"))
async def addbalance(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        _, username, amount = message.text.split()

        amount = float(amount)

        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute(
                "SELECT user_id FROM users WHERE username=?",
                (username.replace('@', ''),)
            )

            row = await cur.fetchone()

            if not row:
                return await message.answer("❌ Пользователь не найден")

            user_id = row[0]

        await update_balance(user_id, amount)

        await message.answer("✅ Баланс начислен")

    except:
        await message.answer(
            "Использование: /addbalance username сумма"
        )


@dp.message(Command("removebalance"))
async def removebalance(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        _, username, amount = message.text.split()

        amount = float(amount)

        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute(
                "SELECT user_id FROM users WHERE username=?",
                (username.replace('@', ''),)
            )

            row = await cur.fetchone()

            if not row:
                return await message.answer("❌ Пользователь не найден")

            user_id = row[0]

        await update_balance(user_id, -amount)

        await message.answer("✅ Баланс снят")

    except:
        await message.answer(
            "Использование: /removebalance username сумма"
        )


# =========================
# MAIN
# =========================

async def main():
    await init_db()

    asyncio.create_task(payment_checker())
    asyncio.create_task(raffle_checker())

    print("BOT STARTED")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
