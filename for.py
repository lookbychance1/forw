import os
import asyncio
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("forwarder-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# Put numeric chat IDs here (recommended), e.g. -1001234567890
SOURCE_CHAT_ID = os.environ.get("SOURCE_CHAT_ID", "").strip()
TARGET_CHAT_ID = os.environ.get("TARGET_CHAT_ID", "").strip()

PING_URL = "https://forw-10tm.onrender.com"
PING_EVERY_SECONDS = 180  # 3 minutes


def _to_int_chat_id(v: str):
    """
    Convert env var to int if possible, otherwise keep as str.
    Telegram bot API accepts either int chat_id or @username (for public).
    """
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return v


async def ping_loop(app: Application):
    """Background loop that pings your URL every 3 minutes."""
    while True:
        try:
            r = requests.get(PING_URL, timeout=15)
            log.info("Ping %s -> %s", PING_URL, r.status_code)
        except Exception as e:
            log.warning("Ping failed: %s", e)
        await asyncio.sleep(PING_EVERY_SECONDS)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Forwarder bot ready.\n\n"
        "Use:\n"
        "/forward <start_id> <end_id>\n\n"
        "Example:\n"
        "/forward 120 135"
    )


async def forward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /forward <start_id> <end_id>")
        return

    src = _to_int_chat_id(SOURCE_CHAT_ID)
    dst = _to_int_chat_id(TARGET_CHAT_ID)

    if src is None or dst is None:
        await update.message.reply_text(
            "Missing SOURCE_CHAT_ID / TARGET_CHAT_ID env vars. Set them and restart."
        )
        return

    try:
        start_id = int(context.args[0])
        end_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("start_id and end_id must be integers.")
        return

    if start_id <= 0 or end_id <= 0:
        await update.message.reply_text("Message IDs must be positive integers.")
        return

    if end_id < start_id:
        start_id, end_id = end_id, start_id

    await update.message.reply_text(
        f"Forwarding messages {start_id} to {end_id}...\n"
        f"From: {src}\nTo: {dst}"
    )

    ok = 0
    fail = 0

    # Telegram may rate-limit; we add a small delay per message.
    for mid in range(start_id, end_id + 1):
        try:
            # Forward message as-is
            await context.bot.copy_message(
                chat_id=dst,
                from_chat_id=src,
                message_id=mid
            )
            ok += 1
            await asyncio.sleep(0.35)
        except Exception as e:
            fail += 1
            log.warning("Failed mid=%s: %s", mid, e)
            await asyncio.sleep(0.6)

    await update.message.reply_text(f"Done.\nSuccess: {ok}\nFailed: {fail}")


async def on_startup(app: Application):
    # Start ping loop
    asyncio.create_task(ping_loop(app))
    log.info("Startup complete. Ping loop started.")


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN env var is required.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("forward", forward_cmd))

    app.post_init = on_startup  # runs once after initialization

    log.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
