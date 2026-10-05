import os, sqlite3, asyncio, secrets, string
from datetime import datetime
from aiogram import Bot, Dispatcher, F
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
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = con()
    c.execute("""CREATE TABLE IF NOT EXISTS businesses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        invite_code TEXT UNIQUE,
        created_by INTEGER NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS business_members(
        business_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL UNIQUE,
        name TEXT,
        joined_at TEXT,
        PRIMARY KEY(business_id,user_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        who TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        business_id INTEGER,
        name TEXT,
        buy REAL,
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
    # Upgrade an older installation that has products.user_id but no business_id.
    cols = [r["name"] for r in c.execute("PRAGMA table_info(products)").fetchall()]
    if "business_id" not in cols:
        c.execute("ALTER TABLE products ADD COLUMN business_id INTEGER")
    if "user_id" not in cols:
        c.execute("ALTER TABLE products ADD COLUMN user_id INTEGER")
    c.commit()
    c.close()

def ensure_business(uid, display_name=None):
    c = con()
    row = c.execute("SELECT b.id FROM businesses b JOIN business_members m ON m.business_id=b.id WHERE m.user_id=?", (uid,)).fetchone()
    if row:
        bid = row["id"]
        c.close()
        return bid

    # Reuse an existing personal business owned by this user if present.
    row = c.execute("SELECT id FROM businesses WHERE created_by=? ORDER BY id LIMIT 1", (uid,)).fetchone()
    if row:
        bid = row["id"]
    else:
        name = f"Бизнес {display_name or uid}"
        code = make_code(c)
        cur = c.execute("INSERT INTO businesses(name,invite_code,created_by) VALUES(?,?,?)", (name, code, uid))
        bid = cur.lastrowid

    c.execute(
        "INSERT OR IGNORE INTO business_members(business_id,user_id,name,joined_at) VALUES(?,?,?,?)",
        (bid, uid, display_name or "", datetime.now().isoformat(timespec="seconds"))
    )
    # Migrate old products owned by this user into their business.
    c.execute("UPDATE products SET business_id=? WHERE user_id=? AND (business_id IS NULL OR business_id=0)", (bid, uid))
    c.commit()
    c.close()
    return bid

def make_code(c):
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "BIZ-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not c.execute("SELECT 1 FROM businesses WHERE invite_code=?", (code,)).fetchone():
            return code

def get_business(uid):
    c = con()
    row = c.execute("""SELECT b.* FROM businesses b
                       JOIN business_members m ON m.business_id=b.id
                       WHERE m.user_id=? LIMIT 1""", (uid,)).fetchone()
    c.close()
    return row

def members(uid):
    b = get_business(uid)
    if not b:
        return []
    c = con()
    rows = c.execute("""SELECT name,user_id FROM business_members
                        WHERE business_id=? ORDER BY joined_at""", (b["id"],)).fetchall()
    c.close()
    return rows

def money(x):
    x = float(x or 0)
    s = f"{x:,.0f}" if x.is_integer() else f"{x:,.2f}"
    return s.replace(",", " ") + " ₽"

def expense_total(product_id):
    c = con()
    row = c.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE product_id=?", (product_id,)).fetchone()
    c.close()
    return float(row["total"] or 0)

def expense_by_who(product_id):
    c = con()
    rows = c.execute("SELECT who, COALESCE(SUM(amount),0) AS total FROM expenses WHERE product_id=? GROUP BY who", (product_id,)).fetchall()
    c.close()
    return {r["who"]: float(r["total"] or 0) for r in rows}

def cost(p):
    return float(p["buy"] or 0) + float(p["extra"] or 0) + expense_total(p["id"])

def profit(p):
    return float(p["sale"] or 0) - cost(p)

def stats(uid):
    b = get_business(uid)
    if not b:
        return 0, 0, 0, 0, 0
    c = con()
    a = c.execute("SELECT * FROM products WHERE business_id=? ORDER BY id DESC", (b["id"],)).fetchall()
    c.close()
    sold = [x for x in a if x["status"] == "Продан"]
    return (
        sum(profit(x) for x in sold),
        sum(cost(x) for x in a if x["status"] != "Продан"),
        sum(float(x["sale"] or 0) for x in sold),
        sum(float(x["extra"] or 0) for x in a) + sum(expense_total(x["id"]) for x in a),
        len(a),
    )

def home_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Общие товары", callback_data="products"),
         InlineKeyboardButton(text="➕ Добавить", callback_data="add")],
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="stats"),
         InlineKeyboardButton(text="🤝 Партнёрство", callback_data="partnership")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ])

def home_text(uid):
    ensure_business(uid)
    p, i, r, e, n = stats(uid)
    b = get_business(uid)
    return f"""📦 <b>{b['name']}</b>

🟢 <b>Чистая прибыль</b>
{money(p)}

🔵 <b>Вложено в товары</b>
{money(i)}

🟣 <b>Выручка от продаж</b>
{money(r)}

🟠 <b>Доп. расходы</b>
{money(e)}

📦 Всего товаров: <b>{n}</b>"""

def icon(cat):
    return {"Телефоны":"📱","Электроника":"🎧","Одежда":"👕","Обувь":"👟","Аксессуары":"⌚"}.get(cat, "📦")

class Add(StatesGroup):
    name=State(); buy=State(); extra=State(); sale=State(); status=State()
    category=State(); date=State(); notes=State(); photo=State()
    partners=State(); p1name=State(); p1amount=State(); p2name=State()
    p2amount=State(); p3name=State(); p3amount=State()

class Join(StatesGroup):
    code = State()

class Expense(StatesGroup):
    amount = State()
    description = State()
    who = State()

def status_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟠 Куплен", callback_data="st:Куплен"),
         InlineKeyboardButton(text="🔵 В продаже", callback_data="st:В продаже")],
        [InlineKeyboardButton(text="🟢 Продан", callback_data="st:Продан")]
    ])

def category_kb():
    cats=["Телефоны","Электроника","Одежда","Обувь","Аксессуары","Другое"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=cats[i], callback_data=f"cat:{cats[i]}"),
         InlineKeyboardButton(text=cats[i+1], callback_data=f"cat:{cats[i+1]}")]
        for i in range(0,6,2)
    ])

def partners_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Один участник", callback_data="partners:1"),
         InlineKeyboardButton(text="👥 Два участника", callback_data="partners:2")],
        [InlineKeyboardButton(text="👥 Три участника", callback_data="partners:3")],
        [InlineKeyboardButton(text="⏭ Без распределения", callback_data="partners:0")]
    ])

def product_kb(pid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit:{pid}"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{pid}")],
        [InlineKeyboardButton(text="➕ Добавить расход", callback_data=f"expense:{pid}")],
        [InlineKeyboardButton(text="📋 Таблица сделки", callback_data=f"table:{pid}")],
        [InlineKeyboardButton(text="← К товарам", callback_data="products")]
    ])

def deal_table(p):
    total=cost(p); sale=float(p["sale"] or 0); pr=sale-total
    people=[(p["p1"],p["p1_amount"]),(p["p2"],p["p2_amount"]),(p["p3"],p["p3_amount"])]
    people=[(n,float(a or 0)) for n,a in people if n]
    lines=[f"📋 <b>ТАБЛИЦА СДЕЛКИ</b>", f"📦 <b>{p['name']}</b>", "",
           f"🔴 <b>СТОИМОСТЬ + РАСХОДЫ</b>   {money(total)}"]
    for n,a in people:
        lines.append(f"   {n} — {money(a)}")
    ex = expense_by_who(p["id"])
    ex_total = sum(ex.values())
    if ex_total:
        lines += ["", f"➕ <b>ДОП. РАСХОДЫ</b>   {money(ex_total)}"]
        for who, amount in ex.items():
            lines.append(f"   {who} — {money(amount)}")
    lines += ["",f"🟡 <b>ПРОДАЖА</b>   {money(sale)}",
              f"🟢 <b>ПРИБЫЛЬ</b>   {money(pr)}",""]
    if people:
        invested=sum(a for _,a in people)
        if invested>0 and sale>0:
            lines.append("<b>РАСПРЕДЕЛЕНИЕ ПРОДАЖИ</b>")
            # Прибыль делится строго 50/50. Сначала каждый получает свою
            # сумму вложений, затем половину общей прибыли. Доп. расходы
            # уже входят в себестоимость товара.
            half_profit = pr / 2
            for n,a in people:
                payout = a + half_profit
                lines.append(f"   {n}: {money(payout)}  (возврат {money(a)} + прибыль {money(half_profit)})")
        elif sale>0:
            lines.append("<b>РАСПРЕДЕЛЕНИЕ ПРОДАЖИ</b>")
            eq=sale/len(people)
            for n,_ in people: lines.append(f"   {n}: {money(eq)}")
    return "\n".join(lines)

@dp.message(CommandStart())
async def start(m):
    ensure_business(m.from_user.id, m.from_user.full_name)
    await m.answer(home_text(m.from_user.id), reply_markup=home_kb(), parse_mode="HTML")

@dp.callback_query(F.data=="home")
async def home(c):
    ensure_business(c.from_user.id, c.from_user.full_name)
    await c.message.edit_text(home_text(c.from_user.id), reply_markup=home_kb(), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data=="partnership")
async def partnership(c):
    ensure_business(c.from_user.id, c.from_user.full_name)
    b=get_business(c.from_user.id)
    ms=members(c.from_user.id)
    lines=[f"🤝 <b>{b['name']}</b>", ""]
    lines.append("<b>Участники:</b>")
    for x in ms:
        lines.append(f"• {x['name'] or x['user_id']}")
    lines += ["", f"🔑 <b>Код приглашения:</b> <code>{b['invite_code']}</code>",
              "", "Передай этот код партнёру. Он откроет здесь «Войти по коду» и введёт его.",
              "", "После присоединения вы оба будете видеть одни и те же товары и общую статистику."]
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Войти по коду", callback_data="join")],
        [InlineKeyboardButton(text="🏠 Главная", callback_data="home")]
    ])
    await c.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data=="join")
async def join(c,state):
    await state.clear(); await state.set_state(Join.code)
    await c.message.edit_text("🤝 <b>Подключение к партнёру</b>\n\nВведи код приглашения, например <code>BIZ-ABC123</code>.", parse_mode="HTML")
    await c.answer()

@dp.message(Join.code)
async def join_code(m,state):
    code=m.text.strip().upper()
    c=con()
    b=c.execute("SELECT * FROM businesses WHERE invite_code=?", (code,)).fetchone()
    if not b:
        c.close()
        return await m.answer("❌ Такой код не найден. Проверь код и попробуй ещё раз.")
    existing=c.execute("SELECT business_id FROM business_members WHERE user_id=?", (m.from_user.id,)).fetchone()
    if existing and existing["business_id"] != b["id"]:
        c.close()
        await state.clear()
        return await m.answer("❌ У тебя уже есть другое партнёрство. Сначала нужно выйти из него.", reply_markup=home_kb())
    c.execute("INSERT OR IGNORE INTO business_members(business_id,user_id,name,joined_at) VALUES(?,?,?,?)",
              (b["id"],m.from_user.id,m.from_user.full_name,datetime.now().isoformat(timespec="seconds")))
    c.execute("UPDATE products SET business_id=? WHERE user_id=? AND (business_id IS NULL OR business_id=0)", (b["id"],m.from_user.id))
    c.commit(); c.close(); await state.clear()
    await m.answer(f"✅ <b>Готово!</b>\n\nТы подключён к бизнесу <b>{b['name']}</b>.\nТеперь вы с партнёром видите общий список товаров и общую статистику.", parse_mode="HTML", reply_markup=home_kb())

@dp.callback_query(F.data.in_({"products", "back_products"}))
async def products(c):
    ensure_business(c.from_user.id, c.from_user.full_name)
    b=get_business(c.from_user.id)
    db=con(); rows=db.execute("SELECT * FROM products WHERE business_id=? ORDER BY id DESC",(b["id"],)).fetchall(); db.close()
    kb=[]
    for p in rows[:40]:
        s={"Продан":"🟢","В продаже":"🔵","Куплен":"🟠"}.get(p["status"],"⚪")
        kb.append([InlineKeyboardButton(text=f"{icon(p['category'])} {p['name']} · {s}",callback_data=f"item:{p['id']}")])
    kb += [[InlineKeyboardButton(text="➕ Добавить товар",callback_data="add")],[InlineKeyboardButton(text="🏠 Главная",callback_data="home")]]
    text="📦 <b>Общие товары</b>\n\nНажми на товар:" if rows else "📦 <b>Общие товары</b>\n\nПока нет товаров."
    await c.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),parse_mode="HTML"); await c.answer()

def get_product_for_user(pid, uid):
    b=get_business(uid)
    if not b: return None
    db=con(); p=db.execute("SELECT * FROM products WHERE id=? AND business_id=?",(pid,b["id"])).fetchone(); db.close()
    return p

@dp.callback_query(F.data.startswith("item:"))
async def item(c):
    pid=int(c.data.split(":")[1]); p=get_product_for_user(pid,c.from_user.id)
    if not p:return await c.answer("Товар не найден",show_alert=True)
    st={"Продан":"🟢","В продаже":"🔵","Куплен":"🟠"}[p["status"]]
    sale=money(p["sale"]) if p["sale"] is not None else "—"
    pr=money(profit(p)) if p["sale"] is not None else "После продажи"
    ex_total = expense_total(p["id"])
    ex_who = expense_by_who(p["id"])
    expense_lines = ""
    if ex_total:
        expense_lines = f"\\n\\n<b>Дополнительные расходы</b>\\nВсего: <b>{money(ex_total)}</b>"
        if ex_who.get("Андрей"):
            expense_lines += f"\\n• Андрей: {money(ex_who['Андрей'])}"
        if ex_who.get("Кирилл"):
            expense_lines += f"\\n• Кирилл: {money(ex_who['Кирилл'])}"
        if ex_who.get("Другой"):
            expense_lines += f"\\n• Другой: {money(ex_who['Другой'])}"
    partners=""
    names=[p["p1"],p["p2"],p["p3"]]
    if any(names):
        partners="\n\n<b>Участники сделки</b>\n"+"\n".join(f"• {p[f'p{i}']} — {money(p[f'p{i}_amount'])}" for i in range(1,4) if p[f'p{i}'])
    text=f"""<b>{icon(p['category'])} {p['name']}</b>
{st} <b>{p['status']}</b> · {p['date']}

<b>Цены и расходы</b>
Цена покупки: <b>{money(p['buy'])}</b>
Доп. расходы: <b>{money(p['extra'])}</b>
Общая сумма вложений: <b>{money(cost(p))}</b>
Цена продажи: <b>{sale}</b>

🟢 <b>Прибыль: {pr}</b>{partners}
Заметки: {p['notes'] or '—'}"""
    if p["photo"]:
        try: await c.message.delete(); await c.message.answer_photo(p["photo"],caption=text,reply_markup=product_kb(pid),parse_mode="HTML")
        except: await c.message.edit_text(text,reply_markup=product_kb(pid),parse_mode="HTML")
    else: await c.message.edit_text(text,reply_markup=product_kb(pid),parse_mode="HTML")
    await c.answer()


@dp.callback_query(F.data.startswith("expense:"))
async def expense_start(c, state):
    pid = int(c.data.split(":")[1])
    p = get_product_for_user(pid, c.from_user.id)
    if not p:
        return await c.answer("Товар не найден", show_alert=True)
    await state.clear()
    await state.update_data(product_id=pid)
    await state.set_state(Expense.amount)
    await c.message.edit_text(
        f"➕ <b>Новый расход</b>\n\nТовар: <b>{p['name']}</b>\n\n"
        "1/3. Введи сумму расхода в ₽:",
        parse_mode="HTML"
    )
    await c.answer()

@dp.message(Expense.amount)
async def expense_amount(m, state):
    try:
        value = float(m.text.replace(" ", "").replace(",", "."))
        if value <= 0:
            raise ValueError
    except:
        return await m.answer("Введи положительную сумму, например 1500.")
    await state.update_data(amount=value)
    await state.set_state(Expense.description)
    await m.answer("2/3. На что потратили?\nНапример: доставка, ремонт, упаковка.")

@dp.message(Expense.description)
async def expense_description(m, state):
    await state.update_data(description=m.text.strip())
    await state.set_state(Expense.who)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Андрей", callback_data="expensewho:Андрей")],
        [InlineKeyboardButton(text="👤 Кирилл", callback_data="expensewho:Кирилл")],
        [InlineKeyboardButton(text="👤 Другой", callback_data="expensewho:Другой")]
    ])
    await m.answer("3/3. Кто оплатил этот расход?", reply_markup=kb)

@dp.callback_query(Expense.who, F.data.startswith("expensewho:"))
async def expense_who(c, state):
    who = c.data.split(":", 1)[1]
    d = await state.get_data()
    pid = int(d["product_id"])
    p = get_product_for_user(pid, c.from_user.id)
    if not p:
        await state.clear()
        return await c.answer("Товар не найден", show_alert=True)
    db = con()
    db.execute(
        "INSERT INTO expenses(product_id,user_id,who,amount,description,created_at) VALUES(?,?,?,?,?,?)",
        (pid, c.from_user.id, who, float(d["amount"]), d["description"], datetime.now().isoformat(timespec="seconds"))
    )
    db.commit()
    db.close()
    await state.clear()
    p = get_product_for_user(pid, c.from_user.id)
    new_cost = cost(p)
    await c.message.edit_text(
        f"✅ <b>Расход добавлен</b>\n\n"
        f"Товар: <b>{p['name']}</b>\n"
        f"Расход: <b>{money(d['amount'])}</b>\n"
        f"Оплатил: <b>{who}</b>\n\n"
        f"Теперь общая себестоимость товара: <b>{money(new_cost)}</b>",
        reply_markup=product_kb(pid),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data.startswith("table:"))
async def table(c):
    pid=int(c.data.split(":")[1]); p=get_product_for_user(pid,c.from_user.id)
    if not p:return await c.answer("Не найдено",show_alert=True)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← К товару",callback_data=f"item:{pid}")]])
    await c.message.edit_text(deal_table(p),reply_markup=kb,parse_mode="HTML"); await c.answer()

@dp.callback_query(F.data=="add")
async def add(c,state):
    ensure_business(c.from_user.id,c.from_user.full_name)
    await state.clear(); await state.set_state(Add.name)
    await c.message.edit_text("➕ <b>Добавить товар</b>\n\n1/15. Название предмета?\nНапример: Nike Air Max",parse_mode="HTML"); await c.answer()

@dp.message(Add.name)
async def a1(m,state): await state.update_data(name=m.text.strip()); await state.set_state(Add.buy); await m.answer("2/15. 💰 Цена покупки (₽)?")
@dp.message(Add.buy)
async def a2(m,state):
    try:v=float(m.text.replace(" ","").replace(",","."))
    except:return await m.answer("Введи число, например 40000.")
    await state.update_data(buy=v); await state.set_state(Add.extra); await m.answer("3/15. 🚚 Доп. расходы (₽)? Если нет — 0.")
@dp.message(Add.extra)
async def a3(m,state):
    try:v=float(m.text.replace(" ","").replace(",","."))
    except:return await m.answer("Введи число, например 500.")
    await state.update_data(extra=v); await state.set_state(Add.sale); await m.answer("4/15. 💵 Цена продажи (₽)? Если ещё не продал — 0.")
@dp.message(Add.sale)
async def a4(m,state):
    try:v=float(m.text.replace(" ","").replace(",","."))
    except:return await m.answer("Введи число, например 75000.")
    await state.update_data(sale=v or None); await state.set_state(Add.status); await m.answer("5/15. Статус:",reply_markup=status_kb())
@dp.callback_query(Add.status,F.data.startswith("st:"))
async def a5(c,state):
    await state.update_data(status=c.data[3:]); await state.set_state(Add.category); await c.message.edit_text("6/15. Категория:",reply_markup=category_kb()); await c.answer()
@dp.callback_query(Add.category,F.data.startswith("cat:"))
async def a6(c,state):
    await state.update_data(category=c.data[4:]); await state.set_state(Add.date); await c.message.edit_text("7/15. 📅 Дата покупки?\nДД.ММ.ГГГГ или «сегодня»."); await c.answer()
@dp.message(Add.date)
async def a7(m,state):
    d=m.text.strip(); d=datetime.now().strftime("%d.%m.%Y") if d.lower()=="сегодня" else d
    await state.update_data(date=d); await state.set_state(Add.notes); await m.answer("8/15. 📝 Заметки? Если не нужны — «-».")
@dp.message(Add.notes)
async def a8(m,state):
    await state.update_data(notes="" if m.text.strip()=="-" else m.text.strip()); await state.set_state(Add.photo); await m.answer("9/15. 📷 Отправь фото товара или напиши «-».")
@dp.message(Add.photo)
async def a9(m,state):
    if not m.photo and m.text.strip()!="-": return await m.answer("Отправь фото или напиши «-».")
    await state.update_data(photo=m.photo[-1].file_id if m.photo else "")
    await state.set_state(Add.partners); await m.answer("10/15. 🤝 Кто участвовал в покупке?",reply_markup=partners_kb())
@dp.callback_query(Add.partners,F.data.startswith("partners:"))
async def a10(c,state):
    n=int(c.data.split(":")[1]); await state.update_data(partner_count=n)
    if n==0:
        await state.set_state(Add.p1name); await state.update_data(p1="",p1_amount=0,p2="",p2_amount=0,p3="",p3_amount=0)
        await c.message.edit_text("11/15. Таблица без участников.\nНапиши «-», чтобы продолжить.")
    else:
        await state.set_state(Add.p1name); await c.message.edit_text("11/15. Имя первого участника?\nНапример: Андрей")
    await c.answer()
@dp.message(Add.p1name)
async def a11(m,state):
    d=await state.get_data(); n=d.get("partner_count",0)
    if n==0:
        await state.set_state(Add.p1amount); await m.answer("12/15. Напиши 0."); return
    await state.update_data(p1=m.text.strip()); await state.set_state(Add.p1amount); await m.answer("12/15. Сколько он вложил (₽)?")
@dp.message(Add.p1amount)
async def a12(m,state):
    try:v=float(m.text.replace(" ","").replace(",","."))
    except:return await m.answer("Введи число.")
    d=await state.get_data(); await state.update_data(p1_amount=v)
    if d.get("partner_count",0)>=2: await state.set_state(Add.p2name); await m.answer("13/15. Имя второго участника?")
    else: await state.set_state(Add.p2name); await m.answer("13/15. Напиши «-».")
@dp.message(Add.p2name)
async def a13(m,state):
    d=await state.get_data()
    if d.get("partner_count",0)>=2: await state.update_data(p2=m.text.strip())
    else: await state.update_data(p2="")
    await state.set_state(Add.p2amount); await m.answer("14/15. Сколько вложил второй участник? Если нет — 0.")
@dp.message(Add.p2amount)
async def a14(m,state):
    try:v=float(m.text.replace(" ","").replace(",","."))
    except:return await m.answer("Введи число.")
    d=await state.get_data(); await state.update_data(p2_amount=v)
    if d.get("partner_count",0)>=3:
        await state.set_state(Add.p3name); await m.answer("15/15. Имя третьего участника?")
    else:
        await state.update_data(p3="",p3_amount=0); await finish(m,state)
@dp.message(Add.p3name)
async def a15(m,state):
    await state.update_data(p3=m.text.strip()); await state.set_state(Add.p3amount); await m.answer("Последний шаг. Сколько вложил третий участник (₽)?")
@dp.message(Add.p3amount)
async def a16(m,state):
    try:v=float(m.text.replace(" ","").replace(",","."))
    except:return await m.answer("Введи число.")
    await state.update_data(p3_amount=v); await finish(m,state)

async def finish(m,state):
    d=await state.get_data(); bid=ensure_business(m.from_user.id,m.from_user.full_name)
    db=con()
    cur=db.execute("""INSERT INTO products(user_id,business_id,name,buy,extra,sale,status,category,date,notes,photo,p1,p1_amount,p2,p2_amount,p3,p3_amount)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (m.from_user.id,bid,d["name"],d["buy"],d["extra"],d["sale"],d["status"],d["category"],d["date"],d["notes"],d["photo"],
       d.get("p1",""),d.get("p1_amount",0),d.get("p2",""),d.get("p2_amount",0),d.get("p3",""),d.get("p3_amount",0)))
    db.commit(); db.close(); await state.clear()
    await m.answer("✅ <b>Товар сохранён в общем бизнесе!</b>\n\nТеперь его увидит и партнёр.",parse_mode="HTML",reply_markup=home_kb())

@dp.callback_query(F.data.startswith("del:"))
async def dele(c):
    pid=int(c.data[4:]); p=get_product_for_user(pid,c.from_user.id)
    if not p:return await c.answer("Товар не найден",show_alert=True)
    db=con(); db.execute("DELETE FROM products WHERE id=?",(pid,)); db.commit(); db.close()
    await c.message.edit_text("🗑 Товар удалён из общего бизнеса.",reply_markup=home_kb()); await c.answer()

@dp.callback_query(F.data.startswith("edit:"))
async def edit_stub(c):
    await c.answer("Редактирование пока оставлено как в текущей версии.",show_alert=True)

@dp.callback_query(F.data=="stats")
async def statistics(c):
    ensure_business(c.from_user.id,c.from_user.full_name)
    p,i,r,e,n=stats(c.from_user.id)
    await c.message.edit_text(f"📊 <b>Общая статистика</b>\n\n🟢 Прибыль: <b>{money(p)}</b>\n🔵 Вложено: <b>{money(i)}</b>\n🟣 Выручка: <b>{money(r)}</b>\n🟠 Расходы: <b>{money(e)}</b>\n📦 Товаров: <b>{n}</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главная",callback_data="home")]]),parse_mode="HTML"); await c.answer()

@dp.callback_query(F.data=="profile")
async def profile(c):
    ensure_business(c.from_user.id,c.from_user.full_name)
    b=get_business(c.from_user.id)
    ms=members(c.from_user.id)
    names=", ".join(x["name"] or str(x["user_id"]) for x in ms)
    await c.message.edit_text(f"👤 <b>Профиль бизнеса</b>\n\n🤝 Участники: <b>{names}</b>\n💰 Валюта: <b>RUB (₽)</b>\n📦 Учёт: <b>общий для участников</b>\n\nВсе участники видят один список товаров и одну статистику.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главная",callback_data="home")]]),parse_mode="HTML"); await c.answer()

async def main():
    init()
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())
