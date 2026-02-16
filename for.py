import express from "express";
import axios from "axios";
import TelegramBot from "node-telegram-bot-api";

const log = (...args) => console.log(new Date().toISOString(), ...args);

const BOT_TOKEN = (process.env.BOT_TOKEN || "").trim();
const SOURCE_CHAT_ID = (process.env.SOURCE_CHAT_ID || "").trim();
const TARGET_CHAT_ID = (process.env.TARGET_CHAT_ID || "").trim();

const PING_URL = (process.env.PING_URL || "https://forw-10tm.onrender.com").trim();
const PING_EVERY_SECONDS = parseInt(process.env.PING_EVERY_SECONDS || "180", 10);

const BASE_DELAY = parseFloat(process.env.BASE_DELAY || "0.9");
const FAIL_DELAY = parseFloat(process.env.FAIL_DELAY || "1.7");

const PORT = parseInt(process.env.PORT || "3000", 10);

function toChatId(v) {
  if (!v) return null;
  // allow -100xxxx, group ids, etc
  if (/^-?\d+$/.test(v)) return Number(v);
  return v; // @username
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function pingLoop() {
  while (true) {
    try {
      const r = await axios.get(PING_URL, { timeout: 15000 });
      log("Ping", PING_URL, "->", r.status);
    } catch (e) {
      log("Ping failed:", e?.message || e);
    }
    await sleep(PING_EVERY_SECONDS * 1000);
  }
}

if (!BOT_TOKEN) {
  throw new Error("BOT_TOKEN env var is required.");
}

const bot = new TelegramBot(BOT_TOKEN, { polling: true });

// ----- Express (optional but useful on Render) -----
const app = express();

app.get("/", (req, res) => res.status(200).send("OK"));
app.get("/health", (req, res) => res.status(200).json({ ok: true }));

app.listen(PORT, () => log(`Express listening on :${PORT}`));

// start keep-alive
pingLoop().catch((e) => log("pingLoop crashed:", e));

// ----- Commands -----
bot.onText(/^\/start(?:@\w+)?$/, async (msg) => {
  const text =
    "Forwarder bot ready ✅\n\n" +
    "Commands:\n" +
    "/chatid  - show this chat id\n" +
    "/test    - test access to source/target\n" +
    "/forward <start_id> <end_id>\n\n" +
    "Example:\n" +
    "/forward 120 135";

  await bot.sendMessage(msg.chat.id, text);
});

bot.onText(/^\/chatid(?:@\w+)?$/, async (msg) => {
  const cid = msg.chat?.id;
  await bot.sendMessage(msg.chat.id, `chat_id = \`${cid}\``, { parse_mode: "Markdown" });
});

bot.onText(/^\/test(?:@\w+)?$/, async (msg) => {
  const src = toChatId(SOURCE_CHAT_ID);
  const dst = toChatId(TARGET_CHAT_ID);

  if (src == null || dst == null) {
    await bot.sendMessage(
      msg.chat.id,
      "❌ Missing SOURCE_CHAT_ID / TARGET_CHAT_ID env vars.\nSet them in Render → Environment and redeploy."
    );
    return;
  }

  await bot.sendMessage(
    msg.chat.id,
    `Env looks set ✅\nSOURCE_CHAT_ID = ${src}\nTARGET_CHAT_ID = ${dst}\n\nNow testing access...`
  );

  // Test 1: send to dst
  try {
    await bot.sendMessage(dst, "✅ Test: I can send to TARGET_CHAT_ID");
  } catch (e) {
    await bot.sendMessage(msg.chat.id, `❌ Cannot send to TARGET_CHAT_ID.\nError: ${e?.message || e}`);
    return;
  }

  // Test 2: access src (getChat)
  try {
    const chat = await bot.getChat(src);
    await bot.sendMessage(msg.chat.id, `✅ Can access SOURCE chat: ${chat.title || chat.id}`);
  } catch (e) {
    await bot.sendMessage(
      msg.chat.id,
      "❌ Cannot access SOURCE_CHAT_ID.\n" +
        "Make sure:\n" +
        "1) bot is added to that group/channel\n" +
        "2) channel: bot must be admin\n\n" +
        `Error: ${e?.message || e}`
    );
    return;
  }

  await bot.sendMessage(msg.chat.id, "✅ Test complete. You can try /forward now.");
});

// /forward start end
bot.onText(/^\/forward(?:@\w+)?(?:\s+(-?\d+)\s+(-?\d+))?$/, async (msg, match) => {
  const chatId = msg.chat.id;
  const src = toChatId(SOURCE_CHAT_ID);
  const dst = toChatId(TARGET_CHAT_ID);

  log("Received /forward from", msg.from?.id, "chat", chatId, "match", match?.slice(1));

  if (!match || match[1] == null || match[2] == null) {
    await bot.sendMessage(chatId, "Usage: /forward <start_id> <end_id>");
    return;
  }

  if (src == null || dst == null) {
    await bot.sendMessage(chatId, "❌ SOURCE_CHAT_ID / TARGET_CHAT_ID not set.");
    return;
  }

  let startId = Number(match[1]);
  let endId = Number(match[2]);
  if (!Number.isInteger(startId) || !Number.isInteger(endId)) {
    await bot.sendMessage(chatId, "❌ start_id and end_id must be integers.");
    return;
  }

  if (endId < startId) [startId, endId] = [endId, startId];

  await bot.sendMessage(
    chatId,
    `Starting copyMessage…\nFrom: ${src}\nTo: ${dst}\nRange: ${startId} → ${endId}\nDelay: ${BASE_DELAY}s`
  );

  let ok = 0;
  let fail = 0;

  for (let mid = startId; mid <= endId; mid++) {
    try {
      // node-telegram-bot-api supports copyMessage(chatId, fromChatId, messageId, options)
      await bot.copyMessage(dst, src, mid);
      ok++;
      await sleep(BASE_DELAY * 1000);
    } catch (e) {
      // Flood control (429)
      const status = e?.response?.statusCode || e?.response?.status;
      const retryAfter =
        e?.response?.body?.parameters?.retry_after ??
        e?.response?.data?.parameters?.retry_after ??
        null;

      if (status === 429 && retryAfter != null) {
        const waitSec = Number(retryAfter) + 1;
        log("Flood control mid=", mid, "wait=", waitSec);
        await sleep(waitSec * 1000);

        // retry once
        try {
          await bot.copyMessage(dst, src, mid);
          ok++;
          await sleep(BASE_DELAY * 1000);
          continue;
        } catch (e2) {
          fail++;
          log("Retry failed mid=", mid, "err=", e2?.message || e2);
          await sleep(FAIL_DELAY * 1000);
          continue;
        }
      }

      // Common hard fails / network issues
      const text = (e?.response?.body?.description || e?.message || String(e)).toLowerCase();
      let userHint = "";

      if (text.includes("chat not found")) {
        userHint = "\n\nFix: TARGET_CHAT_ID is wrong OR bot not in that target chat.";
      } else if (text.includes("not enough rights") || text.includes("administrator")) {
        userHint = "\n\nFix: for channels, bot must be ADMIN. For groups, allow posting.";
      }

      fail++;
      log("Fail mid=", mid, "err=", e?.message || e);
      await sleep(FAIL_DELAY * 1000);
    }
  }

  await bot.sendMessage(chatId, `Done.\nSuccess: ${ok}\nFailed: ${fail}`);
});

log("Bot starting... (polling enabled)");
