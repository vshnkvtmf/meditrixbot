import os, re, json, random, asyncio, sqlite3, traceback, time as _t
from datetime import time
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
GOLD, BROWN, RED = 0xC9A227, 0x6B4423, 0x8B1A1A
DIV = "━━━━━━━━ ⚔ ━━━━━━━━"
PRIZE = 3
R_MEMBER, R_OWNER = "🏰 Житель королевства", "👑 Владелец"
R_MOD_OLD = "👨‍💻 Модератор"
R_MEMBER_OLD = "🏆 Готовится к запуску"
CREATE_NAME = "🛠 Создать войс"
# ключ в БД -> имя канала (для самовосстановления настроек)
KNOWN = {
    "rules": "👨‍⚖️・правила", "cheat": "🤒・шпаргалка", "daily": "🗞・ежедневник",
    "flood": "📨・флуд-флудыч", "tech": "❓・тех-поддержка", "create": CREATE_NAME,
}
POS, NEG = {"👍", "❤", "🔥", "⭐"}, {"👎", "😡"}
RANKS = [(400, "🏰 Герцог"), (150, "🛡 Барон"), (50, "⚔ Рыцарь"), (10, "🗡 Оруженосец"), (-10**9, "🌾 Крестьянин")]
rank = lambda s: next(n for t, n in RANKS if s >= t)

# ---------- DB ----------
db = sqlite3.connect(os.getenv("DB_PATH", "bot.db"))
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS cfg(g INTEGER, k TEXT, v INTEGER, PRIMARY KEY(g,k));
CREATE TABLE IF NOT EXISTS rep(g INTEGER, u INTEGER, score INTEGER DEFAULT 0, daily REAL DEFAULT 0, PRIMARY KEY(g,u));
CREATE TABLE IF NOT EXISTS votes(m INTEGER, r INTEGER, e TEXT, a INTEGER, g INTEGER, d INTEGER, PRIMARY KEY(m,r,e));
CREATE TABLE IF NOT EXISTS rooms(c INTEGER PRIMARY KEY, g INTEGER, owner INTEGER);
CREATE TABLE IF NOT EXISTS vacancies(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    g INTEGER, ch INTEGER, msg INTEGER,
    author INTEGER, role_id INTEGER, slots INTEGER, filled INTEGER DEFAULT 0,
    title TEXT, fields TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS applications(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vid INTEGER, uid INTEGER, data TEXT, status TEXT DEFAULT 'pending');
""")

def q(sql, *a):
    c = db.execute(sql, a); db.commit(); return c
def cfg(g, k):
    r = q("SELECT v FROM cfg WHERE g=? AND k=?", g, k).fetchone(); return r["v"] if r else None
def setcfg(g, k, v): q("INSERT OR REPLACE INTO cfg VALUES(?,?,?)", g, k, v)
def addrep(g, u, d):
    q("INSERT INTO rep(g,u,score) VALUES(?,?,?) ON CONFLICT(g,u) DO UPDATE SET score=score+?", g, u, d, d)
def score(g, u):
    r = q("SELECT score FROM rep WHERE g=? AND u=?", g, u).fetchone(); return r["score"] if r else 0

def emb(title, desc="", color=GOLD, fields=(), footer=None):
    e = discord.Embed(title=title, description=desc, color=color)
    for n, v in fields: e.add_field(name=n, value=v, inline=False)
    if footer: e.set_footer(text=footer)
    return e

def chan(g, key):
    """Канал по ключу из БД; если записи нет — ищет по известному имени и чинит запись."""
    ch = g.get_channel(cfg(g.id, key) or 0)
    if ch: return ch
    name = KNOWN.get(key)
    if name:
        ch = discord.utils.get(g.channels, name=name)
        if ch:
            setcfg(g.id, key, ch.id)
            return ch
    return None

async def get_user(uid):
    u = bot.get_user(uid)
    if u: return u
    try: return await bot.fetch_user(uid)
    except discord.HTTPException: return None

# ---------- Bot ----------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

class Bot(commands.Bot):
    async def setup_hook(self):
        self.add_view(VoicePanel())
        for v in q("SELECT id,filled,slots FROM vacancies WHERE active=1").fetchall():
            self.add_view(VacancyView(v["id"], full=v["filled"] >= v["slots"]))
        for a in q("SELECT id FROM applications WHERE status='pending'").fetchall():
            self.add_view(ReviewView(a["id"]))
        await self.tree.sync()
        events.start()
        sweep_rooms.start()

bot = Bot(command_prefix=commands.when_mentioned, intents=intents)

@bot.event
async def on_ready():
    print("Online:", bot.user)
    for g in bot.guilds:  # восстановить привязки каналов
        for k in KNOWN: chan(g, k)
    for r in q("SELECT c FROM rooms").fetchall():
        ch = bot.get_channel(r["c"])
        if not ch: q("DELETE FROM rooms WHERE c=?", r["c"])
        elif not ch.members:
            q("DELETE FROM rooms WHERE c=?", r["c"])
            try: await ch.delete()
            except discord.HTTPException: pass

# ---------- Тексты ----------
def rules_embeds():
    return [emb("📜 УСТАВ КОРОЛЕВСТВА 📜",
        f"*Внемлите, путники! Здесь царят дружба и честь. Устав краток — чтите его, и пир будет вечным.*\n{DIV}",
        GOLD, [
        ("🛡 I. Уважение", "Относись к другим так, как хочешь, чтобы относились к тебе. Без оскорблений и травли."),
        ("🔒 II. Тайна личности", "Слив чужого адреса, IP, телефона и любых личных данных — изгнание без предупреждения."),
        ("🚫 III. Запретные свитки", "Контент 18+, шок и жестокость запрещены во всех землях."),
        ("⚔ IV. Споры без войны", "Спорить можно — устраивать срач нельзя. Попросили остановиться — остановись."),
        ("📣 V. Без спама", "Реклама, флуд и массовые упоминания — только с позволения администрации."),
        ("👑 VI. Слово администрации", "Администрация всегда права. Не согласен — спокойно напиши администратору, выслушаем."),
        ("⚖ Кара", "Предупреждение → мут → изгнание. Мелочи прощаем, тяжкие грехи — сразу в изгнание."),
        ], "Оставаясь на сервере, ты принимаешь Устав ✦")]

def cheat_embeds(g):
    m = lambda k: (g.get_channel(cfg(g.id, k) or 0) or type("x", (), {"mention": "—"})).mention
    return [
    emb("🤒 ШПАРГАЛКА ПУТНИКА 🗺", f"*Всё, что нужно знать новичку в нашем королевстве.*\n{DIV}", GOLD, [
        ("🏰 Карта земель",
            f"{m('rules')} — Устав\n"
            f"{m('daily')} — новости\n"
            f"{m('news')} — всё о Minecraft-сервере\n"
            f"{m('vacancies')} — вакансии\n"
            f"{m('support')} — поддержать проект\n"
            f"{m('screenshots')} — скриншоты\n"
            f"{m('flood')} — болтовня\n"
            f"{m('tech')} — помощь и баги"),
        (f"🏰 Роль «{R_MEMBER}»", "Выдана всем участникам королевства. Добро пожаловать!")]),
    emb("⭐ РЕПУТАЦИЯ", "", BROWN, [
        ("Как это работает", "Реакции под сообщениями — это оценка автора:\n👍 ❤️ 🔥 ⭐ → **+1 репутации**\n👎 😡 → **−1 репутации**\nСвои реакции и реакции на сообщения ботов не считаются. Убрал реакцию — очко вернулось."),
        ("Команды", "`/tab` — таблица лидеров\n`/profile [участник]` — звание и репутация\n`/daily` — королевская награда раз в 20 часов"),
        ("Звания", "🌾 Крестьянин → 🗡 Оруженосец (10) → ⚔ Рыцарь (50) → 🛡 Барон (150) → 🏰 Герцог (400)")]),
    emb("🎙 ЛИЧНЫЕ КОМНАТЫ", "", BROWN, [
        ("Как создать", "Зайди в **🛠 Создать войс** — бот сделает комнату с твоим ником и перенесёт тебя. Пустая комната удаляется сама."),
        ("Панель управления (придёт в ЛС)", "✏️ название · 👥 лимит · 🔒 закрыть/открыть · 🙈 скрыть/показать · 👢 выгнать · 🗑 удалить\n`/panel` — прислать панель снова."),
        ("Что будет, если…", "🔒 закрыть — новые участники не смогут зайти\n🙈 скрыть — комнату не увидят посторонние\n🥶 Бездействие — сюда уходят молчуны через 15 минут")]),
    emb("🎪 КОРОЛЕВСКИЙ ТУРНИР", "", BROWN, [
        ("Когда", "Каждый день в **20:00 МСК**. 10 минут на запись, затем 5 раундов на смекалку и скорость. Победитель получает **+6 репутации**."),
        ("❓ Тех. поддержка", "Создай пост, выбери тег, опиши проблему. Решено? Напиши `/solved` в своём посте."),
        ("💼 Вакансии", "Следи за каналом вакансий — там появляются открытые наборы в команду сервера."),
        ("Что будет, если…", "Нарушить Устав → предупреждение, мут, изгнание.\nПомогать другим и участвовать в турнирах → репутация и звания 🏰")], "Да пребудет с тобой слава! ✦")]

def support_embed():
    return emb("💎 Поддержать проект",
        f"{DIV}\n*Каждый донат — это вклад в развитие нашего королевства.*\n\n"
        "Мы вкладываем все силы в то, чтобы сервер становился лучше: новые механики, ивенты, "
        "качественная инфраструктура и приятная атмосфера.\n\n"
        "**Твоя поддержка помогает нам:**\n"
        "🏗 Содержать сервер и оплачивать хостинг\n"
        "⚙ Разрабатывать новые плагины и механики\n"
        "🎪 Проводить крутые ивенты и турниры\n"
        "🛡 Обеспечивать стабильность и защиту\n\n"
        "💛 Даже небольшой вклад имеет огромное значение для всего проекта.\n\n"
        f"**Поддержать:** https://dalink.to/disfleqqqs\n{DIV}",
        GOLD, footer="Спасибо, что вы с нами ✦")

# ---------- /reload ----------
async def post(ch, embeds):
    try: await ch.purge(limit=50, check=lambda m: m.author == bot.user)
    except discord.HTTPException: pass
    for e in embeds:
        await ch.send(embed=e)

@bot.tree.command(name="reload", description="Обновить структуру сервера и ролей")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def reload_cmd(i: discord.Interaction):
    if not i.user.guild_permissions.administrator:
        return await i.response.send_message("Только для администратора.", ephemeral=True)
    await i.response.defer(ephemeral=True, thinking=True)
    g = i.guild
    errs, info = [], []

    # ===== восстановление привязок =====
    for k in KNOWN: chan(g, k)

    # ===== РОЛИ =====
    old_member = discord.utils.get(g.roles, name=R_MEMBER_OLD)
    new_member = discord.utils.get(g.roles, name=R_MEMBER)
    if new_member:
        info.append(f"🏰 Роль участника «{R_MEMBER}» уже на месте")
        if old_member and old_member.id != new_member.id:
            moved = 0
            for m in list(old_member.members):
                if new_member not in m.roles:
                    try: await m.add_roles(new_member); moved += 1; await asyncio.sleep(0.3)
                    except discord.HTTPException: pass
            try:
                await old_member.delete(reason="Дубликат старой роли")
                info.append(f"🗑 Удалена старая роль «{R_MEMBER_OLD}» (перенесено участников: {moved})")
            except discord.HTTPException:
                errs.append("Не удалось удалить старую роль участника.")
    elif old_member:
        try:
            await old_member.edit(name=R_MEMBER)
            info.append(f"🏰 Роль участника переименована: «{R_MEMBER_OLD}» → «{R_MEMBER}»")
        except discord.HTTPException:
            errs.append("Не удалось переименовать роль участника (подними роль бота выше).")
    else:
        try:
            await g.create_role(name=R_MEMBER, colour=discord.Colour(0xB8860B))
            info.append(f"🏰 Создана роль участника «{R_MEMBER}»")
        except discord.HTTPException:
            errs.append("Не удалось создать роль участника.")

    mod_old = discord.utils.get(g.roles, name=R_MOD_OLD)
    if mod_old:
        try:
            await mod_old.delete(reason="Упразднение роли модератора по /reload")
            info.append(f"🗑 Роль модератора «{R_MOD_OLD}» удалена")
        except discord.HTTPException:
            errs.append("Не удалось удалить роль модератора (подними роль бота выше).")

    owner_role = discord.utils.get(g.roles, name=R_OWNER)

    def ow_readonly():
        ow = {
            g.default_role: discord.PermissionOverwrite(
                send_messages=False, add_reactions=False,
                create_public_threads=False, create_private_threads=False),
            g.me: discord.PermissionOverwrite(
                send_messages=True, embed_links=True, manage_messages=True),
        }
        if owner_role:
            ow[owner_role] = discord.PermissionOverwrite(send_messages=True, add_reactions=True)
        return ow

    # ===== КАНАЛЫ =====
    # 0. Войс «Создать войс» (без него личные комнаты не работают)
    cr = chan(g, "create")
    if not cr:
        try:
            cat = discord.utils.get(g.categories, name="Общение")
            cr = await g.create_voice_channel(CREATE_NAME, category=cat)
            setcfg(g.id, "create", cr.id)
            info.append(f"🎙 Создан канал «{CREATE_NAME}»")
        except discord.HTTPException as e:
            errs.append(f"Не удалось создать «{CREATE_NAME}»: {e.text}")
    else:
        info.append(f"🎙 Канал «{cr.name}» привязан — личные комнаты работают")

    # 1. Ежедневник → «Самое важное»
    sv = discord.utils.get(g.categories, name="Самое важное")
    daily_ch = chan(g, "daily")
    if daily_ch and sv and daily_ch.category_id != sv.id:
        try: await daily_ch.edit(category=sv); info.append("📅 ежедневник → «Самое важное»")
        except discord.HTTPException: errs.append("Не удалось переместить ежедневник.")

    # 2. Категория «Minecraft-сервер»
    mc_cat = discord.utils.get(g.categories, name="Minecraft-сервер")
    if not mc_cat:
        mc_cat = await g.create_category("Minecraft-сервер")
        obsh = discord.utils.get(g.categories, name="Общение")
        if obsh:
            try: await mc_cat.edit(position=obsh.position + 1)
            except discord.HTTPException: pass
        info.append("🎮 Создана категория «Minecraft-сервер»")

    # 3. mc → 📰・новости-сервера
    mc_ch = g.get_channel(cfg(g.id, "mc") or 0)
    if mc_ch:
        try: await mc_ch.edit(name="📰・новости-сервера", category=mc_cat)
        except discord.HTTPException:
            try: await mc_ch.edit(category=mc_cat)
            except discord.HTTPException: errs.append("Не удалось перенести MC-канал.")
        setcfg(g.id, "news", mc_ch.id)
        q("DELETE FROM cfg WHERE g=? AND k='mc'", g.id)
        info.append("📰 minecraft-сервер → 📰・новости-сервера")
    else:
        news = g.get_channel(cfg(g.id, "news") or 0) or discord.utils.get(g.text_channels, name="📰・новости-сервера")
        if not news:
            news = await g.create_text_channel("📰・новости-сервера", category=mc_cat, overwrites=ow_readonly())
            info.append("📰 Создан канал новостей сервера")
        setcfg(g.id, "news", news.id)

    # 4. 💼・вакансии
    vac_ch = g.get_channel(cfg(g.id, "vacancies") or 0) or discord.utils.get(g.text_channels, name="💼・вакансии")
    if not vac_ch:
        vac_ch = await g.create_text_channel("💼・вакансии", category=mc_cat, overwrites=ow_readonly())
        info.append("💼 Создан канал вакансий")
    elif vac_ch.category_id != mc_cat.id:
        try: await vac_ch.edit(category=mc_cat)
        except discord.HTTPException: pass
    setcfg(g.id, "vacancies", vac_ch.id)

    # 5. 💎・поддержать-проект
    sup_ch = g.get_channel(cfg(g.id, "support") or 0) or discord.utils.get(g.text_channels, name="💎・поддержать-проект")
    if not sup_ch:
        sup_ch = await g.create_text_channel("💎・поддержать-проект", category=mc_cat, overwrites=ow_readonly())
        info.append("💎 Создан канал «Поддержать проект»")
    elif sup_ch.category_id != mc_cat.id:
        try: await sup_ch.edit(category=mc_cat)
        except discord.HTTPException: pass
    setcfg(g.id, "support", sup_ch.id)
    try:
        await sup_ch.purge(limit=50, check=lambda m: m.author == bot.user)
        await sup_ch.send(embed=support_embed())
    except discord.HTTPException: errs.append("Не удалось обновить сообщение в поддержке.")

    # 6. 📸・скриншоты (только картинки)
    ss_ch = g.get_channel(cfg(g.id, "screenshots") or 0) or discord.utils.get(g.text_channels, name="📸・скриншоты")
    if not ss_ch:
        ss_ch = await g.create_text_channel("📸・скриншоты", category=mc_cat, overwrites={
            g.default_role: discord.PermissionOverwrite(
                send_messages=True, add_reactions=True,
                create_public_threads=False, create_private_threads=False),
            g.me: discord.PermissionOverwrite(
                send_messages=True, embed_links=True, manage_messages=True),
        })
        info.append("📸 Создан канал скриншотов")
    elif ss_ch.category_id != mc_cat.id:
        try: await ss_ch.edit(category=mc_cat)
        except discord.HTTPException: pass
    setcfg(g.id, "screenshots", ss_ch.id)

    # 7. Удалить категорию «Новости» (только если пуста)
    nov = discord.utils.get(g.categories, name="Новости")
    if nov:
        if nov.channels:
            info.append(f"⚠ В «Новостях» остались каналы: {', '.join(c.name for c in nov.channels)} — категория НЕ удалена")
        else:
            try: await nov.delete(); info.append("🗑 Категория «Новости» удалена")
            except discord.HTTPException: errs.append("Не удалось удалить «Новости».")

    # 8. Удалить «идеи-minecraft»
    ideas_ch = g.get_channel(cfg(g.id, "ideas") or 0) or discord.utils.get(list(g.forums) + list(g.text_channels), name="💥・идеи-minecraft")
    if ideas_ch:
        try:
            await ideas_ch.delete()
            q("DELETE FROM cfg WHERE g=? AND k='ideas'", g.id)
            info.append("🗑 Канал «идеи-minecraft» удалён")
        except discord.HTTPException: errs.append("Не удалось удалить «идеи-minecraft».")

    # 8.5. Удалить канал «🎙・комнаты»
    lobby = g.get_channel(cfg(g.id, "voice_lobby") or 0) or discord.utils.get(g.text_channels, name="🎙・комнаты")
    if lobby:
        try:
            await lobby.delete()
            q("DELETE FROM cfg WHERE g=? AND k='voice_lobby'", g.id)
            info.append("🗑 Канал «🎙・комнаты» удалён")
        except discord.HTTPException:
            errs.append("Не удалось удалить канал «🎙・комнаты».")

    # 9. Обновить Устав и Шпаргалку
    rules_ch = chan(g, "rules")
    if rules_ch:
        try: await post(rules_ch, rules_embeds())
        except discord.HTTPException: errs.append("Не удалось обновить Устав.")
    cheat_ch = chan(g, "cheat")
    if cheat_ch:
        try: await post(cheat_ch, cheat_embeds(g))
        except discord.HTTPException: errs.append("Не удалось обновить Шпаргалку.")

    # 10. Проверка прав бота для войсов
    p = g.me.guild_permissions
    miss = [n for n, ok in (("Управление каналами", p.manage_channels), ("Перемещение участников", p.move_members),
                            ("Подключение", p.connect)) if not (ok or p.administrator)]
    if miss: errs.append("Для личных комнат боту не хватает прав: " + ", ".join(miss))

    txt = "✅ **Изменения применены:**\n" + ("\n".join(f"• {x}" for x in info) or "—")
    if errs: txt += "\n\n⚠ " + "\n⚠ ".join(errs)
    await i.followup.send(txt[:1990], ephemeral=True)

# ---------- Новички ----------
@bot.event
async def on_member_join(m: discord.Member):
    if m.bot: return
    g = m.guild
    r = (discord.utils.get(g.roles, name=R_MEMBER)
         or discord.utils.get(g.roles, name=R_MEMBER_OLD))
    if r:
        try: await m.add_roles(r)
        except discord.HTTPException: pass
    c = lambda k: (g.get_channel(cfg(g.id, k) or 0) or type("x", (), {"mention": "—"})).mention
    try:
        await m.send(embed=emb(f"🏰 Добро пожаловать, {m.display_name}!",
            f"*Врата **{g.name}** распахнуты для тебя, путник.*\n{DIV}\n\n"
            f"📜 Прочти {c('rules')}\n🤒 Загляни в {c('cheat')}\n🗞 Новости — {c('daily')}\n\n"
            f"Тебе выдана роль **{R_MEMBER}** — добро пожаловать в королевство!\n"
            "Зарабатывай репутацию, участвуй в турнирах и заводи друзей. Да пребудет с тобой слава! ⚔", GOLD))
    except discord.HTTPException: pass

# ---------- Личные войсы ----------
def room_of(i):
    r = q("SELECT c FROM rooms WHERE owner=?", i.user.id).fetchone()
    if not r: return None
    ch = bot.get_channel(r["c"])
    if not ch:  # канал удалён вручную — чистим запись
        q("DELETE FROM rooms WHERE c=?", r["c"])
    return ch

class NameModal(discord.ui.Modal, title="Название комнаты"):
    v = discord.ui.TextInput(label="Новое название", max_length=100)
    def __init__(s, ch): super().__init__(); s.ch = ch
    async def on_submit(s, i):
        name = s.v.value.strip()
        if not name:
            return await i.response.send_message("Название не может быть пустым.", ephemeral=True)
        try: await s.ch.edit(name=name)
        except discord.HTTPException as e:
            return await i.response.send_message(f"Не удалось: {e.text}", ephemeral=True)
        await i.response.send_message("✅ Название изменено.", ephemeral=True)

class LimitModal(discord.ui.Modal, title="Лимит участников"):
    v = discord.ui.TextInput(label="Число от 0 до 99 (0 — без лимита)", max_length=2)
    def __init__(s, ch): super().__init__(); s.ch = ch
    async def on_submit(s, i):
        n = int(s.v.value) if s.v.value.isdigit() else 0
        try: await s.ch.edit(user_limit=n)
        except discord.HTTPException as e:
            return await i.response.send_message(f"Не удалось: {e.text}", ephemeral=True)
        await i.response.send_message(f"✅ Лимит: {n or 'нет'}.", ephemeral=True)

async def toggle(i, perm, msgs):
    ch = room_of(i); role = ch.guild.default_role
    ow = ch.overwrites_for(role)
    new = None if getattr(ow, perm) is False else False
    setattr(ow, perm, new)
    await ch.set_permissions(role, overwrite=ow)
    await i.response.send_message(msgs[0] if new is False else msgs[1], ephemeral=True)

class VoicePanel(discord.ui.View):
    def __init__(s): super().__init__(timeout=None)
    async def interaction_check(s, i):
        if room_of(i): return True
        await i.response.send_message("У тебя нет комнаты. Зайди в «🛠 Создать войс».", ephemeral=True); return False
    @discord.ui.button(label="Название", emoji="✏️", custom_id="vp:name")
    async def name(s, i, b): await i.response.send_modal(NameModal(room_of(i)))
    @discord.ui.button(label="Лимит", emoji="👥", custom_id="vp:limit")
    async def limit(s, i, b): await i.response.send_modal(LimitModal(room_of(i)))
    @discord.ui.button(label="Закрыть/открыть", emoji="🔒", custom_id="vp:lock")
    async def lock(s, i, b): await toggle(i, "connect", ("🔒 Комната закрыта.", "🔓 Комната открыта."))
    @discord.ui.button(label="Скрыть/показать", emoji="🙈", custom_id="vp:hide")
    async def hide(s, i, b): await toggle(i, "view_channel", ("🙈 Комната скрыта.", "👁 Комната видна."))
    @discord.ui.button(label="Удалить", emoji="🗑", style=discord.ButtonStyle.danger, custom_id="vp:del")
    async def delete(s, i, b):
        ch = room_of(i); q("DELETE FROM rooms WHERE c=?", ch.id)
        await i.response.send_message("🗑 Комната удалена.", ephemeral=True)
        try: await ch.delete()
        except discord.HTTPException: pass
    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="👢 Выгнать участника…", custom_id="vp:kick")
    async def kick(s, i, sel):
        ch = room_of(i); mem = ch.guild.get_member(sel.values[0].id)
        if not mem or mem.id == i.user.id or mem not in ch.members:
            return await i.response.send_message("Этого участника нет в твоей комнате.", ephemeral=True)
        try:
            await mem.move_to(None); await ch.set_permissions(mem, connect=False)
        except discord.HTTPException as e:
            return await i.response.send_message(f"Не удалось: {e.text}", ephemeral=True)
        await i.response.send_message(f"👢 {mem.display_name} выгнан и не сможет вернуться.", ephemeral=True)

async def send_panel(m, ch):
    try:
        await m.send(embed=emb("🎙 Твоя комната готова", f"**{ch.name}** — управляй ею кнопками ниже.", BROWN), view=VoicePanel())
        return True
    except discord.HTTPException: return False

async def make_room(m, g, category):
    base = {m: discord.PermissionOverwrite(view_channel=True, connect=True)}
    try:  # пробуем явно дать боту права в комнате
        return await g.create_voice_channel(m.display_name[:100], category=category,
            overwrites={**base, g.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True, move_members=True)})
    except discord.Forbidden:
        return await g.create_voice_channel(m.display_name[:100], category=category, overwrites=base)

@bot.event
async def on_voice_state_update(m, b, a):
    if m.bot: return
    g = m.guild
    try:
        create = chan(g, "create")
        if a.channel and create and a.channel.id == create.id:
            r = q("SELECT c FROM rooms WHERE owner=? AND g=?", m.id, g.id).fetchone()
            ch = g.get_channel(r["c"]) if r else None
            if r and not ch: q("DELETE FROM rooms WHERE c=?", r["c"])
            if not ch:
                ch = await make_room(m, g, a.channel.category)
                q("INSERT OR REPLACE INTO rooms VALUES(?,?,?)", ch.id, g.id, m.id)
                await send_panel(m, ch)
            try: await m.move_to(ch)
            except discord.HTTPException as e:
                print("voice: не удалось переместить (нужно право «Перемещать участников»):", e)
                q("DELETE FROM rooms WHERE c=?", ch.id)
                try: await ch.delete()
                except discord.HTTPException: pass
                try: await m.send("⚠ Не удалось создать комнату: боту не хватает права «Перемещать участников». Сообщи администратору.")
                except discord.HTTPException: pass
        if b.channel and b.channel != a.channel and not b.channel.members \
                and q("SELECT 1 FROM rooms WHERE c=?", b.channel.id).fetchone():
            q("DELETE FROM rooms WHERE c=?", b.channel.id)
            try: await b.channel.delete()
            except discord.HTTPException: pass
    except Exception:
        print("voice error:"); traceback.print_exc()

@bot.tree.command(name="panel", description="Прислать панель управления твоей комнатой")
@app_commands.guild_only()
async def panel(i: discord.Interaction):
    ch = room_of(i)
    if not ch: return await i.response.send_message("Сначала создай комнату через «🛠 Создать войс».", ephemeral=True)
    ok = await send_panel(i.user, ch)
    await i.response.send_message("📨 Панель отправлена в ЛС." if ok else "Не могу написать в ЛС — открой их.", ephemeral=True)

# ---------- Репутация ----------
def val(e):
    e = str(e).replace("\ufe0f", ""); return 1 if e in POS else -1 if e in NEG else 0

async def on_rep(p, add):
    d = val(p.emoji)
    if not d or not p.guild_id or p.user_id == bot.user.id: return
    if add and getattr(p, "member", None) and p.member.bot: return
    ch = bot.get_channel(p.channel_id)
    if not ch: return
    try: msg = await ch.fetch_message(p.message_id)
    except discord.HTTPException: return
    a = msg.author
    if a.bot or a.id == p.user_id: return
    e = str(p.emoji)
    if add:
        if q("INSERT OR IGNORE INTO votes VALUES(?,?,?,?,?,?)", p.message_id, p.user_id, e, a.id, p.guild_id, d).rowcount:
            addrep(p.guild_id, a.id, d)
    else:
        r = q("SELECT d FROM votes WHERE m=? AND r=? AND e=?", p.message_id, p.user_id, e).fetchone()
        if r:
            q("DELETE FROM votes WHERE m=? AND r=? AND e=?", p.message_id, p.user_id, e)
            addrep(p.guild_id, a.id, -r["d"])

@bot.event
async def on_raw_reaction_add(p): await on_rep(p, True)
@bot.event
async def on_raw_reaction_remove(p): await on_rep(p, False)

@bot.tree.command(name="tab", description="Таблица лидеров по репутации")
@app_commands.guild_only()
async def tab(i: discord.Interaction):
    rows = q("SELECT u,score FROM rep WHERE g=? ORDER BY score DESC LIMIT 10", i.guild.id).fetchall()
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
    t = "\n".join(f"{medals[n]} **{n+1}.** <@{r['u']}> — **{r['score']}** · {rank(r['score'])}" for n, r in enumerate(rows))
    await i.response.send_message(embed=emb("🏆 Зал славы", f"{DIV}\n{t or '*Пока пусто — раздавайте реакции!*'}"))

@bot.tree.command(name="profile", description="Звание и репутация участника")
@app_commands.guild_only()
async def profile(i: discord.Interaction, member: discord.Member = None):
    m = member or i.user; s = score(i.guild.id, m.id)
    pos = q("SELECT COUNT(*)+1 c FROM rep WHERE g=? AND score>?", i.guild.id, s).fetchone()["c"]
    e = emb(f"📜 {m.display_name}", f"{rank(s)}\n⭐ Репутация: **{s}**\n🏅 Место: **#{pos}**", BROWN)
    e.set_thumbnail(url=m.display_avatar.url)
    await i.response.send_message(embed=e)

@bot.tree.command(name="daily", description="Королевская награда (раз в 20 часов)")
@app_commands.guild_only()
async def daily(i: discord.Interaction):
    r = q("SELECT daily FROM rep WHERE g=? AND u=?", i.guild.id, i.user.id).fetchone()
    last = r["daily"] if r else 0
    if _t.time() - last < 72000:
        h = int((72000 - (_t.time() - last)) // 3600) + 1
        return await i.response.send_message(f"⏳ Казна пуста. Возвращайся через ~{h} ч.", ephemeral=True)
    n = random.randint(1, 3); addrep(i.guild.id, i.user.id, n)
    q("UPDATE rep SET daily=? WHERE g=? AND u=?", _t.time(), i.guild.id, i.user.id)
    await i.response.send_message(embed=emb("👑 Дар короля", f"{random.choice(['Король щедр сегодня!', 'Трактирщик угостил тебя элем!', 'Ты нашёл кошель в канаве!'])}\nТы получаешь **+{n}** репутации."))

# ---------- /solved ----------
@bot.tree.command(name="solved", description="Отметить тему техподдержки решённой")
@app_commands.guild_only()
async def solved(i: discord.Interaction):
    t = i.channel
    if not isinstance(t, discord.Thread) or t.parent_id != cfg(i.guild.id, "tech"):
        return await i.response.send_message("Работает только внутри поста техподдержки.", ephemeral=True)
    perms = i.user.guild_permissions
    if (i.user.id != t.owner_id and not perms.administrator
            and not perms.manage_messages and not perms.manage_threads):
        return await i.response.send_message("Отметить может автор или администратор.", ephemeral=True)
    tag = discord.utils.get(t.parent.available_tags, name="Решено")
    await i.response.send_message("✅ Тема решена и закрыта. Спасибо!")
    await t.edit(applied_tags=(list(t.applied_tags) + ([tag] if tag else []))[:5], archived=True)

# ---------- Вакансии ----------
class ApplicationModal(discord.ui.Modal):
    def __init__(self, vid, fields):
        super().__init__(title="Заявка на вакансию")
        self.vid = vid
        self.field_items = []
        for f in fields[:5]:
            ti = discord.ui.TextInput(
                label=(f[:45] or "Поле"),
                style=discord.TextStyle.paragraph,
                max_length=1000,
                required=True)
            self.add_item(ti)
            self.field_items.append((f, ti))
    async def on_submit(self, i: discord.Interaction):
        v = q("SELECT * FROM vacancies WHERE id=?", self.vid).fetchone()
        if not v or not v["active"]:
            return await i.response.send_message("Вакансия закрыта.", ephemeral=True)
        if v["filled"] >= v["slots"]:
            return await i.response.send_message("Все места уже заняты.", ephemeral=True)
        data = {f: ti.value for f, ti in self.field_items}
        cur = q("INSERT INTO applications(vid,uid,data) VALUES(?,?,?)",
                self.vid, i.user.id, json.dumps(data, ensure_ascii=False))
        app_id = cur.lastrowid
        await i.response.send_message("✅ Заявка отправлена! Ожидай ответа.", ephemeral=True)
        author = await get_user(v["author"])
        if author:
            e = emb(f"📋 Заявка: {v['title']}",
                f"**Кандидат:** {i.user.mention} (`{i.user.name}`)\n"
                f"**ID:** {i.user.id}\n{DIV}",
                BROWN, footer=f"Заявка #{app_id}")
            for f, val_ in data.items():
                e.add_field(name=f[:256], value=(val_[:1000] or "*пусто*"), inline=False)
            try:
                view = ReviewView(app_id)
                await author.send(embed=e, view=view)
                bot.add_view(view)
            except discord.HTTPException:
                print(f"Не удалось отправить заявку #{app_id} автору вакансии (закрыты ЛС?)")

class ApplyBtn(discord.ui.Button):
    def __init__(self, vid, full=False):
        super().__init__(
            label="Мест нет" if full else "Откликнуться",
            style=discord.ButtonStyle.secondary if full else discord.ButtonStyle.success,
            emoji="🚫" if full else "📝",
            custom_id=f"vac_apply_{vid}",
            disabled=full)
        self.vid = vid
    async def callback(self, i: discord.Interaction):
        v = q("SELECT * FROM vacancies WHERE id=?", self.vid).fetchone()
        if not v or not v["active"]:
            return await i.response.send_message("Вакансия закрыта.", ephemeral=True)
        if v["filled"] >= v["slots"]:
            return await i.response.send_message("Все места уже заняты.", ephemeral=True)
        role = i.guild.get_role(v["role_id"]) if i.guild else None
        if role and role in i.user.roles:
            return await i.response.send_message("У тебя уже есть эта роль.", ephemeral=True)
        if q("SELECT 1 FROM applications WHERE vid=? AND uid=? AND status='pending'", self.vid, i.user.id).fetchone():
            return await i.response.send_message("Ты уже подал заявку.", ephemeral=True)
        fields = [f.strip() for f in v["fields"].split(",") if f.strip()]
        await i.response.send_modal(ApplicationModal(self.vid, fields))

class VacancyView(discord.ui.View):
    def __init__(self, vid, full=False):
        super().__init__(timeout=None)
        self.add_item(ApplyBtn(vid, full))

class AcceptBtn(discord.ui.Button):
    def __init__(self, app_id):
        super().__init__(label="Принять", style=discord.ButtonStyle.success, emoji="✅", custom_id=f"vac_accept_{app_id}")
        self.app_id = app_id
    async def callback(self, i: discord.Interaction):
        app = q("SELECT * FROM applications WHERE id=?", self.app_id).fetchone()
        if not app or app["status"] != "pending":
            return await i.response.send_message("Заявка уже обработана.", ephemeral=True)
        v = q("SELECT * FROM vacancies WHERE id=?", app["vid"]).fetchone()
        if not v:
            return await i.response.send_message("Вакансия не найдена.", ephemeral=True)
        if v["filled"] >= v["slots"]:
            return await i.response.send_message("Все места уже заняты.", ephemeral=True)
        guild = bot.get_guild(v["g"])
        if not guild:
            return await i.response.send_message("Сервер не найден.", ephemeral=True)
        member = guild.get_member(app["uid"])
        role = guild.get_role(v["role_id"])
        if member and role:
            try: await member.add_roles(role)
            except discord.HTTPException as e:
                return await i.response.send_message(f"Не удалось выдать роль: {e}", ephemeral=True)
        q("UPDATE applications SET status='accepted' WHERE id=?", self.app_id)
        q("UPDATE vacancies SET filled=filled+1 WHERE id=?", v["id"])
        new_filled = v["filled"] + 1
        full = new_filled >= v["slots"]
        ch = guild.get_channel(v["ch"])
        if ch:
            try:
                msg = await ch.fetch_message(v["msg"])
                if msg.embeds:
                    d = msg.embeds[0].to_dict()
                    for f in d.get("fields", []):
                        if f["name"] == "Занято":
                            f["value"] = f"{new_filled}/{v['slots']}"
                            break
                    await msg.edit(embed=discord.Embed.from_dict(d), view=VacancyView(v["id"], full=full))
            except discord.HTTPException: pass
        user = await get_user(app["uid"])
        if user:
            try:
                await user.send(embed=emb("🎉 Поздравляем!",
                    f"Твоя заявка на **{v['title']}** принята!\nРоль: {role.mention if role else '—'}", GOLD))
            except discord.HTTPException: pass
        try: await i.message.edit(view=None)
        except discord.HTTPException: pass
        await i.response.send_message("✅ Принято.", ephemeral=True)

class RejectBtn(discord.ui.Button):
    def __init__(self, app_id):
        super().__init__(label="Отклонить", style=discord.ButtonStyle.danger, emoji="❌", custom_id=f"vac_reject_{app_id}")
        self.app_id = app_id
    async def callback(self, i: discord.Interaction):
        app = q("SELECT * FROM applications WHERE id=?", self.app_id).fetchone()
        if not app or app["status"] != "pending":
            return await i.response.send_message("Заявка уже обработана.", ephemeral=True)
        v = q("SELECT * FROM vacancies WHERE id=?", app["vid"]).fetchone()
        q("UPDATE applications SET status='rejected' WHERE id=?", self.app_id)
        user = await get_user(app["uid"])
        if user and v:
            try:
                await user.send(embed=emb("😔 К сожалению…",
                    f"Твоя заявка на **{v['title']}** отклонена.\nПопробуй в другой раз!", RED))
            except discord.HTTPException: pass
        try: await i.message.edit(view=None)
        except discord.HTTPException: pass
        await i.response.send_message("❌ Отклонено.", ephemeral=True)

class ReviewView(discord.ui.View):
    def __init__(self, app_id):
        super().__init__(timeout=None)
        self.add_item(AcceptBtn(app_id))
        self.add_item(RejectBtn(app_id))

@bot.tree.command(name="vac", description="Создать вакансию с анкетой")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    title="Название вакансии",
    role_name="Роль для принятых (имя или упоминание)",
    slots="Сколько человек нужно",
    fields="Поля анкеты через запятую (например: Опыт,Возраст,Мотивация)")
async def vac(i: discord.Interaction, title: str, role_name: str, slots: int, fields: str):
    if not i.user.guild_permissions.administrator:
        return await i.response.send_message("Только для администратора.", ephemeral=True)
    if slots < 1 or slots > 100:
        return await i.response.send_message("Число мест от 1 до 100.", ephemeral=True)
    field_list = [f.strip() for f in fields.split(",") if f.strip()][:5]
    if not field_list:
        return await i.response.send_message("Укажи хотя бы одно поле анкеты.", ephemeral=True)
    g = i.guild
    vac_ch = g.get_channel(cfg(g.id, "vacancies") or 0)
    if not vac_ch:
        return await i.response.send_message("Канал вакансий не найден. Запусти /reload.", ephemeral=True)
    await i.response.defer(ephemeral=True)

    mm = re.match(r"<@&(\d+)>", role_name.strip())
    role = g.get_role(int(mm.group(1))) if mm else None
    if not role:
        role = discord.utils.get(g.roles, name=role_name.strip())
    if not role:
        try: role = await g.create_role(name=role_name.strip(), hoist=True, colour=discord.Colour(0x4A90D9))
        except discord.HTTPException as e:
            return await i.followup.send(f"Не удалось создать роль: {e}", ephemeral=True)

    # Скрыть канал вакансий от роли принятых
    try:
        ow = vac_ch.overwrites_for(role)
        ow.view_channel = False
        await vac_ch.set_permissions(role, overwrite=ow)
    except discord.HTTPException: pass

    e = emb(f"💼 {title}",
        f"{DIV}\nПрисоединяйся к нашей команде! Заполни анкету, нажав кнопку ниже.\n{DIV}", BROWN)
    e.add_field(name="Роль", value=role.mention, inline=True)
    e.add_field(name="Занято", value=f"0/{slots}", inline=True)
    e.add_field(name="Поля анкеты", value=", ".join(field_list), inline=False)

    cur = q("INSERT INTO vacancies(g,ch,msg,author,role_id,slots,filled,title,fields,active) VALUES(?,?,?,?,?,?,?,?,?,?)",
            g.id, vac_ch.id, 0, i.user.id, role.id, slots, 0, title, ",".join(field_list), 1)
    vid = cur.lastrowid
    view = VacancyView(vid)
    msg = await vac_ch.send(embed=e, view=view)
    q("UPDATE vacancies SET msg=? WHERE id=?", msg.id, vid)
    bot.add_view(view)
    await i.followup.send(f"✅ Вакансия создана: {msg.jump_url}", ephemeral=True)

# ---------- Турнир (1 раз в день, 20:00) ----------
EVENT_SIGNUP_TIME = 600
ROUND_TIME = 90

RIDDLE_BANK = [
    ("Что можно сломать, не прикасаясь?", "обещание"),
    ("Чем больше берёшь, тем больше становится. Что это?", "яма"),
    ("У кого есть зубы, но нет рта?", "расчёска"),
    ("Что всегда идёт, но никогда не приходит?", "время"),
    ("Кто говорит на всех языках?", "эхо"),
    ("Что принадлежит тебе, но другие используют это чаще?", "имя"),
    ("Что становится мокрым, пока сохнет?", "полотенце"),
    ("У тебя есть, у меня есть, у дерева нет. Что это?", "тень"),
    ("Что может путешествовать по миру, оставаясь в углу?", "марка"),
    ("Сколько месяцев в году имеют 28 дней?", "все"),
    ("Что можно увидеть с закрытыми глазами?", "сон"),
    ("Что идёт вверх и вниз, но не двигается?", "лестница"),
]

ANAGRAM_WORDS = ["королевство", "сокровище", "подземелье", "арбалет", "алмаз",
                 "дракон", "турнир", "цитадель", "рыцарь", "крепость",
                 "оруженосец", "кузнец", "таверна", "знамя"]

def scr(w):
    s = w
    while s == w and len(w) > 1:
        s = "".join(random.sample(w, len(w)))
    return s.upper()

def round_math(hard=False):
    if hard:
        a, b, c = random.randint(20, 99), random.randint(20, 99), random.randint(100, 500)
        return "🧮 Счётная палата", f"**{a} × {b} − {c}**", str(a * b - c)
    a, b, c = random.randint(5, 15), random.randint(5, 15), random.randint(10, 99)
    return "🧮 Счётная палата", f"**{a} × {b} + {c}**", str(a * b + c)

def round_anagram():
    w = random.choice(ANAGRAM_WORDS)
    return "🔤 Свиток-анаграмма", f"Расшифруй слово: **{scr(w)}**", w

def round_riddle():
    q_, a = random.choice(RIDDLE_BANK)
    return "📜 Загадка сфинкса", q_, a

async def play_round(ch, gen, allowed):
    title, text, answer = gen
    await ch.send(embed=emb(title, f"{text}\n⏱ {ROUND_TIME} секунд.", BROWN))
    def check(m):
        return (m.channel == ch and not m.author.bot and m.author.id in allowed
                and m.content.strip().lower() == answer.lower())
    try:
        m = await bot.wait_for("message", timeout=ROUND_TIME, check=check)
        return m.author
    except asyncio.TimeoutError:
        return None

class EventSignupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=EVENT_SIGNUP_TIME)
        self.participants = set()
    @discord.ui.button(label="Участвовать!", emoji="⚔", style=discord.ButtonStyle.success)
    async def join(self, i: discord.Interaction, b):
        if i.user.bot: return
        if i.user.id in self.participants:
            return await i.response.send_message("Ты уже записан!", ephemeral=True)
        self.participants.add(i.user.id)
        await i.response.send_message(f"✅ Ты в турнире! Участников: **{len(self.participants)}**", ephemeral=True)

async def run_event(g):
    cat = ch = None
    try:
        cat = await g.create_category("🎪 Мини-ивент")
        ch = await cat.create_text_channel("🎲・арена")
        fl = chan(g, "flood")
        if fl:
            await fl.send(embed=emb("🏆 КОРОЛЕВСКИЙ ТУРНИР 🏆",
                f"{DIV}\nЧерез **10 минут** начнётся грандиозный турнир!\n"
                f"Спешите на арену: {ch.mention}\n"
                f"5 раундов на смекалку и скорость. Победитель получит **+{PRIZE*2} репутации**!\n{DIV}",
                GOLD))

        signup = EventSignupView()
        await ch.send(embed=emb("⚔ Запись на турнир",
            "Нажми **Участвовать**, чтобы войти в список.\n"
            "У тебя есть **10 минут**. После начнётся турнир — 5 раундов на смекалку и скорость!\n\n"
            "Раунды: счёт → анаграмма → загадка → сложный счёт → финальная анаграмма.",
            BROWN), view=signup)

        await asyncio.sleep(EVENT_SIGNUP_TIME)
        signup.stop()

        participants = signup.participants
        if not participants:
            await ch.send(embed=emb("⌛ Турнир отменён", "Никто не записался. Ждём вас в следующий раз!", RED))
            await asyncio.sleep(15)
            return

        if len(participants) == 1:
            w = g.get_member(next(iter(participants)))
            if w:
                addrep(g.id, w.id, PRIZE * 2)
                await ch.send(embed=emb("🏆 Единственный участник",
                    f"{w.mention} побеждает без боя и получает **+{PRIZE*2}** репутации!", GOLD))
            await asyncio.sleep(15)
            return

        await ch.send(embed=emb("🏆 ТУРНИР НАЧИНАЕТСЯ!",
            f"Участников: **{len(participants)}**\nДа начнётся битва! ⚔", GOLD))
        scores = {uid: 0 for uid in participants}

        plan = [round_math(False), round_anagram(), round_riddle(), round_math(True), round_anagram()]
        for n, gen in enumerate(plan, 1):
            await ch.send(embed=emb(f"⚔ Раунд {n}/5", f"*{gen[0]}*", BROWN))
            winner = await play_round(ch, gen, participants)
            if winner:
                scores[winner.id] += 1
                top = ", ".join(f"<@{u}>: {s}" for u, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s)
                await ch.send(embed=emb("✅ Верно!",
                    f"{winner.mention} получает **+1** очко!\nТекущий счёт: {top}", GOLD))
            else:
                await ch.send(embed=emb("⌛ Время вышло", f"Никто не ответил. Верный ответ: **{gen[2]}**", RED))
            await asyncio.sleep(3)

        if not any(scores.values()):
            await ch.send(embed=emb("🏁 Турнир окончен", "Никто не набрал очков. Ждём вас снова!", RED))
            await asyncio.sleep(15)
            return

        top_score = max(scores.values())
        top = [uid for uid, s in scores.items() if s == top_score]
        if len(top) > 1:
            await ch.send(embed=emb("⚡ РАВНЫЙ СЧЁТ!", "Финальная битва — кто первый решит, тот и чемпион!", GOLD))
            w2 = await play_round(ch, round_math(True), set(top))
            winner = w2 if w2 else g.get_member(random.choice(top))
        else:
            winner = g.get_member(top[0])

        if winner:
            addrep(g.id, winner.id, PRIZE * 2)
            await ch.send(embed=emb("🏆 ЧЕМПИОН ТУРНИРА!",
                f"{winner.mention} побеждает и получает **+{PRIZE*2}** репутации!\n\n"
                "**Итоговый счёт:**\n" + "\n".join(
                    f"<@{u}> — {s}" for u, s in sorted(scores.items(), key=lambda kv: -kv[1])),
                GOLD, footer="Слава чемпиону! ⚔"))
        await asyncio.sleep(30)
    except Exception:
        print("event error:"); traceback.print_exc()
    finally:
        for x in (ch, cat):
            if x:
                try: await x.delete()
                except discord.HTTPException: pass

@tasks.loop(seconds=60)
async def sweep_rooms():
    now = discord.utils.utcnow()
    for r in q("SELECT c FROM rooms").fetchall():
        ch = bot.get_channel(r["c"])
        if not ch:
            q("DELETE FROM rooms WHERE c=?", r["c"]); continue
        if not ch.members and (now - ch.created_at).total_seconds() > 30:
            q("DELETE FROM rooms WHERE c=?", r["c"])
            try: await ch.delete()
            except discord.HTTPException: pass

@tasks.loop(time=[time(20, 0, tzinfo=TZ)])
async def events():
    for g in bot.guilds:
        if chan(g, "flood"):
            asyncio.create_task(run_event(g))

# ---------- Скриншоты: только изображения ----------
@bot.event
async def on_message(m: discord.Message):
    if not m.guild or m.author.bot: return
    if m.channel.id != cfg(m.guild.id, "screenshots"): return
    if m.author.guild_permissions.manage_messages: return
    has_image = any(a.content_type and a.content_type.startswith("image/") for a in m.attachments)
    if not has_image:
        try:
            await m.delete()
            await m.channel.send(f"📸 {m.author.mention}, в этом канале разрешены только изображения!",
                                 delete_after=6)
        except discord.HTTPException: pass

bot.run(TOKEN)
