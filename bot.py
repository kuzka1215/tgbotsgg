
import os, sqlite3, asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN=os.getenv("BOT_TOKEN")
DB=os.getenv("DB_PATH","business.db")
if not TOKEN: raise RuntimeError("Set BOT_TOKEN")
bot=Bot(TOKEN); dp=Dispatcher(storage=MemoryStorage())

def con():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init():
    c=con()
    c.execute("""CREATE TABLE IF NOT EXISTS products(
      id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,name TEXT,buy REAL,extra REAL DEFAULT 0,
      sale REAL,status TEXT,category TEXT,date TEXT,notes TEXT,photo TEXT,
      p1 TEXT,p1_amount REAL DEFAULT 0,p2 TEXT,p2_amount REAL DEFAULT 0,p3 TEXT,p3_amount REAL DEFAULT 0
    )""")
    c.commit(); c.close()
def money(x):
    x=float(x or 0)
    s=f"{x:,.0f}" if x.is_integer() else f"{x:,.2f}"
    return s.replace(","," ")+" ₽"
def cost(p): return float(p["buy"])+float(p["extra"] or 0)
def profit(p): return float(p["sale"] or 0)-cost(p)
def stats(uid):
    c=con(); a=c.execute("SELECT * FROM products WHERE user_id=?",(uid,)).fetchall(); c.close()
    sold=[x for x in a if x["status"]=="Продан"]
    return sum(profit(x) for x in sold),sum(cost(x) for x in a if x["status"]!="Продан"),sum(float(x["sale"] or 0) for x in sold),sum(float(x["extra"] or 0) for x in a),len(a)
def home_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text="📦 Мои товары",callback_data="products"),InlineKeyboardButton(text="➕ Добавить",callback_data="add")],
      [InlineKeyboardButton(text="📊 Статистика",callback_data="stats"),InlineKeyboardButton(text="👤 Профиль",callback_data="profile")]])
def home_text(uid):
    p,i,r,e,n=stats(uid)
    return f"""📦 <b>Мой бизнес</b>

🟢 <b>Чистая прибыль</b>
{money(p)}

🔵 <b>Вложено в товары</b>
{money(i)}

🟣 <b>Выручка от продаж</b>
{money(r)}

🟠 <b>Доп. расходы</b>
{money(e)}

📦 Всего предметов: <b>{n}</b>"""
def icon(cat): return {"Телефоны":"📱","Электроника":"🎧","Одежда":"👕","Обувь":"👟","Аксессуары":"⌚"}.get(cat,"📦")

class Add(StatesGroup):
    name=State(); buy=State(); extra=State(); sale=State(); status=State(); category=State(); date=State(); notes=State(); photo=State()
    partners=State(); p1name=State(); p1amount=State(); p2name=State(); p2amount=State(); p3name=State(); p3amount=State()

def status_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text="🟠 Куплен",callback_data="st:Куплен"),InlineKeyboardButton(text="🔵 В продаже",callback_data="st:В продаже")],
      [InlineKeyboardButton(text="🟢 Продан",callback_data="st:Продан")]])

def category_kb():
    cats=["Телефоны","Электроника","Одежда","Обувь","Аксессуары","Другое"]
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=cats[i],callback_data=f"cat:{cats[i]}"),InlineKeyboardButton(text=cats[i+1],callback_data=f"cat:{cats[i+1]}")]
      for i in range(0,6,2)])

def partners_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text="👤 Один участник",callback_data="partners:1"),
       InlineKeyboardButton(text="👥 Два участника",callback_data="partners:2")],
      [InlineKeyboardButton(text="👥 Три участника",callback_data="partners:3")],
      [InlineKeyboardButton(text="⏭ Без распределения",callback_data="partners:0")]])

def product_kb(pid):
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text="✏️ Изменить",callback_data=f"edit:{pid}"),
       InlineKeyboardButton(text="🗑 Удалить",callback_data=f"del:{pid}")],
      [InlineKeyboardButton(text="📋 Таблица сделки",callback_data=f"table:{pid}")],
      [InlineKeyboardButton(text="← К товарам",callback_data="products")]])

def deal_table(p):
    total=cost(p); sale=float(p["sale"] or 0); pr=sale-total
    people=[(p["p1"],p["p1_amount"]),(p["p2"],p["p2_amount"]),(p["p3"],p["p3_amount"])]
    people=[(n,float(a or 0)) for n,a in people if n]
    lines=[f"📋 <b>ТАБЛИЦА СДЕЛКИ</b>",
           f"📦 <b>{p['name']}</b>","",
           f"🔴 <b>СТОИМОСТЬ + РАСХОДЫ</b>   {money(total)}"]
    for n,a in people:
        lines.append(f"   {n} — {money(a)}")
    lines += ["",f"🟡 <b>ПРОДАЖА</b>   {money(sale)}",
              f"🟢 <b>ПРИБЫЛЬ</b>   {money(pr)}",""]
    if people:
        # Default: sale is split proportionally to invested amount.
        # Also show each person's profit.
        invested=sum(a for _,a in people)
        if invested>0 and sale>0:
            lines.append("<b>РАСПРЕДЕЛЕНИЕ ПРОДАЖИ</b>")
            for n,a in people:
                share=sale*a/invested
                own_profit=share-a
                lines.append(f"   {n}: {money(share)}  (прибыль {money(own_profit)})")
        elif sale>0:
            lines.append("<b>РАСПРЕДЕЛЕНИЕ ПРОДАЖИ</b>")
            eq=sale/len(people)
            for n,_ in people: lines.append(f"   {n}: {money(eq)}")
    return "\n".join(lines)

@dp.message(CommandStart())
async def start(m): await m.answer(home_text(m.from_user.id),reply_markup=home_kb(),parse_mode="HTML")
@dp.callback_query(F.data=="home")
async def home(c): await c.message.edit_text(home_text(c.from_user.id),reply_markup=home_kb(),parse_mode="HTML"); await c.answer()

@dp.callback_query(F.data=="products")
async def products(c):
    db=con(); rows=db.execute("SELECT * FROM products WHERE user_id=? ORDER BY id DESC",(c.from_user.id,)).fetchall(); db.close()
    b=[]
    for p in rows[:40]:
        s={"Продан":"🟢","В продаже":"🔵","Куплен":"🟠"}.get(p["status"],"⚪")
        b.append([InlineKeyboardButton(text=f"{icon(p['category'])} {p['name']} · {s}",callback_data=f"item:{p['id']}")])
    b += [[InlineKeyboardButton(text="➕ Добавить товар",callback_data="add")],[InlineKeyboardButton(text="🏠 Главная",callback_data="home")]]
    text="📦 <b>Мои товары</b>\n\nНажми на предмет:" if rows else "📦 <b>Мои товары</b>\n\nПока нет товаров."
    await c.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=b),parse_mode="HTML"); await c.answer()

@dp.callback_query(F.data.startswith("item:"))
async def item(c):
    pid=int(c.data.split(":")[1]); db=con(); p=db.execute("SELECT * FROM products WHERE id=? AND user_id=?",(pid,c.from_user.id)).fetchone(); db.close()
    if not p: return await c.answer("Товар не найден",show_alert=True)
    st={"Продан":"🟢","В продаже":"🔵","Куплен":"🟠"}[p["status"]]
    sale=money(p["sale"]) if p["sale"] is not None else "—"
    pr=money(profit(p)) if p["sale"] is not None else "После продажи"
    partners=""
    names=[p["p1"],p["p2"],p["p3"]]
    if any(names):
        partners="\n\n<b>Участники</b>\n"+ "\n".join(f"• {p[f'p{i}']} — {money(p[f'p{i}_amount'])}" for i in range(1,4) if p[f'p{i}'])
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

@dp.callback_query(F.data.startswith("table:"))
async def table(c):
    pid=int(c.data.split(":")[1]); db=con(); p=db.execute("SELECT * FROM products WHERE id=? AND user_id=?",(pid,c.from_user.id)).fetchone(); db.close()
    if not p:return await c.answer("Не найдено",show_alert=True)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← К товару",callback_data=f"item:{pid}")]])
    await c.message.edit_text(deal_table(p),reply_markup=kb,parse_mode="HTML"); await c.answer()

@dp.callback_query(F.data=="add")
async def add(c,state):
    await state.clear(); await state.set_state(Add.name); await c.message.edit_text("➕ <b>Добавить товар</b>\n\n1/15. Название предмета?\nНапример: Maikolin h10",parse_mode="HTML"); await c.answer()
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
        await c.message.edit_text("11/15. Таблица без участников.\nНажми «-», чтобы продолжить.")
    else:
        await state.set_state(Add.p1name); await c.message.edit_text("11/15. Имя первого участника?\nНапример: Андрей"); 
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
    # third participant optional
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
    d=await state.get_data()
    db=con(); cur=db.execute("""INSERT INTO products(user_id,name,buy,extra,sale,status,category,date,notes,photo,p1,p1_amount,p2,p2_amount,p3,p3_amount)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(m.from_user.id,d["name"],d["buy"],d["extra"],d["sale"],d["status"],d["category"],d["date"],d["notes"],d["photo"],d.get("p1",""),d.get("p1_amount",0),d.get("p2",""),d.get("p2_amount",0),d.get("p3",""),d.get("p3_amount",0)))
    db.commit(); pid=cur.lastrowid; db.close(); await state.clear()
    await m.answer("✅ <b>Товар сохранён!</b>\n\n📋 Можно открыть таблицу сделки через карточку товара.",parse_mode="HTML",reply_markup=home_kb())

@dp.callback_query(F.data.startswith("del:"))
async def dele(c):
    pid=int(c.data[4:]); db=con(); db.execute("DELETE FROM products WHERE id=? AND user_id=?",(pid,c.from_user.id)); db.commit(); db.close()
    await c.message.edit_text("🗑 Товар удалён.",reply_markup=home_kb()); await c.answer()

@dp.callback_query(F.data=="stats")
async def statistics(c):
    p,i,r,e,n=stats(c.from_user.id)
    await c.message.edit_text(f"📊 <b>Статистика</b>\n\n🟢 Прибыль: <b>{money(p)}</b>\n🔵 Вложено: <b>{money(i)}</b>\n🟣 Выручка: <b>{money(r)}</b>\n🟠 Расходы: <b>{money(e)}</b>\n📦 Предметов: <b>{n}</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главная",callback_data="home")]]),parse_mode="HTML"); await c.answer()

@dp.callback_query(F.data=="profile")
async def profile(c):
    await c.message.edit_text("👤 <b>Профиль</b>\n\n💰 Валюта: <b>RUB (₽)</b>\n📦 Учёт: <b>каждый предмет отдельно</b>\n🤝 Есть распределение сделки между участниками.\n\n📋 В таблице сделки бот показывает вложения, продажу, общую прибыль и долю каждого участника.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главная",callback_data="home")]]),parse_mode="HTML"); await c.answer()

async def main(): init(); await dp.start_polling(bot)
if __name__=="__main__": asyncio.run(main())
