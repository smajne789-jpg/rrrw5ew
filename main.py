import asyncio
import random
import logging
import requests
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
TICKET_PRICE = float(os.getenv("TICKET_PRICE", "0.1"))
MIN_TICKETS = int(os.getenv("MIN_TICKETS", "5"))
DB_NAME = os.getenv("DB_NAME")
BOT_USERNAME = os.getenv("BOT_USERNAME")

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())


class CreateGiveaway(StatesGroup):
    title = State()
    prize = State()
    duration = State()


class DepositState(StatesGroup):
    amount = State()


class WithdrawState(StatesGroup):
    amount = State()


class BuyTicketsState(StatesGroup):
    amount = State()
    giveaway_id = State()


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            prize TEXT,
            active INTEGER DEFAULT 1
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            giveaway_id INTEGER,
            user_id INTEGER,
            ticket_number INTEGER
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS deposits (
            invoice_id TEXT,
            user_id INTEGER,
            amount REAL,
            paid INTEGER DEFAULT 0
        )
        ''')

        await db.commit()


async def get_user(user_id, username=""):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,)
        )
        user = await cur.fetchone()

        if not user:
            await db.execute(
                "INSERT INTO users(user_id, username) VALUES(?, ?)",
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
        return row[0] if row else 0


async def add_balance(user_id, amount):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (amount, user_id)
        )
        await db.commit()


async def remove_balance(user_id, amount):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id=?",
            (amount, user_id)
        )
        await db.commit()


menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🎁 Розыгрыши", callback_data="giveaways")],
    [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
    [InlineKeyboardButton(text="💰 Пополнить", callback_data="deposit")],
    [InlineKeyboardButton(text="📤 Вывод", callback_data="withdraw")],
])


@dp.message(Command("start"))
async def start(message: Message):
    await get_user(message.from_user.id, message.from_user.username)

    await message.answer(
        "<b>🎉 Giveaway Bot</b>\n\n"
        "Покупай билеты и участвуй в розыгрышах.",
        reply_markup=menu
    )


@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    balance = await get_balance(callback.from_user.id)

    await callback.message.answer(
        f"<b>👤 Профиль</b>\n\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"💰 Баланс: <b>{balance}$</b>"
    )


@dp.callback_query(F.data == "deposit")
async def deposit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DepositState.amount)

    await callback.message.answer(
        "💰 Введите сумму пополнения"
    )


@dp.message(DepositState.amount)
async def deposit_amount(message: Message, state: FSMContext):
    amount = float(message.text)

    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN
    }

    data = {
        "asset": "USDT",
        "amount": amount,
        "description": "Пополнение баланса"
    }

    response = requests.post(
        "https://pay.crypt.bot/api/createInvoice",
        headers=headers,
        json=data
    ).json()

    if not response["ok"]:
        await message.answer("Ошибка создания счета")
        return

    invoice = response["result"]

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO deposits(invoice_id, user_id, amount) VALUES(?, ?, ?)",
            (invoice["invoice_id"], message.from_user.id, amount)
        )
        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=invoice["pay_url"])]
    ])

    await message.answer(
        f"💰 Счет на {amount}$ создан",
        reply_markup=kb
    )

    await state.clear()


@dp.callback_query(F.data == "giveaways")
async def giveaways(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT id, title, prize FROM giveaways WHERE active=1"
        )
        rows = await cur.fetchall()

    if not rows:
        await callback.message.answer("Нет активных розыгрышей")
        return

    for row in rows:
        giveaway_id, title, prize = row

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🎟 Купить билеты",
                callback_data=f"buy_{giveaway_id}"
            )]
        ])

        await callback.message.answer(
            f"🎁 <b>{title}</b>\n"
            f"🏆 Приз: {prize}",
            reply_markup=kb
        )


@dp.callback_query(F.data.startswith("buy_"))
async def buy_tickets(callback: CallbackQuery, state: FSMContext):
    giveaway_id = int(callback.data.split("_")[1])

    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(BuyTicketsState.amount)

    await callback.message.answer(
        f"🎟 Введите количество билетов\n"
        f"1 билет = {TICKET_PRICE}$\n"
        f"Минимум: {MIN_TICKETS}"
    )


@dp.message(BuyTicketsState.amount)
async def process_buy(message: Message, state: FSMContext):
    tickets = int(message.text)

    if tickets < MIN_TICKETS:
        await message.answer("Минимальное количество не достигнуто")
        return

    total = tickets * TICKET_PRICE

    balance = await get_balance(message.from_user.id)

    if balance < total:
        await message.answer(
            f"Недостаточно средств\nНужно: {total}$"
        )
        return

    await remove_balance(message.from_user.id, total)

    data = await state.get_data()
    giveaway_id = data["giveaway_id"]

    ticket_numbers = []

    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT MAX(ticket_number) FROM tickets WHERE giveaway_id=?",
            (giveaway_id,)
        )

        last = await cur.fetchone()
        start = last[0] + 1 if last[0] else 1

        for i in range(tickets):
            number = start + i
            ticket_numbers.append(str(number))

            await db.execute(
                "INSERT INTO tickets(giveaway_id, user_id, ticket_number) VALUES(?, ?, ?)",
                (giveaway_id, message.from_user.id, number)
            )

        await db.commit()

    await message.answer(
        "✅ Билеты куплены\n\n"
        f"🎟 Номера: {', '.join(ticket_numbers)}"
    )

    await bot.send_message(
        CHANNEL_ID,
        f"🎟 Анонимный пользователь купил {tickets} билетов\n"
        f"Номера: {', '.join(ticket_numbers)}"
    )

    await state.clear()


@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: CallbackQuery, state: FSMContext):
    await state.set_state(WithdrawState.amount)

    await callback.message.answer(
        "📤 Введите сумму вывода"
    )


@dp.message(WithdrawState.amount)
async def withdraw_amount(message: Message, state: FSMContext):
    amount = float(message.text)

    balance = await get_balance(message.from_user.id)

    if amount > balance:
        await message.answer("Недостаточно средств")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Подтвердить вывод",
            callback_data=f"approve_withdraw_{message.from_user.id}_{amount}"
        )]
    ])

    await bot.send_message(
        ADMIN_ID,
        f"📤 Новая заявка на вывод\n\n"
        f"👤 User: @{message.from_user.username}\n"
        f"💰 Сумма: {amount}$",
        reply_markup=kb
    )

    await message.answer("✅ Заявка отправлена")
    await state.clear()


@dp.callback_query(F.data.startswith("approve_withdraw_"))
async def approve(callback: CallbackQuery):
    data = callback.data.split("_")

    user_id = int(data[2])
    amount = float(data[3])

    await remove_balance(user_id, amount)

    await bot.send_message(
        user_id,
        f"✅ Вывод успешно обработан\n"
        f"Сумма: {amount}$"
    )

    await callback.message.edit_text("✅ Вывод подтвержден")


@dp.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Создать", callback_data="create_giveaway")]
    ])

    await message.answer(
        "⚙️ Админ панель",
        reply_markup=kb
    )


@dp.callback_query(F.data == "create_giveaway")
async def create_giveaway(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    await state.set_state(CreateGiveaway.title)

    await callback.message.answer("Введите название")


@dp.message(CreateGiveaway.title)
async def giveaway_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(CreateGiveaway.prize)

    await message.answer("Введите приз")


@dp.message(CreateGiveaway.prize)
async def giveaway_prize(message: Message, state: FSMContext):
    await state.update_data(prize=message.text)
    await state.set_state(CreateGiveaway.duration)

    await message.answer("Введите длительность")


@dp.message(CreateGiveaway.duration)
async def giveaway_duration(message: Message, state: FSMContext):
    data = await state.get_data()

    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO giveaways(title, prize) VALUES(?, ?)",
            (data["title"], data["prize"])
        )

        giveaway_id = cur.lastrowid
        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎟 Участвовать",
            url=f"https://t.me/{BOT_USERNAME}?start=join_{giveaway_id}"
        )]
    ])

    await bot.send_message(
        CHANNEL_ID,
        f"🎁 <b>{data['title']}</b>\n"
        f"🏆 Приз: {data['prize']}\n"
        f"⏳ Длительность: {message.text}",
        reply_markup=kb
    )

    await message.answer("✅ Розыгрыш создан")
    await state.clear()


async def check_payments():
    while True:
        try:
            headers = {
                "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN
            }

            response = requests.get(
                "https://pay.crypt.bot/api/getInvoices",
                headers=headers
            ).json()

            if response["ok"]:
                invoices = response["result"]["items"]

                async with aiosqlite.connect(DB_NAME) as db:
                    for invoice in invoices:
                        if invoice["status"] != "paid":
                            continue

                        invoice_id = str(invoice["invoice_id"])

                        cur = await db.execute(
                            "SELECT user_id, amount, paid FROM deposits WHERE invoice_id=?",
                            (invoice_id,)
                        )

                        row = await cur.fetchone()

                        if not row:
                            continue

                        user_id, amount, paid = row

                        if paid:
                            continue

                        await add_balance(user_id, amount)

                        await db.execute(
                            "UPDATE deposits SET paid=1 WHERE invoice_id=?",
                            (invoice_id,)
                        )

                        await db.commit()

                        await bot.send_message(
                            user_id,
                            f"✅ Баланс пополнен на {amount}$"
                        )

        except Exception as e:
            print(e)

        await asyncio.sleep(15)


async def main():
    await init_db()

    asyncio.create_task(check_payments())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
