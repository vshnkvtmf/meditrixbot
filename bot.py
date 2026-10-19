import os, random, asyncio, sqlite3, time as _t
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
R_MEMBER, R_OWNER, R_MOD = "🏆 Готовится к запуску", "👑 Владелец", "👨‍💻 Модератор"
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

# ---------- Bot ----------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class Bot(commands.Bot):
    async def setup_hook(self):
        self.add_view(VoicePanel())
        await self.tree.sync()
        events.start()

bot = Bot(command_prefix=commands.when_mentioned, intents=intents)

@bot.event
async def on_ready():
    print("Online:", bot.user)
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
        ("📣 V. Без спама", "Реклама, флуд и массовые упоминания — только с позволения модерации."),
        ("👑 VI. Слово администрации", "Администрация всегда права. Не согласен — спокойно напиши модератору, выслушаем."),
        ("⚖ Кара", "Предупреждение → мут → изгнание. Мелочи прощаем, тяжкие грехи — сразу в изгнание."),
        ], "Оставаясь на сервере, ты принимаешь Устав ✦")]

def cheat_embeds(g):
    m = lambda k: (g.get_channel(cfg(g.id, k) or 0) or type("x", (), {"mention": "—"})).mention
    return [
    emb("🤒 ШПАРГАЛКА ПУТНИКА 🗺", f"*Всё, что нужно знать новичку в нашем королевстве.*\n{DIV}", GOLD, [
        ("🏰 Карта земель", f"{m('rules')} — Устав\n{m('daily')} — новости\n{m('mc')} — всё о Minecraft-сервере\n{m('flood')} — болтовня\n{m('tech')} — помощь и баги\n{m('ideas')} — твои идеи для сервера"),
        (f"🏆 Роль «{R_MEMBER}»", "Выдана всем: мы готовим запуск Minecraft-сервера. Следи за новостями!")]),
    emb("⭐ РЕПУТАЦИЯ", "", BROWN, [
        ("Как это работает", "Реакции под сообщениями — это оценка автора:\n👍 ❤️ 🔥 ⭐ → **+1 репутации**\n👎 😡 → **−1 репутации**\nСвои реакции и реакции на сообщения ботов не считаются. Убрал реакцию — очко вернулось."),
        ("Команды", "`/tab` — таблица лидеров\n`/profile [участник]` — звание и репутация\n`/daily` — королевская награда раз в 20 часов"),
        ("Звания", "🌾 Крестьянин → 🗡 Оруженосец (10) → ⚔ Рыцарь (50) → 🛡 Барон (150) → 🏰 Герцог (400)")]),
    emb("🎙 ЛИЧНЫЕ КОМНАТЫ", "", BROWN, [
        ("Как создать", "Зайди в **🛠 Создать войс** — бот сделает комнату с твоим ником и перенесёт тебя. Пустая комната удаляется сама."),
        ("Панель управления (придёт в ЛС)", "✏️ название · 👥 лимит · 🔒 закрыть/открыть · 🙈 скрыть/показать · 👢 выгнать · 🗑 удалить\n`/panel` — прислать панель снова."),
        ("Что будет, если…", "🔒 закрыть — новые участники не смогут зайти\n🙈 скрыть — комнату не увидят посторонние\n🥶 Бездействие — сюда уходят молчуны через 15 минут")]),
    emb("🎪 ИВЕНТЫ И ФОРУМЫ", "", BROWN, [
        ("Мини-ивенты", f"Каждый день в **15:00, 17:00 и 20:00** (МСК по умолчанию) открывается временная арена: счёт, анаграммы, угадай число. Победитель получает **+{PRIZE} репутации**."),
        ("❓ Тех. поддержка", "Создай пост, выбери тег, опиши проблему. Решено? Напиши `/solved` в своём посте."),
        ("💥 Идеи Minecraft", "Предложи идею — участники голосуют 👍/👎. Лучшие получают тег «Принято»! Голоса влияют на репутацию автора."),
        ("Что будет, если…", "Нарушить Устав → предупреждение, мут, изгнание.\nПомогать другим и делиться идеями → репутация и звания 🏰")], "Да пребудет с тобой слава! ✦")]

FORUMS = {
    "tech": ([("🐛", "Баг"), ("❓", "Вопрос"), ("🔌", "Подключение"), ("🧩", "Моды и плагины"), ("✅", "Решено")],
             "Проблемы и вопросы по серверу. Перед созданием поста прочитай закреплённое сообщение.",
             emb("📌 Как пользоваться техподдержкой", "1️⃣ Дай посту ясное название\n2️⃣ Выбери тег\n3️⃣ Опиши: что делал, что ожидал, что получил (скриншоты приветствуются)\n4️⃣ Когда решено — напиши `/solved` в посте\n\nНе дублируй темы и будь терпелив — модераторы ответят, как только смогут. 🛠")),
    "ideas": ([("🏗", "Постройки"), ("⚙", "Механики"), ("🎉", "Ивенты"), ("🧩", "Моды"), ("✅", "Принято"), ("❌", "Отклонено")],
              "Предлагай идеи для Minecraft-сервера и голосуй за чужие.",
              emb("📌 Как предлагать идеи", "1️⃣ Создай пост с названием идеи\n2️⃣ Выбери тег\n3️⃣ Опиши, что предлагаешь и чем это сделает игру лучше\n4️⃣ Участники голосуют 👍/👎 (это влияет на репутацию автора!)\n\nЛучшие идеи получают тег «Принято» и попадают на сервер. 💡")),
}

STRUCT = [
    ("Самое важное", [("rules", "t", "👨‍⚖️・правила", 1), ("cheat", "t", "🤒・шпаргалка", 1)]),
    ("Новости", [("daily", "t", "🗞・ежедневник", 1), ("mc", "t", "🧊・minecraft-сервер", 1)]),
    ("Общение", [("flood", "t", "📨・флуд-флудыч", 0), ("create", "v", "🛠 Создать войс", 0)]),
    ("Остальное", [("tech", "f", "❓・тех-поддержка", 0), ("ideas", "f", "💥・идеи-minecraft", 0), ("afk", "v", "🥶 Бездействие", 0)]),
]

# ---------- /setup ----------
async def get_role(g, name, **kw):
    return discord.utils.get(g.roles, name=name) or await g.create_role(name=name, **kw)

async def post(ch, embeds):
    try: await ch.purge(limit=50, check=lambda m: m.author == bot.user)
    except discord.HTTPException: pass
    await ch.send(embeds=embeds)

@bot.tree.command(name="setup", description="Развернуть сервер по шаблону королевства")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def setup(i: discord.Interaction):
    if not i.user.guild_permissions.administrator:
        return await i.response.send_message("Только для администраторов.", ephemeral=True)
    await i.response.defer(ephemeral=True, thinking=True)
    g, errs, new = i.guild, [], set()
    owner = await get_role(g, R_OWNER, colour=discord.Colour(GOLD), hoist=True, permissions=discord.Permissions.all())
    mod = await get_role(g, R_MOD, colour=discord.Colour(0x4A90D9), hoist=True, mentionable=True,
        permissions=discord.Permissions(kick_members=True, moderate_members=True, manage_messages=True,
        manage_threads=True, move_members=True, mute_members=True, view_audit_log=True, manage_nicknames=True))
    mem = await get_role(g, R_MEMBER, colour=discord.Colour(0xB8860B))
    base = g.me.top_role.position
    try: await g.edit_role_positions({owner: max(base - 1, 1), mod: max(base - 2, 1), mem: max(base - 3, 1)})
    except discord.HTTPException: errs.append("Порядок ролей: поднимите роль бота выше и повторите /setup.")
    try: await i.user.add_roles(owner)
    except discord.HTTPException: errs.append("Не удалось выдать роль Владельца (роль бота ниже?).")

    ro = lambda: {g.default_role: discord.PermissionOverwrite(send_messages=False, add_reactions=False,
                  create_public_threads=False, create_private_threads=False),
                  mod: discord.PermissionOverwrite(send_messages=True, add_reactions=True),
                  g.me: discord.PermissionOverwrite(send_messages=True, embed_links=True)}
    for cname, chans in STRUCT:
        cat = discord.utils.get(g.categories, name=cname) or await g.create_category(cname)
        for key, kind, name, readonly in chans:
            if g.get_channel(cfg(g.id, key) or 0): continue
            try:
                if kind == "t": ch = await g.create_text_channel(name, category=cat, overwrites=ro() if readonly else {})
                elif kind == "v": ch = await g.create_voice_channel(name, category=cat)
                else:
                    tags, topic, _ = FORUMS[key]
                    ch = await g.create_forum(name, category=cat, topic=topic,
                        available_tags=[discord.ForumTag(name=n, emoji=e) for e, n in tags])
                setcfg(g.id, key, ch.id); new.add(key)
            except discord.HTTPException as e:
                errs.append(f"Канал «{name}»: {e.text} (для форумов нужен режим Community).")

    for key, kind in (("rules", rules_embeds), ("cheat", lambda: cheat_embeds(g))):
        ch = g.get_channel(cfg(g.id, key) or 0)
        if ch: await post(ch, kind())
    for key, (_, _, guide) in FORUMS.items():
        f = g.get_channel(cfg(g.id, key) or 0)
        if f and key in new:
            t = await f.create_thread(name="📌 Как пользоваться разделом", embed=guide)
            await t.thread.edit(pinned=True, locked=True)
    afk = g.get_channel(cfg(g.id, "afk") or 0)
    if afk:
        try: await g.edit(afk_channel=afk, afk_timeout=900)
        except discord.HTTPException: pass

    n = 0
    for m in g.members:
        if not m.bot and mem not in m.roles:
            try: await m.add_roles(mem); n += 1; await asyncio.sleep(0.3)
            except discord.HTTPException: pass
    txt = f"🏰 Королевство готово! Роль «{R_MEMBER}» выдана: **{n}** уч.\nДобавляйте модераторов в роль {mod.mention}."
    if errs: txt += "\n\n⚠ " + "\n⚠ ".join(errs)
    await i.followup.send(txt, ephemeral=True)

# ---------- Новички ----------
@bot.event
async def on_member_join(m: discord.Member):
    if m.bot: return
    g = m.guild
    r = discord.utils.get(g.roles, name=R_MEMBER)
    if r:
        try: await m.add_roles(r)
        except discord.HTTPException: pass
    c = lambda k: (g.get_channel(cfg(g.id, k) or 0) or type("x", (), {"mention": "—"})).mention
    try:
        await m.send(embed=emb(f"🏰 Добро пожаловать, {m.display_name}!",
            f"*Врата **{g.name}** распахнуты для тебя, путник.*\n{DIV}\n\n"
            f"📜 Прочти {c('rules')}\n🤒 Загляни в {c('cheat')}\n🗞 Новости — {c('daily')}\n\n"
            f"Тебе выдана роль **{R_MEMBER}**: мы готовим запуск Minecraft-сервера.\n"
            "Зарабатывай репутацию, играй в ивенты и заводи друзей. Да пребудет с тобой слава! ⚔", GOLD))
    except discord.HTTPException: pass

# ---------- Личные войсы ----------
def room_of(i):
    r = q("SELECT c FROM rooms WHERE owner=?", i.user.id).fetchone()
    return bot.get_channel(r["c"]) if r else None

class NameModal(discord.ui.Modal, title="Название комнаты"):
    v = discord.ui.TextInput(label="Новое название", max_length=100)
    def __init__(s, ch): super().__init__(); s.ch = ch
    async def on_submit(s, i):
        await s.ch.edit(name=s.v.value); await i.response.send_message("✅ Название изменено.", ephemeral=True)

class LimitModal(discord.ui.Modal, title="Лимит участников"):
    v = discord.ui.TextInput(label="Число от 0 до 99 (0 — без лимита)", max_length=2)
    def __init__(s, ch): super().__init__(); s.ch = ch
    async def on_submit(s, i):
        n = int(s.v.value) if s.v.value.isdigit() else 0
        await s.ch.edit(user_limit=n); await i.response.send_message(f"✅ Лимит: {n or 'нет'}.", ephemeral=True)

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
        await i.response.send_message("🗑 Комната удалена.", ephemeral=True); await ch.delete()
    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="👢 Выгнать участника…", custom_id="vp:kick")
    async def kick(s, i, sel):
        ch = room_of(i); mem = ch.guild.get_member(sel.values[0].id)
        if not mem or mem.id == i.user.id or mem not in ch.members:
            return await i.response.send_message("Этого участника нет в твоей комнате.", ephemeral=True)
        await mem.move_to(None); await ch.set_permissions(mem, connect=False)
        await i.response.send_message(f"👢 {mem.display_name} выгнан и не сможет вернуться.", ephemeral=True)

async def send_panel(m, ch):
    try:
        await m.send(embed=emb("🎙 Твоя комната готова", f"**{ch.name}** — открыта для всех, без лимита.\nУправляй ею кнопками ниже.", BROWN), view=VoicePanel())
        return True
    except discord.HTTPException: return False

@bot.event
async def on_voice_state_update(m, b, a):
    if m.bot: return
    g = m.guild
    if a.channel and a.channel.id == cfg(g.id, "create"):
        r = q("SELECT c FROM rooms WHERE owner=? AND g=?", m.id, g.id).fetchone()
        ch = g.get_channel(r["c"]) if r else None
        if r and not ch: q("DELETE FROM rooms WHERE c=?", r["c"])
        if not ch:
            ch = await g.create_voice_channel(m.display_name[:100], category=a.channel.category,
                overwrites={m: discord.PermissionOverwrite(view_channel=True, connect=True)})
            q("INSERT INTO rooms VALUES(?,?,?)", ch.id, g.id, m.id)
            await send_panel(m, ch)
        try: await m.move_to(ch)
        except discord.HTTPException: pass
    if b.channel and b.channel != a.channel and not b.channel.members and q("SELECT 1 FROM rooms WHERE c=?", b.channel.id).fetchone():
        q("DELETE FROM rooms WHERE c=?", b.channel.id)
        try: await b.channel.delete()
        except discord.HTTPException: pass

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

# ---------- Форумы ----------
@bot.event
async def on_thread_create(t: discord.Thread):
    if t.parent_id and t.parent_id == cfg(t.guild.id, "ideas") and t.owner_id != bot.user.id:
        await asyncio.sleep(1.5)
        try:
            m = await t.fetch_message(t.id)
            await m.add_reaction("👍"); await m.add_reaction("👎")
        except discord.HTTPException: pass

@bot.tree.command(name="solved", description="Отметить тему техподдержки решённой")
@app_commands.guild_only()
async def solved(i: discord.Interaction):
    t = i.channel
    if not isinstance(t, discord.Thread) or t.parent_id != cfg(i.guild.id, "tech"):
        return await i.response.send_message("Работает только внутри поста техподдержки.", ephemeral=True)
    mod = discord.utils.get(i.guild.roles, name=R_MOD)
    if i.user.id != t.owner_id and mod not in i.user.roles and not i.user.guild_permissions.administrator:
        return await i.response.send_message("Отметить может автор или модератор.", ephemeral=True)
    tag = discord.utils.get(t.parent.available_tags, name="Решено")
    await i.response.send_message("✅ Тема решена и закрыта. Спасибо!")
    await t.edit(applied_tags=(list(t.applied_tags) + ([tag] if tag else []))[:5], archived=True)

# ---------- Мини-ивенты ----------
chk = lambda ch: (lambda m: m.channel == ch and not m.author.bot)

async def finish(g, ch, w):
    if w:
        addrep(g.id, w.id, PRIZE)
        await ch.send(embed=emb("🏆 Победитель!", f"{w.mention} получает **+{PRIZE}** репутации!"))
    else:
        await ch.send(embed=emb("⌛ Время вышло", "Никто не справился. Ждём вас на следующем ивенте!", RED))

async def first(ch, check, timeout):
    try: return (await bot.wait_for("message", timeout=timeout, check=lambda m: chk(ch)(m) and check(m))).author
    except asyncio.TimeoutError: return None

async def game_math(g, ch):
    a, b, c = random.randint(6, 19), random.randint(3, 9), random.randint(10, 99)
    await ch.send(embed=emb("🧮 Счётная палата", f"Кто первым решит: **{a} × {b} + {c}** ?\n⏱ 60 секунд."))
    await finish(g, ch, await first(ch, lambda m: m.content.strip() == str(a * b + c), 60))

async def game_scramble(g, ch):
    w = random.choice(["замок", "рыцарь", "дракон", "турнир", "алхимик", "кузнец", "таверна", "король", "оруженосец", "крепость"])
    s = w
    while s == w: s = "".join(random.sample(w, len(w)))
    await ch.send(embed=emb("🔤 Свиток-анаграмма", f"Расшифруй слово: **{s.upper()}**\n⏱ 60 секунд."))
    await finish(g, ch, await first(ch, lambda m: m.content.strip().lower() == w, 60))

async def game_guess(g, ch):
    n, loop, w = random.randint(1, 100), asyncio.get_running_loop(), None
    end = loop.time() + 90
    await ch.send(embed=emb("🎯 Загадка мага", "Я загадал число от **1 до 100**. Угадай за 90 секунд — подскажу «больше/меньше»."))
    while (left := end - loop.time()) > 0:
        try: m = await bot.wait_for("message", timeout=left, check=lambda m: chk(ch)(m) and m.content.strip().isdigit())
        except asyncio.TimeoutError: break
        v = int(m.content)
        if v == n: w = m.author; break
        await m.reply("⬆ Больше" if v < n else "⬇ Меньше", mention_author=False)
    await finish(g, ch, w)

async def run_event(g):
    cat = ch = None
    try:
        cat = await g.create_category("🎪 Мини-ивент")
        ch = await cat.create_text_channel("🎲・арена")
        fl = g.get_channel(cfg(g.id, "flood") or 0)
        if fl: await fl.send(embed=emb("🎪 Мини-ивент начался!", f"Спешите на арену: {ch.mention}\nПобедителю — **+{PRIZE} репутации**!"))
        await random.choice((game_math, game_scramble, game_guess))(g, ch)
        await asyncio.sleep(20)
    except Exception as e: print("event error:", e)
    finally:
        for x in (ch, cat):
            if x:
                try: await x.delete()
                except discord.HTTPException: pass

@tasks.loop(time=[time(h, tzinfo=TZ) for h in (15, 17, 20)])
async def events():
    for g in bot.guilds:
        if cfg(g.id, "flood"): asyncio.create_task(run_event(g))

bot.run(TOKEN)
