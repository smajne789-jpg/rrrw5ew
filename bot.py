import asyncio
import random
import uuid
from datetime import datetime, timedelta

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

DB = "raffle.db"
TICKET_PRICE = 0.1
MIN_TICKETS = 5


# =========================
# DATABASE
# =========================

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS raffles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prize REAL,
            end_time TEXT,
            active INTEGER DEFAULT 1,
            channel_message_id INTEGER
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raffle_id INTEGER,
            user_id INTEGER,
            ticket_number INTEGER
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            raffle_id INTEGER,
            amount REAL,
            payload TEXT,
            paid INTEGER DEFAULT 0,
            ticket_count INTEGER,
            type TEXT
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS withdraws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            status TEXT DEFAULT 'pending'
        )
        ''')

        await db.commit()


# =========================
# HELPERS
# =========================

async def add_user(user_id, username):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, username) VALUES(?, ?)",
            (user_id, username)
        )
        await db.commit()


async def get_balance(user_id):
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def update_balance(user_id, amount):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (amount, user_id)
        )
        await db.commit()


async def create_invoice(amount, payload):
    url = "https://pay.crypt.bot/api/createInvoice"

    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN
    }

    data = {
        "asset": "USDT",
        "amount": amount,
        "description": "Raffle Payment",
        "payload": payload,
        "allow_comments": False,
        "allow_anonymous": True
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            result = await resp.json()
            return result


async def check_invoice(invoice_id):
    url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"

    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            result = await resp.json()
            return result


async def issue_tickets(user_id, raffle_id, count):
    numbers = []

    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT MAX(ticket_number) FROM tickets WHERE raffle_id=?",
            (raffle_id,)
        ) as cur:
            row = await cur.fetchone()
            last = row[0] if row[0] else 0

        for i in range(count):
            num = last + i + 1
            numbers.append(num)

            await db.execute(
                "INSERT INTO tickets(raffle_id, user_id, ticket_number) VALUES(?,?,?)",
                (raffle_id, user_id, num)
            )

        await db.commit()

    return numbers


async def get_raffle_pool(raffle_id):
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM tickets WHERE raffle_id=?",
            (raffle_id,)
        ) as cur:
            row = await cur.fetchone()
            total = row[0]

    return total * TICKET_PRICE


# =========================
# STATES
# =========================

class CreateRaffle(StatesGroup):
    prize = State()
    hours = State()


class BuyTickets(StatesGroup):
    count = State()


class DepositState(StatesGroup):
    amount = State()


class WithdrawState(StatesGroup):
    amount = State()


class AdminBalance(StatesGroup):
    username = State()
    amount = State()


# =========================
# START
# =========================

@dp.message(Command("start"))
async def start(message: Message):
    await add_user(message.from_user.id, message.from_user.username)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 Розыгрыши", callback_data="raffles")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
    ])

    await message.answer(
        "<b>🎰 Добро пожаловать в Raffle Bot</b>\n\n"
        "Покупай билеты и выигрывай крупные призы.",
        reply_markup=kb
    )


# =========================
# PROFILE
# =========================

@dp.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    balance = await get_balance(call.from_user.id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Пополнить", callback_data="deposit")],
        [InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw")]
    ])

    await call.message.edit_text(
        f"<b>👤 Профиль</b>\n\n"
        f"🆔 ID: <code>{call.from_user.id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f}$</b>",
        reply_markup=kb
    )


# =========================
# DEPOSIT
# =========================

@dp.callback_query(F.data == "deposit")
async def deposit(call: CallbackQuery, state: FSMContext):
    await state.set_state(DepositState.amount)

    await call.message.answer(
        "💰 Введите сумму пополнения\n"
        "Минимум: 0.1$"
    )


@dp.message(DepositState.amount)
async def deposit_amount(message: Message, state: FSMContext):
    amount = float(message.text)

    if amount < 0.1:
        return await message.answer("Минимальное пополнение 0.1$")

    payload = str(uuid.uuid4())

    invoice = await create_invoice(amount, payload)

    if not invoice["ok"]:
        return await message.answer("Ошибка создания счета")

    data = invoice["result"]

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO invoices(user_id, amount, payload, type) VALUES(?,?,?,?)",
            (message.from_user.id, amount, payload, "deposit")
        )
        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=data["pay_url"])]
    ])

    await message.answer(
        f"💰 Счет на <b>{amount}$</b> создан",
        reply_markup=kb
    )

    await state.clear()


# =========================
# WITHDRAW
# =========================

@dp.callback_query(F.data == "withdraw")
async def withdraw(call: CallbackQuery, state: FSMContext):
    await state.set_state(WithdrawState.amount)

    await call.message.answer("💸 Введите сумму вывода")


@dp.message(WithdrawState.amount)
async def withdraw_amount(message: Message, state: FSMContext):
    amount = float(message.text)

    balance = await get_balance(message.from_user.id)

    if amount > balance:
        return await message.answer("Недостаточно средств")

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO withdraws(user_id, amount) VALUES(?,?)",
            (message.from_user.id, amount)
        )

        withdraw_id = cur.lastrowid

        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data=f"confirm_withdraw:{withdraw_id}"
        )]
    ])

    await bot.send_message(
        ADMIN_ID,
        f"💸 Новая заявка на вывод\n\n"
        f"👤 Пользователь: @{message.from_user.username}\n"
        f"💰 Сумма: {amount}$",
        reply_markup=kb
    )

    await message.answer("✅ Заявка отправлена администрации")

    await state.clear()


@dp.callback_query(F.data.startswith("confirm_withdraw:"))
async def confirm_withdraw(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return

    withdraw_id = int(call.data.split(":")[1])

    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT user_id, amount FROM withdraws WHERE id=?",
            (withdraw_id,)
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return

        user_id, amount = row

        await update_balance(user_id, -amount)

        await db.execute(
            "UPDATE withdraws SET status='done' WHERE id=?",
            (withdraw_id,)
        )

        await db.commit()

    await bot.send_message(
        user_id,
        f"✅ Вывод {amount}$ успешно обработан"
    )

    await call.answer("Готово")


# =========================
# ADMIN CREATE RAFFLE
# =========================

@dp.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Создать розыгрыш", callback_data="create_raffle")],
    ])

    await message.answer("⚙ Админ панель", reply_markup=kb)


@dp.callback_query(F.data == "create_raffle")
async def create_raffle(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return

    await state.set_state(CreateRaffle.prize)

    await call.message.answer("💰 Введите сумму приза")


@dp.message(CreateRaffle.prize)
async def raffle_prize(message: Message, state: FSMContext):
    await state.update_data(prize=float(message.text))

    await state.set_state(CreateRaffle.hours)

    await message.answer("⏰ Введите длительность в часах")


@dp.message(CreateRaffle.hours)
async def raffle_hours(message: Message, state: FSMContext):
    hours = int(message.text)

    data = await state.get_data()

    prize = data["prize"]

    end_time = datetime.utcnow() + timedelta(hours=hours)

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO raffles(prize, end_time) VALUES(?,?)",
            (prize, end_time.isoformat())
        )

        raffle_id = cur.lastrowid

        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎟 Участвовать",
            url=f"https://t.me/{BOT_USERNAME}?start=raffle_{raffle_id}"
        )]
    ])

    msg = await bot.send_message(
        CHANNEL_ID,
        f"<b>🎰 НОВЫЙ РОЗЫГРЫШ</b>\n\n"
        f"💰 Приз: <b>{prize}$ + банк билетов</b>\n"
        f"🎟 Цена билета: {TICKET_PRICE}$\n"
        f"📦 Минимум билетов: {MIN_TICKETS}\n"
        f"⏰ Конец: через {hours} ч.",
        reply_markup=kb
    )

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE raffles SET channel_message_id=? WHERE id=?",
            (msg.message_id, raffle_id)
        )
        await db.commit()

    await message.answer("✅ Розыгрыш создан")

    await state.clear()


# =========================
# BUY TICKETS
# =========================

@dp.message(F.text.startswith("/start raffle_"))
async def raffle_start(message: Message, state: FSMContext):
    raffle_id = int(message.text.split("_")[1])

    await state.update_data(raffle_id=raffle_id)

    await state.set_state(BuyTickets.count)

    await message.answer(
        f"🎟 Введите количество билетов\n"
        f"1 билет = {TICKET_PRICE}$\n"
        f"Минимум {MIN_TICKETS} билетов"
    )


@dp.message(BuyTickets.count)
async def ticket_count(message: Message, state: FSMContext):
    count = int(message.text)

    if count < MIN_TICKETS:
        return await message.answer("Минимум 5 билетов")

    data = await state.get_data()

    raffle_id = data["raffle_id"]

    amount = round(count * TICKET_PRICE, 2)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💳 Оплатить CryptoBot",
            callback_data=f"pay_crypto:{raffle_id}:{count}"
        )],
        [InlineKeyboardButton(
            text="💰 Оплатить балансом",
            callback_data=f"pay_balance:{raffle_id}:{count}"
        )]
    ])

    await message.answer(
        f"💰 Сумма к оплате: <b>{amount}$</b>",
        reply_markup=kb
    )

    await state.clear()


@dp.callback_query(F.data.startswith("pay_balance:"))
async def pay_balance(call: CallbackQuery):
    _, raffle_id, count = call.data.split(":")

    raffle_id = int(raffle_id)
    count = int(count)

    amount = count * TICKET_PRICE

    balance = await get_balance(call.from_user.id)

    if balance < amount:
        return await call.message.answer("Недостаточно средств")

    await update_balance(call.from_user.id, -amount)

    numbers = await issue_tickets(call.from_user.id, raffle_id, count)

    nums = ", ".join(map(str, numbers))

    await call.message.answer(
        f"✅ Билеты куплены\n\n"
        f"🎟 Ваши номера:\n<code>{nums}</code>"
    )

    await bot.send_message(
        CHANNEL_ID,
        f"🎟 Пользователь аноним купил {count} билетов\n"
        f"Номера: {nums}"
    )


@dp.callback_query(F.data.startswith("pay_crypto:"))
async def pay_crypto(call: CallbackQuery):
    _, raffle_id, count = call.data.split(":")

    raffle_id = int(raffle_id)
    count = int(count)

    amount = round(count * TICKET_PRICE, 2)

    payload = str(uuid.uuid4())

    invoice = await create_invoice(amount, payload)

    if not invoice["ok"]:
        return await call.message.answer("Ошибка счета")

    data = invoice["result"]

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO invoices(user_id, raffle_id, amount, payload, ticket_count, type) VALUES(?,?,?,?,?,?)",
            (
                call.from_user.id,
                raffle_id,
                amount,
                payload,
                count,
                "raffle"
            )
        )
        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=data["pay_url"])]
    ])

    await call.message.answer(
        f"💰 Оплатите счет на {amount}$",
        reply_markup=kb
    )


# =========================
# PAYMENT CHECKER
# =========================

async def payment_checker():
    while True:
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT id, user_id, raffle_id, amount, type, ticket_count FROM invoices WHERE paid=0"
            ) as cur:
                rows = await cur.fetchall()

            for row in rows:
                invoice_db_id, user_id, raffle_id, amount, inv_type, ticket_count = row

                result = await check_invoice(invoice_db_id)

                try:
                    items = result["result"]["items"]
                except:
                    continue

                if not items:
                    continue

                invoice = items[0]

                if invoice["status"] == "paid":
                    await db.execute(
                        "UPDATE invoices SET paid=1 WHERE id=?",
                        (invoice_db_id,)
                    )

                    await db.commit()

                    if inv_type == "deposit":
                        await update_balance(user_id, amount)

                        await bot.send_message(
                            user_id,
                            f"✅ Баланс пополнен на {amount}$"
                        )

                    elif inv_type == "raffle":
                        numbers = await issue_tickets(
                            user_id,
                            raffle_id,
                            ticket_count
                        )

                        nums = ", ".join(map(str, numbers))

                        await bot.send_message(
                            user_id,
                            f"✅ Оплата прошла успешно\n\n"
                            f"🎟 Ваши билеты:\n<code>{nums}</code>"
                        )

                        await bot.send_message(
                            CHANNEL_ID,
                            f"🎟 Пользователь аноним купил {ticket_count} билетов\n"
                            f"Номера: {nums}"
                        )

        await asyncio.sleep(10)


# =========================
# RAFFLE CHECKER
# =========================

async def raffle_checker():
    while True:
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT id, prize, end_time FROM raffles WHERE active=1"
            ) as cur:
                raffles = await cur.fetchall()

            for raffle in raffles:
                raffle_id, prize, end_time = raffle

                if datetime.utcnow() >= datetime.fromisoformat(end_time):
                    async with db.execute(
                        "SELECT user_id, ticket_number FROM tickets WHERE raffle_id=?",
                        (raffle_id,)
                    ) as cur2:
                        tickets = await cur2.fetchall()

                    if not tickets:
                        continue

                    winner = random.choice(tickets)

                    winner_id = winner[0]
                    win_ticket = winner[1]

                    bank = await get_raffle_pool(raffle_id)

                    total_win = prize + bank

                    await update_balance(winner_id, total_win)

                    await bot.send_message(
                        CHANNEL_ID,
                        f"🏆 РОЗЫГРЫШ ЗАВЕРШЕН\n\n"
                        f"🎟 Выигрышный билет: #{win_ticket}\n"
                        f"💰 Выигрыш: {total_win}$"
                    )

                    await bot.send_message(
                        winner_id,
                        f"🎉 Поздравляем!\n\n"
                        f"Вы выиграли {total_win}$"
                    )

                    await db.execute(
                        "UPDATE raffles SET active=0 WHERE id=?",
                        (raffle_id,)
                    )

                    await db.commit()

        await asyncio.sleep(20)


# =========================
# RUN
# =========================

async def main():
    await init_db()

    asyncio.create_task(payment_checker())
    asyncio.create_task(raffle_checker())

    print("BOT STARTED")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
