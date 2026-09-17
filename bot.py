"""
Bot Telegram: hitung join & left grup per hari + total member saat ini,
kirim laporan otomatis tiap pergantian hari (00:00) via DM ke SEMUA admin grup.

Cara kerja singkat:
- Setiap ada member join/left, bot mencatat ke database PostgreSQL
  (di-hosting di Railway) berdasarkan chat_id dan tanggal (zona waktu
  Asia/Jakarta).
- Setiap jam 00:00, bot mengambil daftar admin grup (otomatis, jadi
  mendukung banyak admin tanpa perlu setting manual) lalu mengirim
  laporan hari SEBELUMNYA ke masing-masing admin via chat pribadi (DM).
  Laporan ini juga menyertakan TOTAL MEMBER grup saat ini (diambil
  langsung dari Telegram, lalu disimpan ke database sebagai histori).
- Admin juga bisa ketik /report di grup untuk minta laporan hari
  berjalan (real-time, termasuk total member terkini), akan dikirim
  ke DM admin yang minta.

KENAPA POSTGRESQL DI RAILWAY (bukan file SQLite lagi):
- File SQLite (bot_data.db) hidup di disk lokal proses bot. Di Railway,
  filesystem itu EPHEMERAL — setiap kali service di-redeploy/restart,
  isinya bisa hilang. Jadi datanya harus dipindah ke database
  managed (Postgres) yang hidup terpisah dari proses bot & persisten.
- Railway menyediakan PostgreSQL sebagai plugin terpisah, lalu
  otomatis inject connection string-nya lewat environment variable
  `DATABASE_URL` ke service bot ini (lihat README untuk cara setup).
- Semua perubahan tetap ditulis lewat transaksi database yang atomik,
  jadi kalau bot tiba-tiba mati/crash di tengah proses, data yang
  sudah tersimpan tidak ikut corrupt/hilang.
- Histori total member per hari juga ikut tersimpan permanen di
  database, bukan cuma dihitung ulang tiap kali dibutuhkan.

PENTING (batasan Telegram, bukan bug):
- Bot TIDAK BISA mengirim DM ke user yang belum pernah memulai chat
  dengan bot. Jadi setiap admin WAJIB klik /start ke bot ini di chat
  pribadi minimal sekali, baru bisa menerima laporan.
- Laporan tidak mungkin dikirim ke grup tapi "hanya terlihat admin",
  karena Telegram tidak punya fitur pesan grup yang disembunyikan dari
  sebagian anggota. Makanya laporan dikirim via DM ke tiap admin.
"""

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Status yang dianggap "masih ada di grup" (dipakai untuk mendeteksi join/left
# lewat perubahan status member, bukan lewat service message join/left biasa).
IN_CHAT_STATUSES = (
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
    ChatMemberStatus.RESTRICTED,
)

# ====== KONFIGURASI ======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "GANTI_DENGAN_TOKEN_BOT_ANDA")
TIMEZONE = ZoneInfo("Asia/Jakarta")  # ganti sesuai zona waktu kamu

# Railway otomatis mengisi variabel ini kalau kamu tambahkan plugin
# PostgreSQL dan link-kan ke service bot ini (lihat README).
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# ==========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Connection pool global, dibuat sekali saat bot startup (lihat post_init).
pool: asyncpg.Pool | None = None


# ====================================================================
# LAPISAN DATABASE (PostgreSQL via asyncpg)
# ====================================================================

def _normalize_dsn(url: str) -> str:
    """Railway/Heroku kadang kasih 'postgres://', asyncpg maunya 'postgresql://'."""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


async def init_db() -> None:
    """Buat connection pool + pastikan tabel sudah ada. Dipanggil sekali saat startup."""
    global pool
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL belum di-set. Di Railway: tambahkan plugin PostgreSQL, "
            "lalu pastikan variabel DATABASE_URL sudah ter-link ke service bot ini."
        )
    pool = await asyncpg.create_pool(_normalize_dsn(DATABASE_URL), min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id BIGINT PRIMARY KEY,
                title TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                chat_id BIGINT NOT NULL,
                date DATE NOT NULL,
                join_count INTEGER NOT NULL DEFAULT 0,
                left_count INTEGER NOT NULL DEFAULT 0,
                total_members INTEGER,
                PRIMARY KEY (chat_id, date)
            );
            """
        )
    logger.info("Database siap (PostgreSQL).")


async def close_db() -> None:
    global pool
    if pool is not None:
        await pool.close()


async def upsert_chat(chat_id: int, title: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chats (chat_id, title) VALUES ($1, $2)
            ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title
            """,
            chat_id, title,
        )


async def bump(chat_id: int, key: str) -> None:
    """Tambah counter join/left untuk chat & tanggal hari ini."""
    assert key in ("join", "left")  # whitelist, cegah SQL injection lewat nama kolom
    column = f"{key}_count"
    today = datetime.now(TIMEZONE).date()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO daily_stats (chat_id, date, {column}) VALUES ($1, $2, 1)
            ON CONFLICT (chat_id, date) DO UPDATE SET {column} = daily_stats.{column} + 1
            """,
            chat_id, today,
        )


async def set_total_members(chat_id: int, date_str: str, total: int) -> None:
    date_val = datetime.fromisoformat(date_str).date()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO daily_stats (chat_id, date, total_members) VALUES ($1, $2, $3)
            ON CONFLICT (chat_id, date) DO UPDATE SET total_members = EXCLUDED.total_members
            """,
            chat_id, date_val, total,
        )


async def get_stats(chat_id: int, date_str: str) -> dict:
    date_val = datetime.fromisoformat(date_str).date()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT join_count, left_count, total_members FROM daily_stats "
            "WHERE chat_id = $1 AND date = $2",
            chat_id, date_val,
        )
    if row is None:
        return {"join": 0, "left": 0, "total_members": None}
    return {
        "join": row["join_count"],
        "left": row["left_count"],
        "total_members": row["total_members"],
    }


async def get_known_chat_ids() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT chat_id FROM chats")
    return [row["chat_id"] for row in rows]


# ====================================================================
# HELPER TELEGRAM
# ====================================================================

async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False


async def fetch_total_members(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Ambil total member grup langsung dari Telegram. None kalau gagal."""
    try:
        return await context.bot.get_chat_member_count(chat_id)
    except Exception as e:
        logger.warning("Gagal ambil total member grup %s: %s", chat_id, e)
        return None


def build_report_text(stats: dict, title: str, day: str) -> str:
    lines = [
        f"📊 Laporan hari ini ({day})",
        f"Grup: {title}",
        f"➕ Join: {stats['join']}",
        f"➖ Left: {stats['left']}",
    ]
    if stats.get("total_members") is not None:
        lines.append(f"👥 Total member saat ini: {stats['total_members']}")
    return "\n".join(lines)


async def get_admin_group_choices(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list:
    """Cari grup yang dipantau bot di mana user_id adalah admin."""
    chat_ids = await get_known_chat_ids()
    choices = []
    for chat_id in chat_ids:
        if not await is_admin(chat_id, user_id, context):
            continue
        try:
            info = await context.bot.get_chat(chat_id)
        except Exception:
            continue
        choices.append((chat_id, info.title))
    return choices


# ====================================================================
# HANDLERS
# ====================================================================

async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dipanggil tiap kali status seseorang di grup berubah (join, left, di-kick,
    di-add, dsb). Ini menggantikan pendekatan lama yang membaca service
    message new_chat_members/left_chat_member, karena service message itu
    TIDAK dikirim kalau grup mengaktifkan setelan "Hide Members Who Joined/Left"
    — dengan ChatMemberHandler, event tetap terdeteksi walau setelan itu aktif.

    CATATAN: supaya event ini diterima, bot WAJIB jadi admin di grup (syarat
    dari Telegram API, bukan pilihan).
    """
    result = update.chat_member
    if result is None:
        return

    chat = update.effective_chat
    member_user = result.new_chat_member.user

    was_in_chat = result.old_chat_member.status in IN_CHAT_STATUSES
    is_in_chat = result.new_chat_member.status in IN_CHAT_STATUSES

    if was_in_chat == is_in_chat:
        # Perubahan status lain (misal member -> administrator) tapi tidak
        # relevan buat statistik join/left.
        return

    await upsert_chat(chat.id, chat.title)

    if not was_in_chat and is_in_chat:
        # --- JOIN ---
        if member_user.id == context.bot.id:
            # Bot sendiri yang baru ditambahkan ke grup -> jangan dihitung
            # sebagai "join" member.
            return
        await bump(chat.id, "join")

    else:
        # --- LEFT ---
        if member_user.id == context.bot.id:
            return
        await bump(chat.id, "left")


async def on_new_member_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dipanggil saat ada pesan sistem "X added Y" atau "X joined the group".
    Statistik join TIDAK dihitung di sini (itu tugas on_chat_member_update
    di atas) — fungsi ini hanya untuk menghapus pesan sistemnya, baik yang
    di-*add* oleh orang lain maupun yang join sendiri lewat link undangan,
    supaya grup tidak penuh notifikasi join sama sekali.

    Pengecualian: pesan soal BOT ini sendiri yang ditambahkan ke grup tidak
    dihapus (biar ada jejak kapan bot mulai aktif di grup itu).
    """
    message = update.message
    if not message or not message.new_chat_members:
        return

    if all(member.id == context.bot.id for member in message.new_chat_members):
        return

    try:
        await context.bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.warning(
            "Gagal hapus pesan join di chat %s (mungkin bot bukan admin "
            "atau tidak punya izin 'Delete messages'): %s",
            message.chat.id, e,
        )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != Chat.PRIVATE:
        await upsert_chat(chat.id, chat.title)
    await update.message.reply_text(
        "Bot aktif ✅\n\n"
        "Tambahkan bot ini ke grup untuk mulai menghitung join & left harian.\n"
        "Laporan otomatis dikirim tiap jam 00:00 via DM ke semua admin grup.\n\n"
        "Admin bisa ketik /report di grup, ATAU langsung di DM ini, untuk cek laporan hari ini."
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    today = datetime.now(TIMEZONE).date().isoformat()

    # --- Dipanggil dari dalam grup: perilaku lama, hasil dikirim ke DM ---
    if chat.type != Chat.PRIVATE:
        await upsert_chat(chat.id, chat.title)

        if not await is_admin(chat.id, user.id, context):
            await update.message.reply_text("Maaf, perintah ini hanya untuk admin grup.")
            return

        total = await fetch_total_members(chat.id, context)
        if total is not None:
            await set_total_members(chat.id, today, total)
        stats = await get_stats(chat.id, today)
        text = build_report_text(stats, chat.title, today)

        try:
            await context.bot.send_message(user.id, text)
            await update.message.reply_text("✅ Laporan sudah dikirim ke DM kamu.")
        except Exception:
            await update.message.reply_text(
                "⚠️ Gagal kirim DM. Pastikan kamu sudah pernah /start bot ini di chat pribadi."
            )
        return

    # --- Dipanggil langsung dari DM ---
    choices = await get_admin_group_choices(user.id, context)

    if not choices:
        await update.message.reply_text(
            "Kamu belum jadi admin di grup manapun yang dipantau bot ini."
        )
        return

    if len(choices) == 1:
        chat_id, title = choices[0]
        total = await fetch_total_members(chat_id, context)
        if total is not None:
            await set_total_members(chat_id, today, total)
        stats = await get_stats(chat_id, today)
        await update.message.reply_text(build_report_text(stats, title, today))
        return

    # Lebih dari satu grup -> kasih tombol pilihan
    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"report:{chat_id}")]
        for chat_id, title in choices
    ]
    await update.message.reply_text(
        "Kamu admin di beberapa grup yang dipantau. Pilih grup:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_report_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = int(query.data.split(":", 1)[1])
    user = query.from_user

    if not await is_admin(chat_id, user.id, context):
        await query.edit_message_text("Kamu bukan admin di grup itu (lagi).")
        return

    try:
        info = await context.bot.get_chat(chat_id)
    except Exception:
        await query.edit_message_text("Gagal ambil info grup.")
        return

    today = datetime.now(TIMEZONE).date().isoformat()
    total = await fetch_total_members(chat_id, context)
    if total is not None:
        await set_total_members(chat_id, today, total)
    stats = await get_stats(chat_id, today)
    await query.edit_message_text(build_report_text(stats, info.title, today))


async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """Dijalankan otomatis tiap 00:00 — kirim laporan hari kemarin ke semua admin."""
    yesterday = (datetime.now(TIMEZONE).date() - timedelta(days=1)).isoformat()
    chat_ids = await get_known_chat_ids()

    for chat_id in chat_ids:
        try:
            chat = await context.bot.get_chat(chat_id)
            admins = await context.bot.get_chat_administrators(chat_id)
        except Exception as e:
            logger.warning("Gagal ambil info grup %s: %s", chat_id, e)
            continue

        # Simpan snapshot total member hari kemarin (diambil saat ini,
        # sebagai representasi terbaik yang tersedia) supaya histori
        # total member tetap tersimpan di database.
        total = await fetch_total_members(chat_id, context)
        if total is not None:
            await set_total_members(chat_id, yesterday, total)

        stats = await get_stats(chat_id, yesterday)
        if stats["join"] == 0 and stats["left"] == 0 and stats["total_members"] is None:
            continue

        text = build_report_text(stats, chat.title, yesterday).replace(
            "hari ini", "harian"
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


async def post_init(application: Application) -> None:
    """Dijalankan sekali oleh python-telegram-bot saat startup, sebelum polling mulai."""
    await init_db()


async def post_shutdown(application: Application) -> None:
    """Dijalankan sekali saat bot berhenti, supaya connection pool ditutup rapi."""
    await close_db()


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CallbackQueryHandler(on_report_button, pattern=r"^report:"))
    app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member_message))

    midnight = datetime.strptime("00:00", "%H:%M").time().replace(tzinfo=TIMEZONE)
    app.job_queue.run_daily(send_daily_report, time=midnight)

    logger.info("Bot berjalan...")
    # allowed_updates=Update.ALL_TYPES wajib di-set, karena update tipe
    # "chat_member" tidak dikirim Telegram secara default kalau tidak
    # diminta eksplisit.
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
