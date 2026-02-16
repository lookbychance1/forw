import express from "express";
import axios from "axios";
import TelegramBot from "node-telegram-bot-api";

const log = (...args) => console.log(new Date().toISOString(), ...args);

const BOT_TOKEN = (process.env.BOT_TOKEN || "").trim();

const PING_URL = (process.env.PING_URL || "https://forw-21p3.onrender.com").trim();
const PING_EVERY_SECONDS = parseInt(process.env.PING_EVERY_SECONDS || "180", 10);

const BASE_DELAY = parseFloat(process.env.BASE_DELAY || "0.9");
const FAIL_DELAY = parseFloat(process.env.FAIL_DELAY || "1.7");

const PORT = parseInt(process.env.PORT || "3000", 10);

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// Accept input like: "hime" or "@hime" or "-100123..."
function normalizeChatRef(input) {
  const raw = (input || "").trim();
  if (!raw) return null;

  // numeric chat id
  if (/^-?\d+$/.test(raw)) return Number(raw);

  // username
  const uname = raw.startsWith("@") ? raw.slice(1) : raw;
  if (!/^[A-Za-z0-9_]{5,}$/.test(uname)) return null; // Telegram usernames are typically >= 5 chars
  return "@" + uname;
}

async function resolveChatId(bot, ref) {
  // ref: Number (chat id) OR "@username"
  if (typeof ref === "number") return ref;

  // getChat("@username") works for public groups/channels with username
  const chat = await bot.getChat(ref);
  return chat.id;
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

if (!BOT_TOKEN) throw new Error("BOT_TOKEN env var is required.");

const bot = new TelegramBot(BOT_TOKEN, { polling: true });

// ----- Express (optional but useful on Render) -----
const app = express();
app.get("/", (req, res) => res.status(200).send("OK"));
app.get("/health", (req, res) => res.status(200).json({ ok: true }));
app.listen(PORT, () => log(`Express listening on :${PORT}`));

// start keep-alive
pingLoop().catch((e) => log("pingLoop crashed:", e));

// --------------------
// Conversation state
// --------------------
/**
 * sessions keyed by chatId
 * step: null | "SRC" | "DST"
 * startId/endId: number
 * srcRef/dstRef: "@username" | number
 * running: boolean
 */
const sessions = new Map();

function getSession(chatId) {
  if (!sessions.has(chatId)) sessions.set(chatId, { step: null, running: false });
  return sessions.get(chatId);
}

function resetSession(chatId) {
  sessions.set(chatId, { step: null, running: false });
}

// --------------------
// Commands
// --------------------
bot.onText(/^\/start(?:@\w+)?$/, async (msg) => {
  const text =
    "Forwarder bot ready ✅\n\n" +
    "Commands:\n" +
    "/chatid  - show this chat id\n" +
    "/test    - shows how username resolving works\n" +
    "/forward <start_id> <end_id>  (then bot asks source & target usernames)\n" +
    "/cancel  - cancel current forward setup/job\n\n" +
    "Example:\n" +
    "/forward 2 30\n\n" +
    "Then enter:\n" +
    "source username (without @)\n" +
    "target username (without @)\n\n" +
    "Note: This only works for PUBLIC groups/channels that have a @username.\n" +
    "Private groups without username cannot be found by username.";
  await bot.sendMessage(msg.chat.id, text);
});

bot.onText(/^\/cancel(?:@\w+)?$/, async (msg) => {
  const chatId = msg.chat.id;
  resetSession(chatId);
  await bot.sendMessage(chatId, "✅ Cancelled.");
});

bot.onText(/^\/chatid(?:@\w+)?$/, async (msg) => {
  await bot.sendMessage(msg.chat.id, `chat_id = \`${msg.chat.id}\``, { parse_mode: "Markdown" });
});

bot.onText(/^\/test(?:@\w+)?$/, async (msg) => {
  await bot.sendMessage(
    msg.chat.id,
    "✅ Username resolving test:\n\n" +
      "Send me a public group/channel username (without @), like:\n" +
      "`himeoncebot`\n\n" +
      "I will try to resolve it with getChat(@username).",
    { parse_mode: "Markdown" }
  );

  const s = getSession(msg.chat.id);
  if (s.running) return;
  s.step = "TEST_USERNAME";
});

// /forward start end -> ask for src username -> ask for dst username -> run forwarding
bot.onText(/^\/forward(?:@\w+)?\s+(-?\d+)\s+(-?\d+)\s*$/, async (msg, match) => {
  const chatId = msg.chat.id;
  const s = getSession(chatId);

  if (s.running) {
    await bot.sendMessage(chatId, "⚠️ A forward job is already running in this chat. Use /cancel to stop setup (job will finish current run).");
    return;
  }

  let startId = Number(match[1]);
  let endId = Number(match[2]);

  if (!Number.isInteger(startId) || !Number.isInteger(endId)) {
    await bot.sendMessage(chatId, "❌ start_id and end_id must be integers.");
    return;
  }
  if (endId < startId) [startId, endId] = [endId, startId];

  // store range, ask for source
  s.startId = startId;
  s.endId = endId;
  s.srcRef = null;
  s.dstRef = null;
  s.step = "SRC";

  await bot.sendMessage(
    chatId,
    `Range set: ${startId} → ${endId}\n\n` +
      "Now enter SOURCE group/channel username (WITHOUT @).\n" +
      "Example: for @hime, send: `hime`\n\n" +
      "Or you can send a numeric chat id like: `-1001234567890`",
    { parse_mode: "Markdown" }
  );
});

// --------------------
// Step handler (non-command messages)
// --------------------
bot.on("message", async (msg) => {
  const chatId = msg.chat.id;
  const text = (msg.text || "").trim();
  if (!text) return;

  // ignore commands here (handled by onText)
  if (text.startsWith("/")) return;

  const s = sessions.get(chatId);
  if (!s || !s.step) return;

  // TEST flow (optional)
  if (s.step === "TEST_USERNAME") {
    const ref = normalizeChatRef(text);
    if (!ref || typeof ref === "number") {
      await bot.sendMessage(chatId, "❌ Please send a username (like `himeoncebot`) not a number.", { parse_mode: "Markdown" });
      return;
    }
    try {
      const id = await resolveChatId(bot, ref);
      await bot.sendMessage(chatId, `✅ Resolved ${ref} to chat_id: \`${id}\``, { parse_mode: "Markdown" });
      resetSession(chatId);
    } catch (e) {
      await bot.sendMessage(chatId, `❌ Could not resolve ${ref}.\nError: ${e?.message || e}`);
      resetSession(chatId);
    }
    return;
  }

  // SOURCE username
  if (s.step === "SRC") {
    const ref = normalizeChatRef(text);
    if (!ref) {
      await bot.sendMessage(chatId, "❌ Invalid input. Send username without @ (letters/numbers/_), or numeric chat id.");
      return;
    }
    s.srcRef = ref;
    s.step = "DST";

    await bot.sendMessage(
      chatId,
      "✅ Source received.\n\nNow enter TARGET group/channel username (WITHOUT @).\n" +
        "Example: for @targetgroup, send: `targetgroup`\n\n" +
        "Or numeric chat id like: `-1001234567890`",
      { parse_mode: "Markdown" }
    );
    return;
  }

  // TARGET username -> resolve both -> forward
  if (s.step === "DST") {
    const ref = normalizeChatRef(text);
    if (!ref) {
      await bot.sendMessage(chatId, "❌ Invalid input. Send username without @ (letters/numbers/_), or numeric chat id.");
      return;
    }
    s.dstRef = ref;
    s.step = null;

    // resolve chat IDs
    let srcId, dstId;
    try {
      srcId = await resolveChatId(bot, s.srcRef);
    } catch (e) {
      resetSession(chatId);
      await bot.sendMessage(
        chatId,
        `❌ Could not resolve SOURCE (${String(s.srcRef)}).\n\n` +
          "Make sure:\n" +
          "• It is a PUBLIC group/channel with a username\n" +
          "• The bot is added there (and admin for channels)\n\n" +
          `Error: ${e?.message || e}`
      );
      return;
    }

    try {
      dstId = await resolveChatId(bot, s.dstRef);
    } catch (e) {
      resetSession(chatId);
      await bot.sendMessage(
        chatId,
        `❌ Could not resolve TARGET (${String(s.dstRef)}).\n\n` +
          "Make sure:\n" +
          "• It is a PUBLIC group/channel with a username\n" +
          "• The bot is added there (and admin for channels)\n\n" +
          `Error: ${e?.message || e}`
      );
      return;
    }

    // mark running
    s.running = true;

    await bot.sendMessage(
      chatId,
      `Starting copyMessage…\nFrom: ${srcId}\nTo: ${dstId}\nRange: ${s.startId} → ${s.endId}\nDelay: ${BASE_DELAY}s`
    );

    let ok = 0;
    let fail = 0;

    for (let mid = s.startId; mid <= s.endId; mid++) {
      try {
        await bot.copyMessage(dstId, srcId, mid);
        ok++;
        await sleep(BASE_DELAY * 1000);
      } catch (e) {
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
            await bot.copyMessage(dstId, srcId, mid);
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

        fail++;
        log("Fail mid=", mid, "err=", e?.message || e);
        await sleep(FAIL_DELAY * 1000);
      }
    }

    await bot.sendMessage(chatId, `Done.\nSuccess: ${ok}\nFailed: ${fail}`);
    resetSession(chatId);
  }
});

log("Bot starting... (polling enabled)");
