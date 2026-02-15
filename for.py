import os
import asyncio
import logging
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import RetryAfter, TimedOut, NetworkError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("forwarder-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# Put numeric chat IDs here (recommended), e.g. -1001234567890
SOURCE_CHAT_ID = os.environ.get("SOURCE_CHAT_ID", "").strip()
TARGET_CHAT_ID = os.environ.get("TARGET_CHAT_ID", "").strip()

PING_URL = "https://forw-10tm.onrender.com"
PING_EVERY_SECONDS = 180  # 3 minutes

# Rate-limit tuning
BASE_DELAY = float(os.environ.get("BASE_DELAY", "0.8"))   # normal delay after success
FAIL_DELAY = float(os.environ.get("FAIL_DELAY", "1.5"))   # delay after generic failure


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
        "/forward 120 135\n\n"
        "Tip: set BASE_DELAY / FAIL_DELAY env vars if needed."
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
        f"From: {src}\nTo: {dst}\n"
        f"Delays: BASE_DELAY={BASE_DELAY}s, FAIL_DELAY={FAIL_DELAY}s"
    )

    ok = 0
    fail = 0

    for mid in range(start_id, end_id + 1):
        try:
            await context.bot.copy_message(
                chat_id=dst,
                from_chat_id=src,
                message_id=mid
            )
            ok += 1
            await asyncio.sleep(BASE_DELAY)

        except RetryAfter as e:
            # Telegram tells you exactly how long to wait.
            wait_time = int(getattr(e, "retry_after", 1)) + 1
            log.warning("Flood control hit at mid=%s. Sleeping for %s seconds", mid, wait_time)
            await asyncio.sleep(wait_time)

            # Retry once after waiting
            try:
                await context.bot.copy_message(
                    chat_id=dst,
                    from_chat_id=src,
                    message_id=mid
                )
                ok += 1
                await asyncio.sleep(BASE_DELAY)
            except Exception as e2:
                fail += 1
                log.warning("Retry failed mid=%s: %s", mid, e2)
                await asyncio.sleep(FAIL_DELAY)

        except (TimedOut, NetworkError) as e:
            fail += 1
            log.warning("Network issue mid=%s: %s", mid, e)
            await asyncio.sleep(FAIL_DELAY)

        except Exception as e:
            fail += 1
            log.warning("Failed mid=%s: %s", mid, e)
            await asyncio.sleep(FAIL_DELAY)

    await update.message.reply_text(f"Done.\nSuccess: {ok}\nFailed: {fail}")


async def on_startup(app: Application):
    asyncio.create_task(ping_loop(app))
    log.info("Startup complete. Ping loop started.")


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN env var is required.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("forward", forward_cmd))

    # Runs once after initialization
    app.post_init = on_startup

    log.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
