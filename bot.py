import os
import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta
from flask import Flask
import requests

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# =========================
# CONFIG
# =========================
BOT_TOKEN ="8773612925:AAFX4iVsz2TxNiIWZ6C2vtSm2fUJnWtwLgw"
FOOT_API_KEY = "21165774b3e849c882766618d3e42cee"
ADMIN_ID = int(os.getenv("ADMIN_ID", "5681608229"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003808231751"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@pronostics_bilan")

BASE_URL = "https://api.football-data.org/v4"
LEAGUES = ["PL", "PD", "SA", "BL1", "FL1"]
DB_NAME = "bot_v16_full.db"

VIP_TEXT = """
💎 <b>ACCÈS VIP PRONOSTICS</b>

Prix : <b>10 000 FCFA / mois</b>

💳 <b>Paiement Mobile Money</b>

• MTN Money : <b>+225 05 03 04 63 94</b>
• Orange Money : <b>+225 07 13 88 24 66</b>
• Moov Money : <b>+225 01 61 38 08 10</b>
• Wave : <b>+225 07 13 88 24 66</b>

Après paiement :
1️⃣ Clique sur <b>✅ J’ai payé</b>
2️⃣ Envoie ta preuve
3️⃣ L’admin vérifie
4️⃣ Ton accès VIP est activé
"""

TRADING_TEXT = """
📈 <b>SIGNAUX TRADING VIP</b>

XAUUSD BUY
Entrée : 2945
SL : 2937
TP : 2958

BTCUSD SELL
Entrée : 84250
TP : 83380

⚠️ Le trading comporte des risques.
"""

# =========================
# WEB SERVER (RENDER)
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "BOT V16 FULL ONLINE"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# =========================
# DATABASE
# =========================
conn = sqlite3.connect(DB_NAME, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    created_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS vip(
    user_id INTEGER PRIMARY KEY,
    expire TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    match_name TEXT,
    competition TEXT,
    kickoff TEXT,
    prediction TEXT,
    confidence INTEGER,
    created_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS waiting_proof(
    user_id INTEGER PRIMARY KEY
)
""")

conn.commit()

# =========================
# BOT
# =========================
dp = Dispatcher()

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⚽ Pronostics foot")],
        [KeyboardButton(text="📈 Signaux trading")],
        [KeyboardButton(text="💎 Offre VIP")],
        [KeyboardButton(text="👤 Mon compte")],
    ],
    resize_keyboard=True
)

def vip_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ J’ai payé", callback_data="paid")],
            [InlineKeyboardButton(text="📊 Canal bilan", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")]
        ]
    )

def admin_proof_keyboard(user_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Valider VIP", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton(text="❌ Refuser", callback_data=f"reject_{user_id}")
            ]
        ]
    )

# =========================
# HELPERS
# =========================
def now_str():
    return datetime.now().isoformat()

def save_user(message: Message):
    cur.execute("""
    INSERT OR IGNORE INTO users(id, username, full_name, created_at)
    VALUES (?, ?, ?, ?)
    """, (
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.full_name or "",
        now_str()
    ))
    conn.commit()

def is_vip(user_id: int) -> bool:
    row = cur.execute("SELECT expire FROM vip WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return False
    try:
        return datetime.fromisoformat(row["expire"]) > datetime.now()
    except Exception:
        return False

def vip_expire(user_id: int):
    row = cur.execute("SELECT expire FROM vip WHERE user_id=?", (user_id,)).fetchone()
    return row["expire"] if row else "—"

def activate_vip(user_id: int, days: int = 30):
    expire_date = datetime.now() + timedelta(days=days)
    cur.execute("INSERT OR REPLACE INTO vip(user_id, expire) VALUES (?, ?)", (user_id, expire_date.isoformat()))
    cur.execute("DELETE FROM waiting_proof WHERE user_id=?", (user_id,))
    conn.commit()

def waiting_proof(user_id: int) -> bool:
    row = cur.execute("SELECT user_id FROM waiting_proof WHERE user_id=?", (user_id,)).fetchone()
    return row is not None

def count_users() -> int:
    row = cur.execute("SELECT COUNT(*) AS total FROM users").fetchone()
    return int(row["total"])

# =========================
# API FOOT
# =========================
def api_get(path, params=None):
    headers = {"X-Auth-Token": FOOT_API_KEY}
    response = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=25)
    response.raise_for_status()
    return response.json()

def get_standings_map(code):
    data = api_get(f"/competitions/{code}/standings")
    table = {}
    standings = data.get("standings", [])
    if not standings:
        return table

    rows = standings[0].get("table", [])
    for row in rows:
        team = row.get("team", {})
        team_id = team.get("id")
        position = row.get("position")
        if team_id and position:
            table[team_id] = position
    return table

def build_prediction(home_name, away_name, home_pos, away_pos):
    if home_pos is None or away_pos is None:
        return {"prediction": "Over 1.5 buts", "confidence": 58}

    diff = away_pos - home_pos
    abs_diff = abs(diff)

    if diff >= 8:
        prediction = "1X"
        confidence = 82
    elif diff >= 4:
        prediction = "Victoire domicile ou nul"
        confidence = 75
    elif diff <= -8:
        prediction = "X2"
        confidence = 80
    elif diff <= -4:
        prediction = "Victoire extérieur ou nul"
        confidence = 74
    else:
        prediction = "Over 1.5 buts"
        confidence = 64

    if abs_diff <= 1:
        confidence -= 6
    elif abs_diff == 2:
        confidence -= 3

    confidence = max(55, min(confidence, 88))
    return {"prediction": prediction, "confidence": confidence}

def generate_pronos_from_api():
    data = api_get("/matches", params={"competitions": ",".join(LEAGUES)})
    matches = data.get("matches", [])
    if not matches:
        return []

    standings_cache = {}
    predictions = []

    for match in matches:
        if match.get("status") not in ["TIMED", "SCHEDULED"]:
            continue

        competition = match.get("competition", {})
        code = competition.get("code")
        competition_name = competition.get("name", "Compétition")

        home = match.get("homeTeam", {})
        away = match.get("awayTeam", {})

        home_name = home.get("name", "Home")
        away_name = away.get("name", "Away")
        home_id = home.get("id")
        away_id = away.get("id")
        utc_date = match.get("utcDate", "")

        if not code:
            continue

        if code not in standings_cache:
            try:
                standings_cache[code] = get_standings_map(code)
            except Exception:
                standings_cache[code] = {}

        standings = standings_cache[code]
        home_pos = standings.get(home_id)
        away_pos = standings.get(away_id)

        pred = build_prediction(home_name, away_name, home_pos, away_pos)

        predictions.append({
            "match_name": f"{home_name} vs {away_name}",
            "competition": competition_name,
            "kickoff": utc_date,
            "prediction": pred["prediction"],
            "confidence": pred["confidence"]
        })

    predictions.sort(key=lambda x: x["confidence"], reverse=True)
    return predictions[:5]

async def send_pronos(chat_id: int, bot: Bot):
    try:
        pronos = await asyncio.to_thread(generate_pronos_from_api)
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Erreur API foot : {e}")
        return

    if not pronos:
        await bot.send_message(chat_id, "⚠️ Aucun match disponible aujourd’hui.")
        return

    text = "⚽ <b>PRONOSTICS DU JOUR</b>\n\n"

    for item in pronos:
        text += (
            f"<b>{item['match_name']}</b>\n"
            f"🏆 {item['competition']}\n"
            f"🕒 {item['kickoff']}\n"
            f"➡️ {item['prediction']}\n"
            f"📊 Confiance : {item['confidence']}%\n\n"
        )

        cur.execute("""
        INSERT INTO history(date, match_name, competition, kickoff, prediction, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d"),
            item["match_name"],
            item["competition"],
            item["kickoff"],
            item["prediction"],
            item["confidence"],
            now_str()
        ))

    conn.commit()
    await bot.send_message(chat_id, text)

async def send_bilan(chat_id: int, bot: Bot):
    rows = cur.execute("""
    SELECT * FROM history
    ORDER BY id DESC
    LIMIT 5
    """).fetchall()

    if not rows:
        await bot.send_message(chat_id, "⚠️ Aucun historique disponible.")
        return

    text = "📊 <b>BILAN DES DERNIERS PRONOSTICS</b>\n\n"
    for row in rows:
        text += (
            f"<b>{row['match_name']}</b>\n"
            f"🏆 {row['competition']}\n"
            f"➡️ {row['prediction']}\n"
            f"📊 Confiance : {row['confidence']}%\n\n"
        )

    await bot.send_message(chat_id, text)

# =========================
# COMMANDS
# =========================
@dp.message(CommandStart())
async def start_cmd(message: Message):
    save_user(message)
    await message.answer(
        "👋 <b>Bienvenue sur PRONOSTIC BOT V16</b>\n\nChoisis une option dans le menu ci-dessous.",
        reply_markup=main_menu
    )

@dp.message(Command("id"))
async def id_cmd(message: Message):
    save_user(message)
    await message.answer(f"Ton ID Telegram : <code>{message.from_user.id}</code>")

@dp.message(Command("pronos"))
async def pronos_cmd(message: Message, bot: Bot):
    save_user(message)
    await send_pronos(message.chat.id, bot)

@dp.message(Command("vip"))
async def vip_cmd(message: Message):
    save_user(message)
    await message.answer(VIP_TEXT, reply_markup=vip_keyboard())

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    save_user(message)
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "📊 <b>STATISTIQUES</b>\n\n"
        f"👥 Utilisateurs : <b>{count_users()}</b>\n"
    )

@dp.message(Command("prono"))
async def prono_admin_cmd(message: Message, bot: Bot):
    save_user(message)
    if message.from_user.id != ADMIN_ID:
        return
    await send_pronos(CHANNEL_ID, bot)
    await message.answer("✅ Pronostics envoyés dans le canal.")

@dp.message(Command("bilan"))
async def bilan_cmd(message: Message, bot: Bot):
    save_user(message)
    if message.from_user.id != ADMIN_ID:
        return
    await send_bilan(CHANNEL_ID, bot)
    await message.answer("✅ Bilan envoyé dans le canal.")

@dp.message(Command("broadcast"))
async def broadcast_cmd(message: Message, bot: Bot):
    save_user(message)
    if message.from_user.id != ADMIN_ID:
        return

    content = message.text.replace("/broadcast", "", 1).strip()
    if not content:
        await message.answer("Usage : /broadcast votre message")
        return

    users = cur.execute("SELECT id FROM users").fetchall()
    sent = 0
    for user in users:
        try:
            await bot.send_message(user["id"], content)
            sent += 1
        except Exception:
            pass

    await message.answer(f"✅ Message envoyé à {sent} utilisateur(s).")

# =========================
# BUTTONS
# =========================
@dp.message(F.text == "⚽ Pronostics foot")
async def button_pronos(message: Message, bot: Bot):
    save_user(message)
    await send_pronos(message.chat.id, bot)

@dp.message(F.text == "💎 Offre VIP")
async def button_vip(message: Message):
    save_user(message)
    await message.answer(VIP_TEXT, reply_markup=vip_keyboard())

@dp.message(F.text == "👤 Mon compte")
async def button_account(message: Message):
    save_user(message)
    status = "✅ Actif" if is_vip(message.from_user.id) else "❌ Inactif"
    expire = vip_expire(message.from_user.id)
    proof = "Oui" if waiting_proof(message.from_user.id) else "Non"

    text = (
        "👤 <b>Mon compte</b>\n\n"
        f"ID : <code>{message.from_user.id}</code>\n"
        f"Nom : {message.from_user.full_name}\n"
        f"VIP : {status}\n"
        f"Expiration VIP : {expire}\n"
        f"Preuve en attente : {proof}"
    )
    await message.answer(text)

@dp.message(F.text == "📈 Signaux trading")
async def button_trading(message: Message):
    save_user(message)
    if not is_vip(message.from_user.id):
        await message.answer(
            "🔒 Les signaux trading sont réservés aux VIP.\n\n" + VIP_TEXT,
            reply_markup=vip_keyboard()
        )
        return
    await message.answer(TRADING_TEXT)

# =========================
# CALLBACKS
# =========================
@dp.callback_query(F.data == "paid")
async def paid_callback(callback: CallbackQuery):
    cur.execute("INSERT OR REPLACE INTO waiting_proof(user_id) VALUES (?)", (callback.from_user.id,))
    conn.commit()
    await callback.message.answer("✅ Envoie maintenant ta preuve de paiement.\nTu peux envoyer une photo.")
    await callback.answer()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_callback(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Non autorisé", show_alert=True)
        return

    user_id = int(callback.data.split("_")[1])
    activate_vip(user_id, 30)
    await bot.send_message(user_id, "🎉 Ton accès VIP est activé pour 30 jours.")
    await callback.answer("VIP activé")
    await callback.message.answer(f"✅ VIP validé pour {user_id}")

@dp.callback_query(F.data.startswith("reject_"))
async def reject_callback(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Non autorisé", show_alert=True)
        return

    user_id = int(callback.data.split("_")[1])
    cur.execute("DELETE FROM waiting_proof WHERE user_id=?", (user_id,))
    conn.commit()

    await bot.send_message(user_id, "❌ Paiement refusé.")
    await callback.answer("Paiement refusé")
    await callback.message.answer(f"❌ Paiement refusé pour {user_id}")

# =========================
# PROOF PHOTO
# =========================
@dp.message(F.photo)
async def proof_photo(message: Message, bot: Bot):
    save_user(message)

    if not waiting_proof(message.from_user.id):
        await message.answer("Photo reçue.")
        return

    await bot.send_photo(
        ADMIN_ID,
        message.photo[-1].file_id,
        caption=(
            f"💰 <b>PREUVE DE PAIEMENT</b>\n\n"
            f"User ID : <code>{message.from_user.id}</code>\n"
            f"Nom : {message.from_user.full_name}"
        ),
        reply_markup=admin_proof_keyboard(message.from_user.id)
    )

    await message.answer("✅ Preuve envoyée à l’admin.")

# =========================
# FALLBACK
# =========================
@dp.message()
async def fallback_handler(message: Message):
    save_user(message)
    await message.answer("Utilise le menu ou une commande comme /start")

# =========================
# MAIN
# =========================
async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN manquant.")
    if not FOOT_API_KEY:
        raise ValueError("FOOT_API_KEY manquante.")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    print("Bot V16 connecté. En attente des messages...", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    asyncio.run(main())