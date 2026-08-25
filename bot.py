"""
Telegram Social Media Downloader Bot
=====================================
Dev: Anas | Owner: SAHAT ANAS (@sahatanas)

Supports: Facebook, Instagram, TikTok, Twitter/X, Pinterest, Reddit,
          Dailymotion, Vimeo, and 1000+ other sites via yt-dlp.
          (YouTube intentionally excluded)
"""

import os
import re
import gc
import time
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMember,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BOT_TOKEN         = os.environ.get("BOT_TOKEN")
BOT_DEV           = "Anas"
OWNER_ID          = 7701549179
CONTACT_USERNAME  = "@sahatanas"
SUPPORT_URL       = "https://t.me/sahatanas"

FORCE_JOIN_CHANNELS = [
    {"username": "@sahatanas",  "url": "https://t.me/sahatanas"},
    {"username": "@sahatanass", "url": "https://t.me/sahatanass"},
]

DB_PATH      = os.environ.get("DB_PATH", "bot_data.db")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "downloads")

MAX_DAILY_DOWNLOADS   = 2
MAX_FILE_SIZE_MB      = 50
MAX_DURATION_SECONDS  = 30 * 60
MAX_RESOLUTION_FORMAT = (
    "bestvideo[height<=720]+bestaudio/best[height<=720]/best[height<=720]/best"
)
MAX_CONCURRENT_DOWNLOADS = 1

YOUTUBE_DOMAINS = re.compile(
    r"(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be)/\S+",
    re.IGNORECASE,
)
GENERIC_URL_REGEX = re.compile(r"https?://[^\s]+", re.IGNORECASE)

SUPPORTED_PLATFORMS = [
    "Facebook", "Instagram", "TikTok", "Twitter / X",
    "Pinterest", "Reddit", "Dailymotion", "Vimeo",
    "Snapchat", "LinkedIn", "Twitch Clips", "SoundCloud",
    "and 1000+ more sites via yt-dlp",
]

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("social_downloader_bot")

download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
active_status_text = {}


# --------------------------------------------------------------------------- #
# Database layer
# --------------------------------------------------------------------------- #

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            is_vip     INTEGER NOT NULL DEFAULT 0,
            joined_at  TEXT    NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            url           TEXT,
            downloaded_at TEXT    NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def ensure_user(user_id: int, username: str):
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO users (user_id, username, is_vip, joined_at) VALUES (?, ?, 0, ?)",
            (user_id, username, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    conn.close()


def is_vip(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("SELECT is_vip FROM users WHERE user_id = ?", (user_id,))
    row  = cur.fetchone()
    conn.close()
    return bool(row and row["is_vip"] == 1)


def set_vip(user_id: int, value: int):
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("UPDATE users SET is_vip = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()


def get_downloads_last_24h(user_id: int) -> int:
    conn  = db_connect()
    cur   = conn.cursor()
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cur.execute(
        "SELECT COUNT(*) AS c FROM downloads WHERE user_id = ? AND downloaded_at >= ?",
        (user_id, since),
    )
    row  = cur.fetchone()
    conn.close()
    return row["c"] if row else 0


def record_download(user_id: int, url: str):
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute(
        "INSERT INTO downloads (user_id, url, downloaded_at) VALUES (?, ?, ?)",
        (user_id, url, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def can_download(user_id: int) -> bool:
    if is_vip(user_id):
        return True
    return get_downloads_last_24h(user_id) < MAX_DAILY_DOWNLOADS


# --------------------------------------------------------------------------- #
# Force-join check
# --------------------------------------------------------------------------- #

async def check_force_join(bot, user_id: int):
    not_joined = []
    for ch in FORCE_JOIN_CHANNELS:
        try:
            member = await bot.get_chat_member(ch["username"], user_id)
            if member.status in (ChatMember.LEFT, ChatMember.BANNED):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined


def force_join_keyboard(missing):
    buttons = [[InlineKeyboardButton(f"➕ Join {ch['username']}", url=ch["url"])]
               for ch in missing]
    buttons.append([InlineKeyboardButton("✅ I Joined — Try Again", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)


# --------------------------------------------------------------------------- #
# UI helpers
# --------------------------------------------------------------------------- #

def status_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Live Status", callback_data="live_status"),
        InlineKeyboardButton("💬 Support", url=SUPPORT_URL),
    ]])


def limit_reached_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Contact Owner for VIP", url=SUPPORT_URL),
    ]])


async def set_status(message, chat_id: int, text: str, keyboard=None):
    try:
        await message.edit_text(
            text,
            reply_markup=keyboard or status_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        active_status_text[(chat_id, message.message_id)] = text
    except Exception as exc:
        logger.warning("Failed to edit status message: %s", exc)


# --------------------------------------------------------------------------- #
# yt-dlp helpers
# --------------------------------------------------------------------------- #

def _extract_info_sync(url: str):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 20,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _download_sync(url: str, output_template: str):
    opts = {
        "format": MAX_RESOLUTION_FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "max_filesize": MAX_FILE_SIZE_MB * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 3,
        "concurrent_fragment_downloads": 1,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


async def extract_info(url: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _extract_info_sync, url)


async def download_video(url: str, output_template: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _download_sync, url, output_template)


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username or user.first_name)
    platforms_text = "\n".join(f"  • {p}" for p in SUPPORTED_PLATFORMS)
    text = (
        "👋 <b>Welcome to Social Media Downloader Bot!</b>\n\n"
        "Send me any video link and I'll download it for you (up to <b>720p</b>, max <b>50MB</b>).\n\n"
        "📱 <b>Supported Platforms:</b>\n"
        f"{platforms_text}\n\n"
        "⚠️ <b>Note:</b> YouTube is <u>not supported</u> here.\n\n"
        "📊 <b>Free Quota:</b>\n"
        f"• <b>{MAX_DAILY_DOWNLOADS} downloads / 24 hours</b>\n"
        f"• Max length: <b>{MAX_DURATION_SECONDS // 60} minutes</b>\n"
        f"• Max size: <b>{MAX_FILE_SIZE_MB}MB</b>\n\n"
        "👑 Want <b>VIP (unlimited)</b> access?\n"
        f"Contact: {CONTACT_USERNAME}\n\n"
        f"🛠 <i>Dev: {BOT_DEV}</i>"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Contact Owner", url=SUPPORT_URL),
        ]]),
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username or user.first_name)
    if is_vip(user.id):
        label = "Owner 👑" if user.id == OWNER_ID else "VIP 👑"
        text  = f"✅ <b>You have unlimited ({label}) access.</b>"
    else:
        used      = get_downloads_last_24h(user.id)
        remaining = max(0, MAX_DAILY_DOWNLOADS - used)
        text = (
            f"📊 <b>Your Quota</b>\n"
            f"Used today: <b>{used}/{MAX_DAILY_DOWNLOADS}</b>\n"
            f"Remaining: <b>{remaining}</b>\n\n"
            f"Want VIP? Contact {CONTACT_USERNAME}"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def giveaccess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("⛔ This command is for the owner only.")
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/giveaccess &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML
        )
        return
    try:
        target_id = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Must be a number.")
        return
    ensure_user(target_id, "unknown")
    set_vip(target_id, 1)
    await update.message.reply_text(
        f"✅ <b>VIP granted!</b>\nUser <code>{target_id}</code> now has unlimited access. 👑",
        parse_mode=ParseMode.HTML,
    )
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "🎉 <b>Congratulations!</b>\n\n"
                "You have been granted <b>VIP (unlimited)</b> access by the owner.\n"
                "Enjoy unlimited downloads! 👑"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def removeaccess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("⛔ This command is for the owner only.")
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/removeaccess &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML
        )
        return
    try:
        target_id = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Must be a number.")
        return
    set_vip(target_id, 0)
    await update.message.reply_text(
        f"✅ VIP removed from user <code>{target_id}</code>.",
        parse_mode=ParseMode.HTML,
    )


async def listusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("⛔ This command is for the owner only.")
        return
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("SELECT user_id, username, is_vip FROM users ORDER BY is_vip DESC, user_id")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No users yet.")
        return
    lines = ["<b>📋 All Users:</b>\n"]
    for row in rows:
        badge = "👑 VIP" if row["is_vip"] else "👤 Free"
        name  = row["username"] or "unknown"
        lines.append(f"{badge} | <code>{row['user_id']}</code> | @{name}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# Callback query handler
# --------------------------------------------------------------------------- #

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    chat_id = query.message.chat_id
    msg_id  = query.message.message_id
    user    = query.from_user

    if query.data == "live_status":
        current = active_status_text.get((chat_id, msg_id), "⏳ No active task right now.")
        plain   = re.sub(r"<[^>]+>", "", current)
        await query.answer(text=plain, show_alert=True)
    elif query.data == "check_join":
        missing = await check_force_join(context.bot, user.id)
        if missing:
            await query.answer(
                text="❌ You haven't joined all channels yet!",
                show_alert=True,
            )
        else:
            await query.answer(text="✅ Thank you! Send your video link now.", show_alert=True)
            try:
                await query.message.delete()
            except Exception:
                pass
    else:
        await query.answer()


# --------------------------------------------------------------------------- #
# Core: handle a social media link
# --------------------------------------------------------------------------- #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user    = update.effective_user
    text    = (message.text or "").strip()

    url_match = GENERIC_URL_REGEX.search(text)
    if not url_match:
        await message.reply_text(
            "🤔 That doesn't look like a video link.\n"
            "Send a link from Facebook, Instagram, TikTok, Twitter/X, etc.\n"
            "Use /start to see all supported platforms."
        )
        return

    url = url_match.group(0)

    if YOUTUBE_DOMAINS.search(url):
        await message.reply_text(
            "⛔ <b>YouTube is not supported here.</b>\n\n"
            "This bot is for other platforms:\n"
            "Instagram, TikTok, Facebook, Twitter/X, etc.\n\n"
            "Use /start to see the full list.",
            parse_mode=ParseMode.HTML,
        )
        return

    ensure_user(user.id, user.username or user.first_name)

    missing = await check_force_join(context.bot, user.id)
    if missing:
        names = " and ".join(ch["username"] for ch in missing)
        await message.reply_text(
            f"⚠️ <b>Please join our channels first!</b>\n\n"
            f"You must join <b>{names}</b> to use this bot.\n"
            f"After joining, click the button below 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=force_join_keyboard(missing),
        )
        return

    if not can_download(user.id):
        await message.reply_text(
            "🚫 <b>Daily Limit Reached</b>\n\n"
            f"You've used your <b>{MAX_DAILY_DOWNLOADS} free downloads</b> for today.\n"
            "⏰ Quota resets 24 hours after each download.\n\n"
            f"Want unlimited access? Contact {CONTACT_USERNAME} 👑",
            parse_mode=ParseMode.HTML,
            reply_markup=limit_reached_keyboard(),
        )
        return

    status_msg = await message.reply_text(
        "⏳ <b>Processing...</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=status_keyboard(),
    )
    active_status_text[(status_msg.chat_id, status_msg.message_id)] = "⏳ Processing..."

    output_path = None
    try:
        async with download_semaphore:
            try:
                info = await asyncio.wait_for(extract_info(url), timeout=30)
            except asyncio.TimeoutError:
                await set_status(status_msg, status_msg.chat_id,
                                 "⏱️ <b>Timed out</b> fetching video info. Please try again.")
                return
            except yt_dlp.utils.DownloadError as exc:
                logger.info("Info extraction failed: %s", exc)
                await set_status(status_msg, status_msg.chat_id,
                                 "❌ <b>Couldn't access this video.</b>\n"
                                 "It may be private, age-restricted, or unavailable.")
                return

            if info.get("is_live"):
                await set_status(status_msg, status_msg.chat_id,
                                 "🔴 <b>Live streams are not supported.</b>")
                return

            duration = info.get("duration") or 0
            if duration and duration > MAX_DURATION_SECONDS:
                await set_status(status_msg, status_msg.chat_id,
                                 f"📏 <b>Video too long.</b>\nMax is {MAX_DURATION_SECONDS // 60} minutes.")
                return

            approx_size = info.get("filesize") or info.get("filesize_approx")
            if approx_size and approx_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                await set_status(status_msg, status_msg.chat_id,
                                 f"📦 <b>File too large.</b>\nExceeds {MAX_FILE_SIZE_MB}MB limit at 720p.")
                return

            extractor = info.get("extractor_key") or info.get("extractor") or "Unknown"
            await set_status(status_msg, status_msg.chat_id,
                             f"📥 <b>Downloading from {extractor}...</b>")

            safe_name       = f"{user.id}_{int(time.time())}"
            output_template = os.path.join(DOWNLOAD_DIR, f"{safe_name}.%(ext)s")

            try:
                output_path = await asyncio.wait_for(
                    download_video(url, output_template), timeout=600
                )
            except asyncio.TimeoutError:
                await set_status(status_msg, status_msg.chat_id,
                                 "⏱️ <b>Download timed out.</b> Please try again later.")
                return
            except yt_dlp.utils.DownloadError as exc:
                logger.info("Download failed: %s", exc)
                await set_status(status_msg, status_msg.chat_id,
                                 "❌ <b>Download failed.</b>\nVideo may be restricted or unavailable.")
                return

            if not output_path or not os.path.exists(output_path):
                await set_status(status_msg, status_msg.chat_id,
                                 "❌ <b>Something went wrong.</b> File was not created.")
                return

            actual_size = os.path.getsize(output_path)
            if actual_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                await set_status(status_msg, status_msg.chat_id,
                                 f"📦 <b>File too large to send.</b> Exceeds {MAX_FILE_SIZE_MB}MB.")
                return

            await set_status(status_msg, status_msg.chat_id, "📤 <b>Uploading...</b>")

            title = info.get("title", "video")
            with open(output_path, "rb") as video_file:
                await context.bot.send_video(
                    chat_id=message.chat_id,
                    video=video_file,
                    caption=(
                        f"🎬 <b>{title}</b>\n"
                        f"📱 <i>via {extractor}</i>\n\n"
                        f"🛠 <a href='{SUPPORT_URL}'>Social Downloader Bot</a> | Dev: {BOT_DEV}"
                    ),
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=60,
                )

            record_download(user.id, url)

            remaining = (
                "Unlimited 👑"
                if is_vip(user.id)
                else str(max(0, MAX_DAILY_DOWNLOADS - get_downloads_last_24h(user.id)))
            )
            await set_status(
                status_msg, status_msg.chat_id,
                f"✅ <b>Done!</b> Remaining downloads today: <b>{remaining}</b>",
            )

    except Exception as exc:
        logger.exception("Unexpected error handling %s: %s", url, exc)
        try:
            await set_status(status_msg, status_msg.chat_id,
                             "⚠️ <b>Unexpected error.</b> Please try again later.")
        except Exception:
            pass

    finally:
        try:
            if output_path and os.path.exists(output_path):
                os.remove(output_path)
                logger.info("Removed temp file: %s", output_path)
        except Exception as exc:
            logger.warning("Failed to remove temp file %s: %s", output_path, exc)
        active_status_text.pop((status_msg.chat_id, status_msg.message_id), None)
        gc.collect()


# --------------------------------------------------------------------------- #
# Global error handler
# --------------------------------------------------------------------------- #

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Update %s caused error: %s", update, context.error)


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is not set. "
            "Set it in Railway's Variables tab before deploying."
        )

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",        start_command))
    app.add_handler(CommandHandler("help",         start_command))
    app.add_handler(CommandHandler("status",       status_command))
    app.add_handler(CommandHandler("giveaccess",   giveaccess_command))
    app.add_handler(CommandHandler("removeaccess", removeaccess_command))
    app.add_handler(CommandHandler("listusers",    listusers_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot starting (owner: SAHAT ANAS | dev: %s)...", BOT_DEV)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
