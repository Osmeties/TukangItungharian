"""
Bot Telegram: hitung join & left grup per hari, kirim laporan otomatis
tiap pergantian hari (00:00) via DM ke SEMUA admin grup.

Cara kerja singkat:
- Setiap ada member join/left, bot mencatat ke file member_stats.json
  berdasarkan chat_id dan tanggal (zona waktu Asia/Jakarta).
- Setiap jam 00:00, bot mengambil daftar admin grup (otomatis, jadi
  mendukung banyak admin tanpa perlu setting manual) lalu mengirim
  laporan hari SEBELUMNYA ke masing-masing admin via chat pribadi (DM).
- Admin juga bisa ketik /report di grup untuk minta laporan hari
  berjalan (real-time), akan dikirim ke DM admin yang minta.

PENTING (batasan Telegram, bukan bug):
- Bot TIDAK BISA mengirim DM ke user yang belum pernah memulai chat
  dengan bot. Jadi setiap admin WAJIB klik /start ke bot ini di chat
  pribadi minimal sekali, baru bisa menerima laporan.
- Laporan tidak mungkin dikirim ke grup tapi "hanya terlihat admin",
  karena Telegram tidak punya fitur pesan grup yang disembunyikan dari
  sebagian anggota. Makanya laporan dikirim via DM ke tiap admin.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Chat, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ====== KONFIGURASI ======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "GANTI_DENGAN_TOKEN_BOT_ANDA")
TIMEZONE = ZoneInfo("Asia/Jakarta")  # ganti sesuai zona waktu kamu
DATA_FILE = "member_stats.json"
# ==========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def bump(chat_id: int, key: str) -> None:
    """Tambah counter join/left untuk chat & tanggal hari ini."""
    data = load_data()
    chat_key = str(chat_id)
    today = datetime.now(TIMEZONE).date().isoformat()
    data.setdefault(chat_key, {}).setdefault(today, {"join": 0, "left": 0})
    data[chat_key][today][key] += 1
    save_data(data)


async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.new_chat_members:
        for _ in update.message.new_chat_members:
            bump(update.effective_chat.id, "join")


async def on_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.left_chat_member:
        bump(update.effective_chat.id, "left")


async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot aktif ✅\n\n"
        "Tambahkan bot ini ke grup untuk mulai menghitung join & left harian.\n"
        "Laporan otomatis dikirim tiap jam 00:00 via DM ke semua admin grup.\n\n"
        "Admin bisa ketik /report di grup untuk cek laporan hari ini."
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == Chat.PRIVATE:
        await update.message.reply_text("Perintah ini dipakai di dalam grup, bukan di DM.")
        return

    if not await is_admin(chat.id, user.id, context):
        await update.message.reply_text("Maaf, perintah ini hanya untuk admin grup.")
        return

    data = load_data()
    today = datetime.now(TIMEZONE).date().isoformat()
    stats = data.get(str(chat.id), {}).get(today, {"join": 0, "left": 0})

    text = (
        f"📊 Laporan hari ini ({today})\n"
        f"Grup: {chat.title}\n"
        f"➕ Join: {stats['join']}\n"
        f"➖ Left: {stats['left']}"
    )

    try:
        await context.bot.send_message(user.id, text)
        await update.message.reply_text("✅ Laporan sudah dikirim ke DM kamu.")
    except Exception:
        await update.message.reply_text(
            "⚠️ Gagal kirim DM. Pastikan kamu sudah pernah /start bot ini di chat pribadi."
        )


async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """Dijalankan otomatis tiap 00:00 — kirim laporan hari kemarin ke semua admin."""
    data = load_data()
    yesterday = (datetime.now(TIMEZONE).date() - timedelta(days=1)).isoformat()

    for chat_id_str, days in data.items():
        stats = days.get(yesterday)
        if not stats:
            continue

        chat_id = int(chat_id_str)
        try:
            chat = await context.bot.get_chat(chat_id)
            admins = await context.bot.get_chat_administrators(chat_id)
        except Exception as e:
            logger.warning("Gagal ambil info grup %s: %s", chat_id, e)
            continue

        text = (
            f"📊 Laporan Harian Grup\n"
            f"Grup: {chat.title}\n"
            f"🗓️ Tanggal: {yesterday}\n"
            f"➕ Join: {stats['join']}\n"
            f"➖ Left: {stats['left']}"
        )

        for admin in admins:
            if admin.user.is_bot:
                continue
            try:
                await context.bot.send_message(admin.user.id, text)
            except Exception as e:
                logger.warning(
                    "Gagal kirim DM ke admin %s (%s): %s — admin mungkin belum /start bot",
                    admin.user.id, admin.user.full_name, e,
                )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_member))

    midnight = datetime.strptime("00:00", "%H:%M").time().replace(tzinfo=TIMEZONE)
    app.job_queue.run_daily(send_daily_report, time=midnight)

    logger.info("Bot berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
