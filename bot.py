import os
import sqlite3
import asyncio
import secrets
import string
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN = os.getenv("BOT_TOKEN")
DB = os.getenv("DB_PATH", "business.db")
if not TOKEN:
    raise RuntimeError("Set BOT_TOKEN")

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())


def con():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def now():
    return datetime.now().isoformat(timespec="seconds")


def init():
    db = con()
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS businesses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        invite_code TEXT UNIQUE,
        created_by INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS business_members(
        business_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL UNIQUE,
        name TEXT,
        joined_at TEXT,
        PRIMARY KEY(business_id,user_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        business_id INTEGER,
        name TEXT,
        buy REAL DEFAULT 0,
        extra REAL DEFAULT 0,
        sale REAL,
        status TEXT,
        category TEXT,
        date TEXT,
        notes TEXT,
        photo TEXT,
        p1 TEXT,
        p1_amount REAL DEFAULT 0,
        p2 TEXT,
        p2_amount REAL DEFAULT 0,
        p3 TEXT,
        p3_amount REAL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        who TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        created_at TEXT
    )""")
    # Compatibility with older versions.
    cols = {r["name"] for r in db.execute("PRAGMA table_info(products)").fetchall()}
    for col, definition in [
        ("user_id", "INTEGER"), ("business_id", "INTEGER"), ("buy", "REAL DEFAULT 0"),
        ("extra", "REAL DEFAULT 0"), ("sale", "REAL"), ("status", "TEXT"),
        ("category", "TEXT"), ("date", "TEXT"), ("notes", "TEXT"), ("photo", "TEXT"),
        ("p1", "TEXT"), ("p1_amount", "REAL DEFAULT 0"), ("p2", "TEXT"),
        ("p2_amount", "REAL DEFAULT 0"), ("p3", "TEXT"), ("p3_amount", "REAL DEFAULT 0")
    ]:
        if col not in cols:
            db.execute(f"ALTER TABLE products ADD COLUMN {col} {definition}")
    db.commit()
    db.close()


def make_code(db):
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "BIZ-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.execute("SELECT 1 FROM businesses WHERE invite_code=?", (code,)).fetchone():
            return code


def get_business(uid):
    db = con()
    row = db.execute("""SELECT b.* FROM businesses b
                       JOIN business_members m ON m.business_id=b.id
                       WHERE m.user_id=? LIMIT 1""", (uid,)).fetchone()
    db.close()
    return row


def ensure_business(uid, display_name=""):
    existing = get_business(uid)
    if existing:
        return existing["id"]
    db = con()
    code = make_code(db)
    name = f"Бизнес {display_name or uid}"
    cur = db.execute("INSERT INTO businesses(name,invite_code,created_by) VALUES(?,?,?)", (name, code, uid))
    bid = cur.lastrowid
    db.execute("INSERT INTO business_members(business_id,user_id,name,joined_at) VALUES(?,?,?,?)",
               (bid, uid, display_name or "", now()))
    # Old personal products without a business get attached to the new business.
    db.execute("UPDATE products SET business_id=? WHERE user_id=? AND (business_id IS NULL OR business_id=0)", (bid, uid))
    db.commit(); db.close()
    return bid


def members(uid):
    b = get_business(uid)
    if not b:
        return []
    db = con()
    rows = db.execute("SELECT * FROM business_members WHERE business_id=? ORDER BY joined_at", (b["id"],)).fetchall()
    db.close()
    return rows


def money(x):
    x = float(x or 0)
    s = f"{x:,.0f}" if x.is_integer() else f"{x:,.2f}"
    return s.replace(",", " ") + " ₽"


def parse_money(text):
    value = float(text.replace(" ", "").replace(",", "."))
    if value < 0:
        raise ValueError
    return value


def expense_total(pid):
    db = con(); row = db.execute("SELECT COALESCE(SUM(amount),0) total FROM expenses WHERE product_id=?", (pid,)).fetchone(); db.close()
    return float(row["total"] or 0)


def expense_by_who(pid):
    db = con(); rows = db.execute("SELECT who, COALESCE(SUM(amount),0) total FROM expenses WHERE product_id=? GROUP BY who", (pid,)).fetchall(); db.close()
    return {r["who"]: float(r["total"] or 0) for r in rows}


def cost(p):
    return float(p["buy"] or 0) + float(p["extra"] or 0) + expense_total(p["id"])


def profit(p):
    if p["sale"] is None:
        return 0.0
    return float(p["sale"] or 0) - cost(p)


def get_product(pid, uid):
    b = get_business(uid)
    if not b:
        return None
    db = con(); p = db.execute("SELECT * FROM products WHERE id=? AND business_id=?", (pid, b["id"])).fetchone(); db.close()
    return p


def stats(uid):
    b = get_business(uid)
    if not b:
        return 0, 0, 0, 0, 0
    db = con(); rows = db.execute("SELECT * FROM products WHERE business_id=? ORDER BY id DESC", (b["id"],)).fetchall(); db.close()
    sold = [p for p in rows if p["status"] == "Продан"]
    inventory = [p for p in rows if p["status"] != "Продан"]
    return (
        sum(profit(p) for p in sold),
        sum(cost(p) for p in inventory),
        sum(float(p["sale"] or 0) for p in sold),
        sum(float(p["extra"] or 0) for p in rows) + sum(expense_total(p["id"]) for p in rows),
        len(rows),
    )


def icon(category):
    return {"Телефоны":"📱", "Электроника":"🎧", "Одежда":"👕", "Обувь":"👟", "Аксессуары":"⌚"}.get(category, "📦")


def home_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Общие товары", callback_data="products"), InlineKeyboardButton(text="➕ Добавить", callback_data="add")],
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="stats"), InlineKeyboardButton(text="🤝 Партнёрство", callback_data="partnership")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ])


def home_text(uid):
    ensure_business(uid)
    p, i, r, e, n = stats(uid)
    b = get_business(uid)
    return f"""📦 <b>{escape(b['name'])}</b>\n\n🟢 <b>Чистая прибыль</b>\n{money(p)}\n\n🔵 <b>Вложено в товары</b>\n{money(i)}\n\n🟣 <b>Выручка от продаж</b>\n{money(r)}\n\n🟠 <b>Доп. расходы</b>\n{money(e)}\n\n📦 Всего товаров: <b>{n}</b>"""


def status_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟠 Куплен", callback_data="st:Куплен"), InlineKeyboardButton(text="🔵 В продаже", callback_data="st:В продаже")],
        [InlineKeyboardButton(text="🟢 Продан", callback_data="st:Продан")]
    ])


def category_kb(prefix="cat:"):
    cats = ["Телефоны", "Электроника", "Одежда", "Обувь", "Аксессуары", "Другое"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=cats[i], callback_data=f"{prefix}{cats[i]}"), InlineKeyboardButton(text=cats[i+1], callback_data=f"{prefix}{cats[i+1]}")]
        for i in range(0, 6, 2)
    ])


def partners_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Один участник", callback_data="partners:1"), InlineKeyboardButton(text="👥 Два участника", callback_data="partners:2")],
        [InlineKeyboardButton(text="👥 Три участника", callback_data="partners:3")],
        [InlineKeyboardButton(text="⏭ Без распределения", callback_data="partners:0")]
    ])


def product_kb(pid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit:{pid}"), InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{pid}")],
        [InlineKeyboardButton(text="➕ Добавить расход", callback_data=f"expense:{pid}")],
        [InlineKeyboardButton(text="📋 Таблица сделки", callback_data=f"table:{pid}")],
        [InlineKeyboardButton(text="← К товарам", callback_data="products")]
    ])


async def edit_message(c, text, reply_markup=None, parse_mode="HTML"):
    """Safely edit both text messages and photo messages."""
    msg = c.message
    try:
        if msg and msg.photo:
            return await msg.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        err = str(e)
        if "message is not modified" in err.lower():
            return
        # If Telegram rejects editing (for example after a media transition), send a fresh message.
        try:
            if msg and msg.photo:
                await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            else:
                await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            raise


async def show_product(c, pid):
    p = get_product(pid, c.from_user.id)
    if not p:
        await c.answer("Товар не найден", show_alert=True); return
    st = {"Продан":"🟢", "В продаже":"🔵", "Куплен":"🟠"}.get(p["status"], "⚪")
    ex_total = expense_total(pid); ex_who = expense_by_who(pid)
    people = [(p["p1"], float(p["p1_amount"] or 0)), (p["p2"], float(p["p2_amount"] or 0)), (p["p3"], float(p["p3_amount"] or 0))]
    people = [(n, a) for n, a in people if n]
    partners = ""
    if people:
        partners = "\n\n<b>Участники сделки</b>\n" + "\n".join(f"• {escape(n)} — {money(a)}" for n, a in people)
    ex_lines = ""
    if ex_total:
        ex_lines = f"\n\n<b>Доп. расходы</b>\nВсего: <b>{money(ex_total)}</b>"
        for who, amount in ex_who.items():
            ex_lines += f"\n• {escape(who)}: {money(amount)}"
    sale = "—" if p["sale"] is None else money(p["sale"])
    pr = "После продажи" if p["sale"] is None else money(profit(p))
    text = f"""<b>{icon(p['category'])} {escape(p['name'])}</b>\n{st} <b>{escape(p['status'] or '')}</b> · {escape(p['date'] or '')}\n\n<b>Цены и расходы</b>\nЦена покупки: <b>{money(p['buy'])}</b>\nДоп. расходы при добавлении: <b>{money(p['extra'])}</b>\nОбщая себестоимость: <b>{money(cost(p))}</b>\nЦена продажи: <b>{sale}</b>\n\n🟢 <b>Прибыль: {pr}</b>{partners}{ex_lines}\n\nЗаметки: {escape(p['notes'] or '—')}"""
    if p["photo"]:
        # Replace current message with a clean photo card. This avoids trying to edit text into media.
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer_photo(p["photo"], caption=text, reply_markup=product_kb(pid), parse_mode="HTML")
    else:
        await edit_message(c, text, reply_markup=product_kb(pid), parse_mode="HTML")


def deal_table(p):
    total = cost(p); sale = float(p["sale"] or 0); pr = sale - total
    base = [(p["p1"], float(p["p1_amount"] or 0)), (p["p2"], float(p["p2_amount"] or 0)), (p["p3"], float(p["p3_amount"] or 0))]
    base = [(n, a) for n, a in base if n]
    ex = expense_by_who(p["id"])
    lines = ["📋 <b>ТАБЛИЦА СДЕЛКИ</b>", f"📦 <b>{escape(p['name'])}</b>", "", f"🔴 <b>СЕБЕСТОИМОСТЬ</b> {money(total)}"]
    for n, a in base:
        lines.append(f"   {escape(n)} — базовое вложение {money(a)}")
    if ex:
        lines += ["", f"➕ <b>ДОП. РАСХОДЫ</b> {money(sum(ex.values()))}"]
        for who, amount in ex.items():
            lines.append(f"   {escape(who)} — {money(amount)}")
    lines += ["", f"🟡 <b>ПРОДАЖА</b> {money(sale)}", f"🟢 <b>ПРИБЫЛЬ</b> {money(pr)}"]
    if base and sale > 0:
        lines += ["", "<b>РАСПРЕДЕЛЕНИЕ: сначала возврат вложений, затем прибыль 50/50</b>"]
        if len(base) == 1:
            n, a = base[0]; extra = ex.get(n, 0); invested = a + extra
            lines.append(f"   {escape(n)}: {money(invested + pr)} (возврат {money(invested)} + прибыль {money(pr)})")
        else:
            half = pr / 2
            for n, a in base:
                invested = a + ex.get(n, 0)
                lines.append(f"   {escape(n)}: {money(invested + half)} (возврат {money(invested)} + прибыль {money(half)})")
    elif sale > 0:
        lines.append("\nПри участниках не указано распределение. Продажа отражена в общей статистике.")
    return "\n".join(lines)


class Add(StatesGroup):
    name=State(); buy=State(); extra=State(); sale=State(); status=State(); category=State(); date=State(); notes=State(); photo=State(); partners=State(); p1name=State(); p1amount=State(); p2name=State(); p2amount=State(); p3name=State(); p3amount=State()

class Join(StatesGroup): code=State()
class Expense(StatesGroup): amount=State(); description=State(); who=State()
class Edit(StatesGroup): field=State(); value=State(); photo=State()


@dp.message(CommandStart())
async def start(m: Message):
    ensure_business(m.from_user.id, m.from_user.full_name)
    await m.answer(home_text(m.from_user.id), reply_markup=home_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "home")
async def home(c: CallbackQuery):
    ensure_business(c.from_user.id, c.from_user.full_name)
    await edit_message(c, home_text(c.from_user.id), reply_markup=home_kb())
    await c.answer()


@dp.callback_query(F.data == "products")
async def products(c: CallbackQuery):
    ensure_business(c.from_user.id, c.from_user.full_name)
    b = get_business(c.from_user.id)
    db = con(); rows = db.execute("SELECT * FROM products WHERE business_id=? ORDER BY id DESC", (b["id"],)).fetchall(); db.close()
    kb = []
    for p in rows[:50]:
        s = {"Продан":"🟢", "В продаже":"🔵", "Куплен":"🟠"}.get(p["status"], "⚪")
        kb.append([InlineKeyboardButton(text=f"{icon(p['category'])} {p['name']} · {s}", callback_data=f"item:{p['id']}")])
    kb.append([InlineKeyboardButton(text="➕ Добавить товар", callback_data="add")])
    kb.append([InlineKeyboardButton(text="🏠 Главная", callback_data="home")])
    text = "📦 <b>Общие товары</b>\n\nНажми на товар:" if rows else "📦 <b>Общие товары</b>\n\nПока нет товаров."
    await edit_message(c, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await c.answer()


@dp.callback_query(F.data.startswith("item:"))
async def item(c: CallbackQuery):
    await show_product(c, int(c.data.split(":",1)[1])); await c.answer()


@dp.callback_query(F.data == "partnership")
async def partnership(c: CallbackQuery):
    ensure_business(c.from_user.id, c.from_user.full_name)
    b = get_business(c.from_user.id); ms = members(c.from_user.id)
    lines = [f"🤝 <b>{escape(b['name'])}</b>", "", "<b>Участники:</b>"]
    lines += [f"• {escape(x['name'] or str(x['user_id']))}" for x in ms]
    lines += ["", f"🔑 <b>Код приглашения:</b> <code>{b['invite_code']}</code>", "", "Передай этот код партнёру.", "После входа вы будете видеть один общий список товаров, расходы и статистику."]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Войти по коду", callback_data="join")],
        [InlineKeyboardButton(text="🚪 Выйти из партнёрства", callback_data="leave_partnership")],
        [InlineKeyboardButton(text="🏠 Главная", callback_data="home")]
    ])
    await edit_message(c, "\n".join(lines), reply_markup=kb); await c.answer()


@dp.callback_query(F.data == "leave_partnership")
async def leave_partnership(c: CallbackQuery):
    uid = c.from_user.id
    db = con()
    row = db.execute("SELECT b.* FROM businesses b JOIN business_members m ON m.business_id=b.id WHERE m.user_id=? LIMIT 1", (uid,)).fetchone()
    if not row:
        db.close(); await c.answer("Нет активного бизнеса", show_alert=True); return
    bid = row["id"]
    count = db.execute("SELECT COUNT(*) n FROM business_members WHERE business_id=?", (bid,)).fetchone()["n"]
    db.execute("DELETE FROM business_members WHERE business_id=? AND user_id=?", (bid, uid))
    # If the business becomes empty, remove it and its products. If the partner remains, their shared business stays intact.
    if count <= 1:
        db.execute("DELETE FROM expenses WHERE product_id IN (SELECT id FROM products WHERE business_id=?)", (bid,))
        db.execute("DELETE FROM products WHERE business_id=?", (bid,))
        db.execute("DELETE FROM businesses WHERE id=?", (bid,))
    db.commit(); db.close()
    ensure_business(uid, c.from_user.full_name)
    await edit_message(c, "🚪 <b>Ты вышел из партнёрства.</b>\n\nТеперь можно подключиться к другому бизнесу по коду.", reply_markup=home_kb())
    await c.answer("Готово")


@dp.callback_query(F.data == "join")
async def join(c: CallbackQuery, state: FSMContext):
    await state.clear(); await state.set_state(Join.code)
    await edit_message(c, "🤝 <b>Подключение к партнёру</b>\n\nВведи код приглашения, например <code>BIZ-ABC123</code>.")
    await c.answer()


@dp.message(Join.code)
async def join_code(m: Message, state: FSMContext):
    code = (m.text or "").strip().upper()
    db = con(); b = db.execute("SELECT * FROM businesses WHERE invite_code=?", (code,)).fetchone()
    if not b:
        db.close(); await m.answer("❌ Код не найден. Проверь его ещё раз."); return
    existing = db.execute("SELECT business_id FROM business_members WHERE user_id=?", (m.from_user.id,)).fetchone()
    if existing and existing["business_id"] == b["id"]:
        db.close(); await state.clear(); await m.answer("✅ Ты уже подключён к этому бизнесу.", reply_markup=home_kb()); return
    if existing:
        db.close(); await state.clear(); await m.answer("❌ У тебя уже есть другой бизнес. Открой 🤝 Партнёрство → 🚪 Выйти из партнёрства, затем введи новый код.", reply_markup=home_kb()); return
    count = db.execute("SELECT COUNT(*) n FROM business_members WHERE business_id=?", (b["id"],)).fetchone()["n"]
    if count >= 2:
        db.close(); await state.clear(); await m.answer("❌ В этом бизнесе уже 2 участника.", reply_markup=home_kb()); return
    db.execute("INSERT INTO business_members(business_id,user_id,name,joined_at) VALUES(?,?,?,?)", (b["id"], m.from_user.id, m.from_user.full_name, now()))
    db.commit(); db.close(); await state.clear()
    await m.answer(f"✅ <b>Готово!</b>\n\nТы подключён к <b>{escape(b['name'])}</b>.\nТеперь вы видите один общий бизнес.", reply_markup=home_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "stats")
async def statistics(c: CallbackQuery):
    ensure_business(c.from_user.id, c.from_user.full_name)
    p,i,r,e,n = stats(c.from_user.id)
    text = f"📊 <b>Общая статистика</b>\n\n🟢 Прибыль: <b>{money(p)}</b>\n🔵 Вложено в товары: <b>{money(i)}</b>\n🟣 Выручка: <b>{money(r)}</b>\n🟠 Доп. расходы: <b>{money(e)}</b>\n📦 Товаров: <b>{n}</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главная", callback_data="home")]])
    await edit_message(c, text, reply_markup=kb); await c.answer()


@dp.callback_query(F.data == "profile")
async def profile(c: CallbackQuery):
    ensure_business(c.from_user.id, c.from_user.full_name)
    b = get_business(c.from_user.id); ms = members(c.from_user.id)
    names = ", ".join(escape(x["name"] or str(x["user_id"])) for x in ms)
    text = f"👤 <b>Профиль бизнеса</b>\n\n🤝 Участники: <b>{names}</b>\n💰 Валюта: <b>RUB (₽)</b>\n📦 Учёт: <b>общий</b>\n\nКод партнёрства: <code>{b['invite_code']}</code>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤝 Партнёрство", callback_data="partnership")],[InlineKeyboardButton(text="🏠 Главная", callback_data="home")]])
    await edit_message(c, text, reply_markup=kb); await c.answer()


@dp.callback_query(F.data == "add")
async def add(c: CallbackQuery, state: FSMContext):
    ensure_business(c.from_user.id, c.from_user.full_name)
    await state.clear(); await state.set_state(Add.name)
    await edit_message(c, "➕ <b>Добавить товар</b>\n\n1/15. Название предмета?\nНапример: Nike Air Max")
    await c.answer()


@dp.message(Add.name)
async def a1(m,state): await state.update_data(name=m.text.strip()); await state.set_state(Add.buy); await m.answer("2/15. 💰 Цена покупки (₽)?")
@dp.message(Add.buy)
async def a2(m,state):
    try: v=parse_money(m.text)
    except: return await m.answer("Введи число, например 30000.")
    await state.update_data(buy=v); await state.set_state(Add.extra); await m.answer("3/15. 🚚 Доп. расходы (₽)? Если нет — 0.")
@dp.message(Add.extra)
async def a3(m,state):
    try: v=parse_money(m.text)
    except: return await m.answer("Введи число, например 500.")
    await state.update_data(extra=v); await state.set_state(Add.sale); await m.answer("4/15. 💵 Цена продажи (₽)? Если ещё не продан — 0.")
@dp.message(Add.sale)
async def a4(m,state):
    try: v=parse_money(m.text)
    except: return await m.answer("Введи число, например 60000.")
    await state.update_data(sale=(v if v > 0 else None)); await state.set_state(Add.status); await m.answer("5/15. Статус:", reply_markup=status_kb())
@dp.callback_query(Add.status, F.data.startswith("st:"))
async def a5(c,state): await state.update_data(status=c.data[3:]); await state.set_state(Add.category); await edit_message(c,"6/15. Категория:",reply_markup=category_kb()); await c.answer()
@dp.callback_query(Add.category, F.data.startswith("cat:"))
async def a6(c,state): await state.update_data(category=c.data[4:]); await state.set_state(Add.date); await edit_message(c,"7/15. 📅 Дата покупки?\nДД.ММ.ГГГГ или «сегодня»."); await c.answer()
@dp.message(Add.date)
async def a7(m,state): await state.update_data(date=datetime.now().strftime("%d.%m.%Y") if m.text.strip().lower()=="сегодня" else m.text.strip()); await state.set_state(Add.notes); await m.answer("8/15. 📝 Заметки? Если не нужны — «-».")
@dp.message(Add.notes)
async def a8(m,state): await state.update_data(notes="" if m.text.strip()=="-" else m.text.strip()); await state.set_state(Add.photo); await m.answer("9/15. 📷 Отправь фото товара или напиши «-».")
@dp.message(Add.photo)
async def a9(m,state):
    if not m.photo and (m.text or "").strip()!="-": return await m.answer("Отправь фото или напиши «-».")
    await state.update_data(photo=m.photo[-1].file_id if m.photo else ""); await state.set_state(Add.partners); await m.answer("10/15. 🤝 Кто участвовал в покупке?",reply_markup=partners_kb())
@dp.callback_query(Add.partners, F.data.startswith("partners:"))
async def a10(c,state):
    n=int(c.data.split(":")[1]); await state.update_data(partner_count=n,p1="",p1_amount=0,p2="",p2_amount=0,p3="",p3_amount=0); await state.set_state(Add.p1name)
    await edit_message(c,"11/15. Имя первого участника?\nНапример: Андрей" if n else "11/15. Без распределения. Нажми «-»."); await c.answer()
@dp.message(Add.p1name)
async def a11(m,state):
    d=await state.get_data(); n=d.get("partner_count",0)
    if n==0: await state.set_state(Add.p1amount); return await m.answer("12/15. Напиши 0.")
    await state.update_data(p1=m.text.strip()); await state.set_state(Add.p1amount); await m.answer("12/15. Сколько он вложил (₽)?")
@dp.message(Add.p1amount)
async def a12(m,state):
    try:v=parse_money(m.text)
    except:return await m.answer("Введи число.")
    d=await state.get_data(); await state.update_data(p1_amount=v); await state.set_state(Add.p2name); await m.answer("13/15. Имя второго участника?" if d.get("partner_count",0)>=2 else "13/15. Напиши «-».")
@dp.message(Add.p2name)
async def a13(m,state):
    d=await state.get_data(); await state.update_data(p2=m.text.strip() if d.get("partner_count",0)>=2 else ""); await state.set_state(Add.p2amount); await m.answer("14/15. Сколько вложил второй участник? Если нет — 0.")
@dp.message(Add.p2amount)
async def a14(m,state):
    try:v=parse_money(m.text)
    except:return await m.answer("Введи число.")
    d=await state.get_data(); await state.update_data(p2_amount=v)
    if d.get("partner_count",0)>=3: await state.set_state(Add.p3name); await m.answer("15/15. Имя третьего участника?")
    else: await finish_product(m,state)
@dp.message(Add.p3name)
async def a15(m,state): await state.update_data(p3=m.text.strip()); await state.set_state(Add.p3amount); await m.answer("Последний шаг. Сколько вложил третий участник (₽)?")
@dp.message(Add.p3amount)
async def a16(m,state):
    try:v=parse_money(m.text)
    except:return await m.answer("Введи число.")
    await state.update_data(p3_amount=v); await finish_product(m,state)


async def finish_product(m,state):
    d=await state.get_data(); bid=ensure_business(m.from_user.id,m.from_user.full_name)
    db=con(); db.execute("""INSERT INTO products(user_id,business_id,name,buy,extra,sale,status,category,date,notes,photo,p1,p1_amount,p2,p2_amount,p3,p3_amount)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(m.from_user.id,bid,d["name"],d["buy"],d["extra"],d["sale"],d["status"],d["category"],d["date"],d["notes"],d["photo"],d.get("p1",""),d.get("p1_amount",0),d.get("p2",""),d.get("p2_amount",0),d.get("p3",""),d.get("p3_amount",0)))
    db.commit(); db.close(); await state.clear(); await m.answer("✅ <b>Товар сохранён!</b>\n\nОн находится в общем бизнесе и виден партнёру.",reply_markup=home_kb(),parse_mode="HTML")


@dp.callback_query(F.data.startswith("expense:"))
async def expense_start(c,state):
    pid=int(c.data.split(":",1)[1]); p=get_product(pid,c.from_user.id)
    if not p:return await c.answer("Товар не найден",show_alert=True)
    await state.clear(); await state.update_data(product_id=pid); await state.set_state(Expense.amount)
    await edit_message(c,f"➕ <b>Новый расход</b>\n\nТовар: <b>{escape(p['name'])}</b>\n\n1/3. Сумма расхода в ₽:"); await c.answer()
@dp.message(Expense.amount)
async def expense_amount(m,state):
    try:v=parse_money(m.text)
    except:return await m.answer("Введи положительную сумму, например 1500.")
    if v<=0:return await m.answer("Сумма должна быть больше 0.")
    await state.update_data(amount=v); await state.set_state(Expense.description); await m.answer("2/3. На что потратили? Например: доставка, ремонт, упаковка.")
@dp.message(Expense.description)
async def expense_description(m,state):
    await state.update_data(description=(m.text or "").strip()); await state.set_state(Expense.who)
    # Payer buttons use actual business member names instead of hard-coded names.
    ms=members(m.from_user.id); names=[]
    for x in ms:
        n=(x["name"] or "").strip()
        if n and n not in names: names.append(n)
    buttons=[[InlineKeyboardButton(text=f"👤 {n[:30]}",callback_data=f"expensewho:{n}")] for n in names[:2]]
    buttons.append([InlineKeyboardButton(text="👤 Другой",callback_data="expensewho:Другой")])
    await m.answer("3/3. Кто оплатил этот расход?",reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
@dp.callback_query(Expense.who, F.data.startswith("expensewho:"))
async def expense_who(c,state):
    who=c.data.split(":",1)[1]; d=await state.get_data(); pid=int(d["product_id"]); p=get_product(pid,c.from_user.id)
    if not p: await state.clear(); return await c.answer("Товар не найден",show_alert=True)
    db=con(); db.execute("INSERT INTO expenses(product_id,user_id,who,amount,description,created_at) VALUES(?,?,?,?,?,?)",(pid,c.from_user.id,who,float(d["amount"]),d.get("description",""),now())); db.commit(); db.close(); await state.clear()
    p=get_product(pid,c.from_user.id); await edit_message(c,f"✅ <b>Расход добавлен</b>\n\nТовар: <b>{escape(p['name'])}</b>\nРасход: <b>{money(d['amount'])}</b>\nОплатил: <b>{escape(who)}</b>\n\nНовая себестоимость: <b>{money(cost(p))}</b>",reply_markup=product_kb(pid)); await c.answer()


@dp.callback_query(F.data.startswith("table:"))
async def table(c):
    pid=int(c.data.split(":",1)[1]); p=get_product(pid,c.from_user.id)
    if not p:return await c.answer("Товар не найден",show_alert=True)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← К товару",callback_data=f"item:{pid}")]])
    await edit_message(c,deal_table(p),reply_markup=kb); await c.answer()


@dp.callback_query(F.data.startswith("del:"))
async def delete_product(c):
    pid=int(c.data.split(":",1)[1]); p=get_product(pid,c.from_user.id)
    if not p:return await c.answer("Товар не найден",show_alert=True)
    db=con(); db.execute("DELETE FROM expenses WHERE product_id=?",(pid,)); db.execute("DELETE FROM products WHERE id=?",(pid,)); db.commit(); db.close()
    await edit_message(c,"🗑 <b>Товар удалён.</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← К товарам",callback_data="products")],[InlineKeyboardButton(text="🏠 Главная",callback_data="home")]])); await c.answer()


@dp.callback_query(F.data.startswith("edit:"))
async def edit_menu(c,state):
    pid=int(c.data.split(":",1)[1]); p=get_product(pid,c.from_user.id)
    if not p:return await c.answer("Товар не найден",show_alert=True)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Название",callback_data=f"ef:name:{pid}"),InlineKeyboardButton(text="Цена покупки",callback_data=f"ef:buy:{pid}")],
        [InlineKeyboardButton(text="Доп. расходы",callback_data=f"ef:extra:{pid}"),InlineKeyboardButton(text="Цена продажи",callback_data=f"ef:sale:{pid}")],
        [InlineKeyboardButton(text="Статус",callback_data=f"ef:status:{pid}"),InlineKeyboardButton(text="Категория",callback_data=f"ef:category:{pid}")],
        [InlineKeyboardButton(text="Заметки",callback_data=f"ef:notes:{pid}"),InlineKeyboardButton(text="Фото",callback_data=f"ef:photo:{pid}")],
        [InlineKeyboardButton(text="← К товару",callback_data=f"item:{pid}")]
    ])
    await edit_message(c,"✏️ <b>Что изменить?</b>",reply_markup=kb); await c.answer()


@dp.callback_query(F.data.startswith("ef:"))
async def edit_field(c,state):
    _, field, pid_s = c.data.split(":",2); pid=int(pid_s); p=get_product(pid,c.from_user.id)
    if not p:return await c.answer("Товар не найден",show_alert=True)
    await state.clear(); await state.update_data(pid=pid,field=field)
    if field=="status":
        await edit_message(c,"Выбери новый статус:",reply_markup=status_kb()); await state.set_state(Edit.field); await c.answer(); return
    if field=="category":
        await edit_message(c,"Выбери категорию:",reply_markup=category_kb("editcat:")); await state.set_state(Edit.field); await c.answer(); return
    if field=="photo":
        await edit_message(c,"📷 Отправь новое фото товара."); await state.set_state(Edit.photo); await c.answer(); return
    prompts={"name":"Новое название:","buy":"Новая цена покупки в ₽:","extra":"Новая сумма доп. расходов при добавлении в ₽:","sale":"Новая цена продажи в ₽. Для снятия продажи введи 0.","notes":"Новые заметки. Введи «-», чтобы очистить."}
    await edit_message(c,prompts[field]); await state.set_state(Edit.value); await c.answer()

@dp.callback_query(Edit.field, F.data.startswith("st:"))
async def edit_status(c,state):
    d=await state.get_data(); pid=int(d["pid"]); db=con(); db.execute("UPDATE products SET status=? WHERE id=?",(c.data[3:],pid)); db.commit(); db.close(); await state.clear(); await show_product(c,pid); await c.answer("Статус изменён")
@dp.callback_query(Edit.field, F.data.startswith("editcat:"))
async def edit_category(c,state):
    d=await state.get_data(); pid=int(d["pid"]); db=con(); db.execute("UPDATE products SET category=? WHERE id=?",(c.data[8:],pid)); db.commit(); db.close(); await state.clear(); await show_product(c,pid); await c.answer("Категория изменена")
@dp.message(Edit.value)
async def edit_value(m,state):
    d=await state.get_data(); pid=int(d["pid"]); field=d["field"]
    if field in {"buy","extra","sale"}:
        try:v=parse_money(m.text)
        except:return await m.answer("Введи число.")
        if field=="sale": v = v if v>0 else None
        db=con(); db.execute(f"UPDATE products SET {field}=? WHERE id=?",(v,pid)); db.commit(); db.close()
    else:
        v="" if field=="notes" and (m.text or "").strip()=="-" else (m.text or "").strip()
        db=con(); db.execute(f"UPDATE products SET {field}=? WHERE id=?",(v,pid)); db.commit(); db.close()
    await state.clear(); await m.answer("✅ Изменено.",reply_markup=home_kb())
@dp.message(Edit.photo)
async def edit_photo(m,state):
    if not m.photo:return await m.answer("Отправь фотографию товара.")
    d=await state.get_data(); pid=int(d["pid"]); db=con(); db.execute("UPDATE products SET photo=? WHERE id=?",(m.photo[-1].file_id,pid)); db.commit(); db.close(); await state.clear(); await m.answer("✅ Фото обновлено.",reply_markup=home_kb())


async def main():
    init()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
