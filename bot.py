import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
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

# =======================
# CONFIG
# =======================
BOT_TOKEN = "8773612925:AAFX4iVsz2TxNiIWZ6C2vtSm2fUJnWtwLgw"
FOOT_API_KEY = "21165774b3e849c882766618d3e42cee"
ADMIN_ID = 5681608229

DB_PATH = "bot_v7.db"
BASE_URL = "https://api.football-data.org/v4"
LEAGUES = ["PL", "PD", "SA", "BL1", "FL1"]
VIP_PRICE = "10 000 FCFA / mois"

dp = Dispatcher()

menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⚽ Pronostics foot")],
        [KeyboardButton(text="📈 Signaux trading")],
        [KeyboardButton(text="💎 Offre VIP")],
        [KeyboardButton(text="👤 Mon compte")],
    ],
    resize_keyboard=True
)

vip_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✅ J’ai payé", callback_data="i_paid")]
    ]
)

# =======================
# DB
# =======================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        created_at TEXT,
        last_seen_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS vip_users (
        telegram_id INTEGER PRIMARY KEY,
        expire_date TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS waiting_proof (
        telegram_id INTEGER PRIMARY KEY,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        proof_text TEXT,
        proof_type TEXT,
        status TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS prediction_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_name TEXT,
        competition TEXT,
        kickoff TEXT,
        prediction TEXT,
        confidence INTEGER,
        analysis TEXT,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()

def save_user(message: Message):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO users (telegram_id, username, full_name, created_at, last_seen_at)
    VALUES (
        ?,
        ?,
        ?,
        COALESCE((SELECT created_at FROM users WHERE telegram_id = ?), ?),
        ?
    )
    """, (
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.full_name or "",
        message.from_user.id,
        now_iso(),
        now_iso()
    ))
    conn.commit()
    conn.close()

def is_vip(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT expire_date FROM vip_users WHERE telegram_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False

    try:
        expiry = datetime.fromisoformat(row["expire_date"])
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry > datetime.now(timezone.utc)
    except Exception:
        return False

def get_vip_expiry(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT expire_date FROM vip_users WHERE telegram_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["expire_date"] if row else None

def activate_vip(user_id: int, days: int = 30):
    expiry = datetime.now(timezone.utc) + timedelta(days=days)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO vip_users (telegram_id, expire_date)
    VALUES (?, ?)
    """, (user_id, expiry.isoformat()))
    conn.commit()
    conn.close()

def set_waiting_proof(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO waiting_proof (telegram_id, created_at)
    VALUES (?, ?)
    """, (user_id, now_iso()))
    conn.commit()
    conn.close()

def clear_waiting_proof(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM waiting_proof WHERE telegram_id = ?", (user_id,))
    conn.commit()
    conn.close()

def is_waiting_proof(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM waiting_proof WHERE telegram_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None

def add_payment(user_id: int, proof_text: str, proof_type: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO payments (telegram_id, proof_text, proof_type, status, created_at)
    VALUES (?, ?, ?, 'pending', ?)
    """, (user_id, proof_text, proof_type, now_iso()))
    conn.commit()
    conn.close()

def count_users():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM users")
    row = cur.fetchone()
    conn.close()
    return int(row["c"])

def count_vip():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT expire_date FROM vip_users")
    rows = cur.fetchall()
    conn.close()

    total = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        try:
            expiry = datetime.fromisoformat(row["expire_date"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > now:
                total += 1
        except Exception:
            pass
    return total

def save_prediction_history(match_name, competition, kickoff, prediction, confidence, analysis):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO prediction_history (
        match_name, competition, kickoff, prediction, confidence, analysis, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (match_name, competition, kickoff, prediction, confidence, analysis, now_iso()))
    conn.commit()
    conn.close()

def get_last_predictions(limit=10):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    SELECT * FROM prediction_history
    ORDER BY id DESC
    LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

# =======================
# FOOT API
# =======================
def api_get(path, params=None):
    headers = {"X-Auth-Token": FOOT_API_KEY}
    r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def get_standings_map(code):
    data = api_get(f"/competitions/{code}/standings")
    table = {}
    standings = data.get("standings", [])
    if not standings:
        return table

    rows = standings[0].get("table", [])
    for row in rows:
        team = row.get("team", {})
        if team.get("id") and row.get("position"):
            table[team["id"]] = row["position"]
    return table

# =======================
# V7 SCORING
# =======================
def compute_confidence_and_pick(home_name, away_name, home_pos, away_pos):
    if home_pos is None or away_pos is None:
        return {
            "prediction": "Over 1.5 buts",
            "confidence": 58,
            "analysis": "Classement indisponible, choix prudent.",
            "rank_score": 58
        }

    diff = away_pos - home_pos
    abs_diff = abs(diff)

    # Base score
    score = 50 + min(abs_diff * 4, 28)

    # Marché choisi
    if diff >= 8:
        prediction = "1X"
        analysis = f"{home_name} est largement mieux classé et joue à domicile."
        score += 10
    elif diff >= 4:
        prediction = "Victoire ou nul domicile"
        analysis = f"{home_name} a un avantage sérieux au classement."
        score += 6
    elif diff <= -8:
        prediction = "X2"
        analysis = f"{away_name} est largement mieux classé malgré le déplacement."
        score += 10
    elif diff <= -4:
        prediction = "Victoire ou nul extérieur"
        analysis = f"{away_name} a un meilleur profil sur le classement."
        score += 6
    else:
        prediction = "Over 1.5 buts"
        analysis = "Match plus équilibré, on reste sur un marché prudent."
        score += 2

    # Petite pénalité si trop équilibré
    if abs_diff <= 1:
        score -= 8
    elif abs_diff == 2:
        score -= 4

    # Encadrement
    score = max(55, min(score, 88))

    return {
        "prediction": prediction,
        "confidence": score,
        "analysis": analysis,
        "rank_score": score
    }

def build_predictions():
    data = api_get("/matches", params={"competitions": ",".join(LEAGUES)})
    matches = data.get("matches", [])

    if not matches:
        return ["Aucun match trouvé aujourd’hui."]

    standings_cache = {}
    scored_matches = []

    for match in matches:
        if match.get("status") not in ["TIMED", "SCHEDULED"]:
            continue

        comp = match.get("competition", {})
        code = comp.get("code")
        comp_name = comp.get("name", "Compétition")

        home = match.get("homeTeam", {})
        away = match.get("awayTeam", {})
        home_id = home.get("id")
        away_id = away.get("id")
        home_name = home.get("name", "Home")
        away_name = away.get("name", "Away")
        kickoff = match.get("utcDate", "")

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

        result = compute_confidence_and_pick(home_name, away_name, home_pos, away_pos)

        scored_matches.append({
            "match_name": f"{home_name} vs {away_name}",
            "competition": comp_name,
            "kickoff": kickoff,
            "prediction": result["prediction"],
            "confidence": result["confidence"],
            "analysis": result["analysis"],
            "rank_score": result["rank_score"]
        })

    if not scored_matches:
        return ["Aucun match exploitable trouvé aujourd’hui."]

    # Trier par confiance décroissante
    scored_matches.sort(key=lambda x: x["rank_score"], reverse=True)

    # Prendre les 5 meilleurs
    top = scored_matches[:5]

    out = []
    for item in top:
        save_prediction_history(
            item["match_name"],
            item["competition"],
            item["kickoff"],
            item["prediction"],
            item["confidence"],
            item["analysis"]
        )

        out.append(
            f"⚽ <b>{item['match_name']}</b>\n"
            f"🏆 {item['competition']}\n"
            f"🕒 {item['kickoff']}\n"
            f"🎯 Prono : <b>{item['prediction']}</b>\n"
            f"📊 Confiance : {item['confidence']}%\n"
            f"🧠 Analyse : {item['analysis']}"
        )

    return out

async def send_predictions(bot: Bot, chat_id: int):
    preds = await asyncio.to_thread(build_predictions)
    text = "⚽ <b>Top 5 pronostics du jour</b>\n\n" + "\n\n".join(preds)
    await bot.send_message(chat_id, text)

# =======================
# HANDLERS
# =======================
@dp.message(CommandStart())
async def start_cmd(message: Message):
    save_user(message)
    await message.answer(
        "👋 Bienvenue sur <b>PRONOSTIC BOT V7</b>\n\nChoisis une option :",
        reply_markup=menu
    )

@dp.message(Command("id"))
async def id_cmd(message: Message):
    save_user(message)
    await message.answer(f"Ton ID : <code>{message.chat.id}</code>")

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    save_user(message)
    if message.from_user.id != ADMIN_ID:
        await message.answer("Commande réservée à l’admin.")
        return

    await message.answer(
        "📊 <b>Stats bot</b>\n\n"
        f"Utilisateurs : <b>{count_users()}</b>\n"
        f"VIP actifs : <b>{count_vip()}</b>"
    )

@dp.message(Command("history"))
async def history_cmd(message: Message):
    save_user(message)
    if message.from_user.id != ADMIN_ID:
        await message.answer("Commande réservée à l’admin.")
        return

    rows = get_last_predictions(10)
    if not rows:
        await message.answer("Aucun historique pour le moment.")
        return

    text = "📚 <b>Derniers pronostics enregistrés</b>\n\n"
    for row in rows:
        text += (
            f"⚽ {row['match_name']}\n"
            f"🎯 {row['prediction']} | {row['confidence']}%\n"
            f"🏆 {row['competition']}\n\n"
        )

    await message.answer(text)

@dp.message(Command("sendtest"))
async def sendtest_cmd(message: Message):
    save_user(message)
    if message.from_user.id != ADMIN_ID:
        await message.answer("Commande réservée à l’admin.")
        return

    try:
        await send_predictions(message.bot, message.chat.id)
    except Exception as e:
        await message.answer(f"❌ Erreur : {e}")

@dp.message(Command("vip"))
async def vip_admin_cmd(message: Message):
    save_user(message)
    if message.from_user.id != ADMIN_ID:
        return

    try:
        parts = message.text.split()
        user_id = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30
        activate_vip(user_id, days)
        clear_waiting_proof(user_id)
        await message.answer(f"✅ VIP activé pour {user_id} pendant {days} jours.")
        await message.bot.send_message(user_id, f"🎉 Ton VIP est activé pour {days} jours.")
    except Exception:
        await message.answer("Usage : /vip user_id 30")

@dp.message(F.text == "⚽ Pronostics foot")
async def foot_btn(message: Message):
    save_user(message)
    await message.answer("⏳ Analyse des meilleurs matchs du jour...")
    try:
        preds = await asyncio.to_thread(build_predictions)
        await message.answer("⚽ <b>Top 5 pronostics foot</b>\n\n" + "\n\n".join(preds))
    except Exception as e:
        await message.answer(f"❌ Erreur API foot : {e}")

@dp.message(F.text == "📈 Signaux trading")
async def trade_btn(message: Message):
    save_user(message)

    if not is_vip(message.from_user.id):
        await message.answer(
            "🔒 Les signaux trading sont réservés aux VIP.\n\n"
            "💎 Offre VIP\n"
            f"Prix : <b>{VIP_PRICE}</b>",
            reply_markup=vip_keyboard
        )
        return

    await message.answer(
        "📈 <b>Signaux trading VIP</b>\n\n"
        "1. XAUUSD — BUY\n"
        "Entrée : 2945\nSL : 2937\nTP : 2958\nTF : M15\n\n"
        "2. BTCUSD — SELL\n"
        "Entrée : 84250\nSL : 84720\nTP : 83380\nTF : M15\n\n"
        "⚠️ Le trading comporte des risques."
    )

@dp.message(F.text == "💎 Offre VIP")
async def vip_btn(message: Message):
    save_user(message)

    if is_vip(message.from_user.id):
        expiry = get_vip_expiry(message.from_user.id) or "—"
        await message.answer(f"✅ Tu es déjà VIP.\nExpiration : <code>{expiry}</code>")
        return

    await message.answer(
        "💎 <b>OFFRE VIP</b>\n\n"
        "• Pronostics premium\n"
        "• Signaux trading VIP\n"
        "• Alertes prioritaires\n\n"
        f"Prix : <b>{VIP_PRICE}</b>\n\n"
        "Paiement :\n"
        "• MTN Money : <b>225 0503046394</b>\n"
        "• Orange Money : <b>225 0713882466</b>\n"
        "• Moov Money : <b>225 0161380810</b>\n"
        "• Wave : <b>225 0713882466</b>\n\n"
        "Après paiement, clique sur le bouton ci-dessous.",
        reply_markup=vip_keyboard
    )

@dp.message(F.text == "👤 Mon compte")
async def account_btn(message: Message):
    save_user(message)
    vip_status = "✅ Actif" if is_vip(message.from_user.id) else "❌ Inactif"
    expiry = get_vip_expiry(message.from_user.id) or "—"
    waiting = "Oui" if is_waiting_proof(message.from_user.id) else "Non"

    await message.answer(
        "👤 <b>Mon compte</b>\n\n"
        f"ID : <code>{message.from_user.id}</code>\n"
        f"Nom : {message.from_user.full_name}\n"
        f"VIP : {vip_status}\n"
        f"Expiration VIP : <code>{expiry}</code>\n"
        f"Preuve en attente : {waiting}"
    )

# =======================
# CALLBACKS
# =======================
@dp.callback_query(F.data == "i_paid")
async def i_paid_callback(callback: CallbackQuery):
    set_waiting_proof(callback.from_user.id)
    await callback.message.answer(
        "✅ D’accord.\n\nEnvoie maintenant ta preuve de paiement.\n"
        "Tu peux envoyer une photo ou un texte avec la référence."
    )
    await callback.answer()

# =======================
# PAYMENT PROOF
# =======================
@dp.message(F.photo)
async def photo_handler(message: Message):
    save_user(message)

    if not is_waiting_proof(message.from_user.id):
        await message.answer("Photo reçue. Pour un paiement, clique d’abord sur 💎 Offre VIP puis ✅ J’ai payé.")
        return

    add_payment(message.from_user.id, "photo", "photo")

    caption = (
        "💰 <b>Nouvelle preuve de paiement</b>\n\n"
        f"Nom : {message.from_user.full_name}\n"
        f"User ID : <code>{message.from_user.id}</code>\n"
        f"Username : @{message.from_user.username or 'aucun'}\n\n"
        f"Pour activer : <code>/vip {message.from_user.id} 30</code>"
    )

    await message.bot.send_photo(
        ADMIN_ID,
        message.photo[-1].file_id,
        caption=caption
    )

    await message.answer("✅ Preuve reçue. L’admin va vérifier.")

@dp.message(F.text)
async def text_handler(message: Message):
    save_user(message)

    known = {"⚽ Pronostics foot", "📈 Signaux trading", "💎 Offre VIP", "👤 Mon compte"}
    if message.text in known:
        return

    if message.text.startswith("/"):
        return

    if is_waiting_proof(message.from_user.id):
        add_payment(message.from_user.id, message.text, "text")

        await message.bot.send_message(
            ADMIN_ID,
            "💰 <b>Nouvelle référence paiement</b>\n\n"
            f"Nom : {message.from_user.full_name}\n"
            f"User ID : <code>{message.from_user.id}</code>\n"
            f"Message : {message.text}\n\n"
            f"Pour activer : <code>/vip {message.from_user.id} 30</code>"
        )

        await message.answer("✅ Référence reçue. L’admin va vérifier.")
        return

    await message.answer("Message reçu 👍 Utilise le menu.")

# =======================
# MAIN
# =======================
async def main():
    print("Initialisation V7...", flush=True)
    init_db()

    print("Connexion bot...", flush=True)
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    print("Bot V7 connecté. En attente des messages...", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())