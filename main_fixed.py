import asyncio
import logging
import random
import time
from datetime import datetime, timedelta
from decimal import Decimal

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
CRYPTOPAY_TOKEN = os.getenv("CRYPTOPAY_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")

DB_NAME = "bot.db"
TICKET_PRICE = Decimal("0.1")
MIN_TICKETS = 5
CRYPTO_API = "https://pay.crypt.bot/api"

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())


class TopupState(StatesGroup):
    amount = State()


class WithdrawState(StatesGroup):
    amount = State()


class GiveawayState(StatesGroup):
    prize = State()
    duration = State()


class BuyTicketsState(StatesGroup):
    amount = State()


class AdminBalanceState(StatesGroup):
    username = State()
    amount = State()
    action = State()


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0,
            created_at TEXT
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prize REAL,
            total_bank REAL,
            end_time INTEGER,
            active INTEGER DEFAULT 1,
            message_id INTEGER
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
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            invoice_id TEXT,
            amount REAL,
            status TEXT
        )
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            status TEXT
        )
        ''')

        await db.commit()


async def register_user(user):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT user_id FROM users WHERE user_id=?",
            (user.id,)
        )
        exists = await cursor.fetchone()

        if not exists:
            await db.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?)",
                (
                    user.id,
                    user.username,
                    0,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
            )
            await db.commit()


async def get_balance(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (user_id,)
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0


async def update_balance(user_id, amount):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (amount, user_id)
        )
        await db.commit()


async def create_invoice(amount):
    headers = {
        "Crypto-Pay-API-Token": CRYPTOPAY_TOKEN,
        "Content-Type": "application/json"
    }

    payload = {
        "asset": "USDT",
        "amount": str(amount),
        "description": "Balance top up",
        "paid_btn_name": "openBot",
        "paid_btn_url": f"https://t.me/{BOT_USERNAME}"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{CRYPTO_API}/createInvoice",
            headers=headers,
            json=payload
        ) as response:
            text = await response.text()
            logging.info(f"CREATE INVOICE RESPONSE: {text}")
            data = await response.json(content_type=None)
            return data


async def check_invoice(invoice_id):
    headers = {
        "Crypto-Pay-API-Token": CRYPTOPAY_TOKEN
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{CRYPTO_API}/getInvoices",
            headers=headers,
            params={"invoice_ids": invoice_id}
        ) as response:
            text = await response.text()
            logging.info(f"CHECK INVOICE RESPONSE: {text}")
            data = await response.json(content_type=None)
            return data


async def main_menu():
    kb = InlineKeyboardBuilder()

    kb.button(text="🎟 Розыгрыши", callback_data="giveaways")
    kb.button(text="👤 Профиль", callback_data="profile")
    kb.button(text="💰 Баланс", callback_data="balance")
    kb.button(text="➕ Пополнить", callback_data="topup")
    kb.button(text="💸 Вывести", callback_data="withdraw")

    kb.adjust(2)
    return kb.as_markup()


@dp.message(CommandStart())
async def start(message: Message):
    await register_user(message.from_user)

    text = """
<b>🎁 Giveaway Bot</b>

Добро пожаловать в бота розыгрышей.

Покупайте билеты и выигрывайте большие призы.
"""

    await message.answer(text, reply_markup=await main_menu())


@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    balance = await get_balance(callback.from_user.id)

    text = f"""
<b>👤 Ваш профиль</b>

🆔 ID: <code>{callback.from_user.id}</code>
👤 Username: @{callback.from_user.username}
💰 Баланс: <b>{balance}$</b>
"""

    await callback.message.edit_text(text, reply_markup=await main_menu())


@dp.callback_query(F.data == "balance")
async def balance(callback: CallbackQuery):
    bal = await get_balance(callback.from_user.id)

    await callback.answer(
        f"Ваш баланс: {bal}$",
        show_alert=True
    )


@dp.callback_query(F.data == "topup")
async def topup(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TopupState.amount)

    await callback.message.answer(
        "💰 Введите сумму пополнения\n\nМинимум: 0.1$"
    )


@dp.message(TopupState.amount)
async def topup_amount(message: Message, state: FSMContext):
    try:
        amount = Decimal(message.text)

        if amount < Decimal("0.1"):
            return await message.answer("❌ Минимум 0.1$")

        invoice = await create_invoice(amount)

        if not invoice.get("ok"):
            return await message.answer("❌ Ошибка создания счета")

        invoice_data = invoice["result"]

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO payments (user_id, invoice_id, amount, status) VALUES (?, ?, ?, ?)",
                (
                    message.from_user.id,
                    invoice_data["invoice_id"],
                    float(amount),
                    "pending"
                )
            )
            await db.commit()

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💳 Оплатить",
                        url=invoice_data["pay_url"]
                    )
                ]
            ]
        )

        await message.answer(
            f"💰 Счет на {amount}$ создан",
            reply_markup=kb
        )

        asyncio.create_task(
            wait_payment(
                message.from_user.id,
                invoice_data["invoice_id"],
                amount,
                message.chat.id
            )
        )

        await state.clear()

    except Exception as e:
        logging.exception(e)
        await message.answer(f"❌ Ошибка: {str(e)}")


async def wait_payment(user_id, invoice_id, amount, chat_id):
    for _ in range(120):
        try:
            data = await check_invoice(invoice_id)

            if data["ok"]:
                items = data["result"]["items"]

                if items:
                    invoice = items[0]

                    if invoice["status"] == "paid":
                        async with aiosqlite.connect(DB_NAME) as db:
                            cursor = await db.execute(
                                "SELECT status FROM payments WHERE invoice_id=?",
                                (str(invoice_id),)
                            )
                            row = await cursor.fetchone()

                            if row and row[0] == "paid":
                                return

                            await db.execute(
                                "UPDATE payments SET status='paid' WHERE invoice_id=?",
                                (str(invoice_id),)
                            )

                            await db.commit()

                        await update_balance(user_id, float(amount))

                        await bot.send_message(
                            chat_id,
                            f"✅ Баланс пополнен на {amount}$"
                        )

                        return

        except Exception as e:
            logging.error(e)

        await asyncio.sleep(5)


@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: CallbackQuery, state: FSMContext):
    await state.set_state(WithdrawState.amount)

    await callback.message.answer(
        "💸 Введите сумму вывода"
    )


@dp.message(WithdrawState.amount)
async def withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text)
        balance = await get_balance(message.from_user.id)

        if amount > balance:
            return await message.answer("❌ Недостаточно средств")

        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute(
                "INSERT INTO withdrawals (user_id, amount, status) VALUES (?, ?, ?)",
                (
                    message.from_user.id,
                    amount,
                    "pending"
                )
            )
            await db.commit()

            withdrawal_id = cursor.lastrowid

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить",
                        callback_data=f"approve_withdraw:{withdrawal_id}:{message.from_user.id}:{amount}"
                    )
                ]
            ]
        )

        await bot.send_message(
            ADMIN_ID,
            f"💸 Заявка на вывод\n\n"
            f"👤 @{message.from_user.username}\n"
            f"🆔 {message.from_user.id}\n"
            f"💰 {amount}$",
            reply_markup=kb
        )

        await message.answer("✅ Заявка отправлена")
        await state.clear()

    except Exception as e:
        logging.exception(e)
        await message.answer(f"❌ Ошибка: {str(e)}")


@dp.callback_query(F.data.startswith("approve_withdraw"))
async def approve_withdraw(callback: CallbackQuery):
    try:
        _, wid, user_id, amount = callback.data.split(":")

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE withdrawals SET status='done' WHERE id=?",
                (wid,)
            )

            await db.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id=?",
                (float(amount), int(user_id))
            )

            await db.commit()

        await bot.send_message(
            int(user_id),
            "✅ Ваш вывод успешно обработан"
        )

        await callback.answer("Подтверждено")

    except Exception as e:
        logging.error(e)


@dp.message(F.text == "/admin")
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Создать розыгрыш", callback_data="create_giveaway")],
            [InlineKeyboardButton(text="💳 Выдать баланс", callback_data="add_balance")],
            [InlineKeyboardButton(text="❌ Снять баланс", callback_data="remove_balance")]
        ]
    )

    await message.answer("⚙ Админ панель", reply_markup=kb)


@dp.callback_query(F.data == "add_balance")
async def add_balance_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminBalanceState.username)
    await state.update_data(action="add")
    await callback.message.answer("Введите username пользователя без @")


@dp.callback_query(F.data == "remove_balance")
async def remove_balance_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminBalanceState.username)
    await state.update_data(action="remove")
    await callback.message.answer("Введите username пользователя без @")


@dp.message(AdminBalanceState.username)
async def admin_balance_username(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT user_id FROM users WHERE username=?",
            (message.text.replace('@', ''),)
        )
        user = await cursor.fetchone()

    if not user:
        return await message.answer("❌ Пользователь не найден")

    await state.update_data(target_id=user[0])
    await state.set_state(AdminBalanceState.amount)
    await message.answer("Введите сумму")


@dp.message(AdminBalanceState.amount)
async def admin_balance_amount(message: Message, state: FSMContext):
    data = await state.get_data()

    amount = float(message.text)
    target_id = data['target_id']

    async with aiosqlite.connect(DB_NAME) as db:
        if data['action'] == 'add':
            await db.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id=?",
                (amount, target_id)
            )
            text = f"✅ Баланс пополнен на {amount}$"
        else:
            await db.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id=?",
                (amount, target_id)
            )
            text = f"❌ С баланса списано {amount}$"

        await db.commit()

    await bot.send_message(target_id, text)
    await message.answer("✅ Успешно")
    await state.clear()


@dp.callback_query(F.data == "create_giveaway")
async def create_giveaway(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    await state.set_state(GiveawayState.prize)
    await callback.message.answer("Введите сумму приза")


@dp.message(GiveawayState.prize)
async def giveaway_prize(message: Message, state: FSMContext):
    await state.update_data(prize=float(message.text))
    await state.set_state(GiveawayState.duration)

    await message.answer(
        "Введите длительность в минутах"
    )


@dp.message(GiveawayState.duration)
async def giveaway_duration(message: Message, state: FSMContext):
    try:
        data = await state.get_data()

        minutes = int(message.text)

        if minutes <= 0:
            return await message.answer("❌ Введите корректное количество минут")

        end_time = int(time.time()) + (minutes * 60)

        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute(
                "INSERT INTO giveaways (prize, total_bank, end_time, active) VALUES (?, ?, ?, ?)",
                (
                    float(data['prize']),
                    float(data['prize']),
                    end_time,
                    1
                )
            )

            await db.commit()
            giveaway_id = cursor.lastrowid

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎟 Участвовать",
                        url=f"https://t.me/{BOT_USERNAME}?start=gw_{giveaway_id}"
                    )
                ]
            ]
        )

        text = (
            f"🎁 <b>НОВЫЙ РОЗЫГРЫШ</b>

"
            f"💰 Начальный приз: <b>{data['prize']}$</b>
"
            f"🎟 Цена билета: <b>0.1$</b>
"
            f"📈 Все деньги с билетов идут в призовой фонд
"
            f"⏳ Длительность: <b>{minutes} мин.</b>

"
            f"🔥 Участвуй прямо сейчас!"
        )

        msg = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            reply_markup=kb
        )

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE giveaways SET message_id=? WHERE id=?",
                (msg.message_id, giveaway_id)
            )
            await db.commit()

        await message.answer(
            f"✅ Розыгрыш успешно создан

ID: {giveaway_id}"
        )

        await state.clear()

    except Exception as e:
        logging.exception(e)
        await message.answer(f"❌ Ошибка создания розыгрыша:
{str(e)}")


async def giveaway_checker():
    while True:
        try:
            now = int(time.time())

            async with aiosqlite.connect(DB_NAME) as db:
                cursor = await db.execute(
                    "SELECT id, total_bank FROM giveaways WHERE active=1 AND end_time<=?",
                    (now,)
                )

                giveaways = await cursor.fetchall()

                for giveaway in giveaways:
                    gid = giveaway[0]
                    bank = giveaway[1]

                    cursor2 = await db.execute(
                        "SELECT user_id, ticket_number FROM tickets WHERE giveaway_id=?",
                        (gid,)
                    )

                    tickets = await cursor2.fetchall()

                    if not tickets:
                        continue

                    winner = random.choice(tickets)

                    user_id = winner[0]
                    ticket = winner[1]

                    await db.execute(
                        "UPDATE giveaways SET active=0 WHERE id=?",
                        (gid,)
                    )

                    await db.commit()

                    try:
                        user = await bot.get_chat(user_id)
                        username = user.username or user.first_name
                    except:
                        username = "Unknown"

                    await bot.send_message(
                        CHANNEL_ID,
                        f"🎉 <b>Розыгрыш завершен</b>\n\n"
                        f"🏆 Победитель: @{username}\n"
                        f"🎟 Билет: {ticket}\n"
                        f"💰 Выигрыш: {bank}$"
                    )

        except Exception as e:
            logging.error(e)

        await asyncio.sleep(10)


async def main():
    await init_db()

    asyncio.create_task(giveaway_checker())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
