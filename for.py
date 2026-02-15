import os
import asyncio
import logging
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import RetryAfter, TimedOut, NetworkError, BadRequest, Forbidden

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("forwarder-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
SOURCE_CHAT_ID = os.environ.get("SOURCE_CHAT_ID", "").strip()
TARGET_CHAT_ID = os.environ.get("TARGET_CHAT_ID", "").strip()

PING_URL = os.environ.get("PING_URL", "https://forw-10tm.onrender.com").strip()
PING_EVERY_SECONDS = int(os.environ.get("PING_EVERY_SECONDS", "180"))

BASE_DELAY = float(os.environ.get("BASE_DELAY", "0.9"))
FAIL_DELAY = float(os.environ.get("FAIL_DELAY", "1.7"))


def _to_int_chat_id(v: str):
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return v  # could be @username for public chats


async def ping_loop():
    while True:
        try:
            r = requests.get(PING_URL, timeout=15)
            log.info("Ping %s -> %s", PING_URL, r.status_code)
        except Exception as e:
            log.warning("Ping failed: %s", e)
        await asyncio.sleep(PING_EVERY_SECONDS)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Forwarder bot ready ✅\n\n"
        "Commands:\n"
        "/chatid  - show this chat id\n"
        "/test    - test access to source/target\n"
        "/forward <start_id> <end_id>\n\n"
        "Example:\n"
        "/forward 120 135"
    )


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id if update.effective_chat else None
    await update.message.reply_text(f"chat_id = `{cid}`", parse_mode="Markdown")


async def test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    src = _to_int_chat_id(SOURCE_CHAT_ID)
    dst = _to_int_chat_id(TARGET_CHAT_ID)

    if src is None or dst is None:
        await update.message.reply_text(
            "❌ Missing SOURCE_CHAT_ID / TARGET_CHAT_ID env vars.\n"
            "Set them in Render → Environment and redeploy."
        )
        return

    msg = (
        f"Env looks set ✅\n"
        f"SOURCE_CHAT_ID = {src}\n"
        f"TARGET_CHAT_ID = {dst}\n\n"
        "Now testing access..."
    )
    await update.message.reply_text(msg)

    # Test 1: bot can send to dst
    try:
        await context.bot.send_message(chat_id=dst, text="✅ Test: I can send to TARGET_CHAT_ID")
    except Exception as e:
        await update.message.reply_text(f"❌ Cannot send to TARGET_CHAT_ID.\nError: {e}")
        return

    # Test 2: bot can access src (getChat)
    try:
        chat = await context.bot.get_chat(chat_id=src)
        await update.message.reply_text(f"✅ Can access SOURCE chat: {chat.title or chat.id}")
    except Exception as e:
        await update.message.reply_text(
            "❌ Cannot access SOURCE_CHAT_ID.\n"
            "Make sure:\n"
            "1) bot is added to that group/channel\n"
            "2) bot has permission (channel: must be admin)\n"
            f"\nError: {e}"
        )
        return

    await update.message.reply_text("✅ Test complete. You can try /forward now.")


async def forward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    # confirm we receive the command
    log.info("Received /forward from user=%s chat=%s args=%s",
             update.effective_user.id if update.effective_user else None,
             update.effective_chat.id if update.effective_chat else None,
             context.args)

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /forward <start_id> <end_id>")
        return

    src = _to_int_chat_id(SOURCE_CHAT_ID)
    dst = _to_int_chat_id(TARGET_CHAT_ID)

    if src is None or dst is None:
        await update.message.reply_text("❌ SOURCE_CHAT_ID / TARGET_CHAT_ID not set.")
        return

    try:
        start_id = int(context.args[0])
        end_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ start_id and end_id must be integers.")
        return

    if end_id < start_id:
        start_id, end_id = end_id, start_id

    await update.message.reply_text(
        f"Starting copy_message…\n"
        f"From: {src}\nTo: {dst}\n"
        f"Range: {start_id} → {end_id}\n"
        f"Delay: {BASE_DELAY}s"
    )

    ok, fail = 0, 0

    for mid in range(start_id, end_id + 1):
        try:
            await context.bot.copy_message(chat_id=dst, from_chat_id=src, message_id=mid)
            ok += 1
            await asyncio.sleep(BASE_DELAY)

        except RetryAfter as e:
            wait_time = int(getattr(e, "retry_after", 2)) + 1
            log.warning("Flood control mid=%s wait=%s", mid, wait_time)
            await asyncio.sleep(wait_time)
            # retry once
            try:
                await context.bot.copy_message(chat_id=dst, from_chat_id=src, message_id=mid)
                ok += 1
                await asyncio.sleep(BASE_DELAY)
            except Exception as e2:
                fail += 1
                log.warning("Retry failed mid=%s err=%s", mid, e2)
                await asyncio.sleep(FAIL_DELAY)

        except (Forbidden, BadRequest) as e:
            fail += 1
            # show user-readable reason for common cases
            text = str(e)
            if "chat not found" in text.lower():
                text += "\n\nFix: TARGET_CHAT_ID is wrong OR bot not in that target chat."
            if "not enough rights" in text.lower() or "administrator" in text.lower():
                text += "\n\nFix: for channels, bot must be ADMIN. For groups, allow posting."
            log.warning("Hard fail mid=%s: %s", mid, e)
            await asyncio.sleep(FAIL_DELAY)

        except (TimedOut, NetworkError) as e:
            fail += 1
            log.warning("Network fail mid=%s: %s", mid, e)
            await asyncio.sleep(FAIL_DELAY)

        except Exception as e:
            fail += 1
            log.warning("Other fail mid=%s: %s", mid, e)
            await asyncio.sleep(FAIL_DELAY)

    await update.message.reply_text(f"Done.\nSuccess: {ok}\nFailed: {fail}")


async def on_startup(app: Application):
    asyncio.create_task(ping_loop())
    log.info("Startup complete. Ping loop started.")


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN env var is required.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("chatid", chatid_cmd))
    app.add_handler(CommandHandler("test", test_cmd))
    app.add_handler(CommandHandler("forward", forward_cmd))

    app.post_init = on_startup

    log.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
