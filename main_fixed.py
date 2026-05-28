from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

import asyncio
import aiosqlite
import os

BOT_TOKEN = "TOKEN"
BOT_USERNAME = "your_bot"

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB = "database.db"


class BuyState(StatesGroup):
    tickets = State()


class AdminBalance(StatesGroup):
    user = State()
    amount = State()


async def db():
    conn = await aiosqlite.connect(DB)
    return conn


async def create_tables():
    conn = await db()

    await conn.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        balance REAL DEFAULT 0
    )
    """)

    await conn.commit()
    await conn.close()


menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🎟 Купить билеты", callback_data="buy_menu")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ]
)


@dp.message(CommandStart())
async def start(message: Message):
    args = message.text.split()

    conn = await db()

    await conn.execute(
        "INSERT OR IGNORE INTO users(user_id) VALUES(?)",
        (message.from_user.id,)
    )

    await conn.commit()
    await conn.close()

    # ВАЖНО! FIX deep link
    if len(args) > 1:
        param = args[1]

        if param.startswith("join_"):
            giveaway_id = param.split("_")[1]

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎟 Купить билеты",
                            callback_data=f"buy_{giveaway_id}"
                        )
                    ]
                ]
            )

            await message.answer(
                f"🎁 Розыгрыш #{giveaway_id}\n\n"
                f"Нажмите кнопку ниже чтобы участвовать",
                reply_markup=kb
            )

            return

    await message.answer(
        "🎉 Добро пожаловать",
        reply_markup=menu
    )


@dp.callback_query(F.data == "buy_menu")
async def buy_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎟 Купить билеты",
                    callback_data="buy_1"
                )
            ]
        ]
    )

    await callback.message.answer(
        "🎁 Активный розыгрыш",
        reply_markup=kb
    )


@dp.callback_query(F.data.startswith("buy_"))
async def buy(callback: CallbackQuery, state: FSMContext):
    giveaway_id = callback.data.split("_")[1]

    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(BuyState.tickets)

    await callback.message.answer(
        "Введите количество билетов"
    )


@dp.message(BuyState.tickets)
async def process_buy(message: Message, state: FSMContext):
    amount = int(message.text)

    tickets = []

    for i in range(amount):
        tickets.append(str(i + 1))

    await message.answer(
        "✅ Билеты куплены\n\n"
        f"🎟 Ваши номера:\n{', '.join(tickets)}"
    )

    await state.clear()


@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    conn = await db()

    cur = await conn.execute(
        "SELECT balance FROM users WHERE user_id=?",
        (callback.from_user.id,)
    )

    row = await cur.fetchone()

    balance = row[0]

    await conn.close()

    await callback.message.answer(
        f"👤 Профиль\n\n"
        f"💰 Баланс: {balance}$"
    )


# ADMIN
@dp.message(Command("admin"))
async def admin(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Пополнить баланс",
                    callback_data="add_balance"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➖ Снять баланс",
                    callback_data="remove_balance"
                )
            ]
        ]
    )

    await message.answer(
        "⚙️ Админ панель",
        reply_markup=kb
    )


@dp.callback_query(F.data == "add_balance")
async def add_balance_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminBalance.user)

    await callback.message.answer(
        "Введите ID пользователя"
    )


@dp.message(AdminBalance.user)
async def admin_user(message: Message, state: FSMContext):
    await state.update_data(user_id=message.text)
    await state.set_state(AdminBalance.amount)

    await message.answer(
        "Введите сумму"
    )


@dp.message(AdminBalance.amount)
async def admin_amount(message: Message, state: FSMContext):
    data = await state.get_data()

    user_id = int(data["user_id"])
    amount = float(message.text)

    conn = await db()

    await conn.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (amount, user_id)
    )

    await conn.commit()
    await conn.close()

    await message.answer(
        "✅ Баланс изменен"
    )

    await bot.send_message(
        user_id,
        f"💰 Вам начислено {amount}$"
    )

    await state.clear()


async def main():
    await create_tables()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
