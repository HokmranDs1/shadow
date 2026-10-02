

import os
import sys
import sqlite3
import time
import asyncio
import shutil
import logging
from telebot import TeleBot, types
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, ChatWriteForbiddenError,
    UserBannedInChannelError, ChannelPrivateError, UsernameNotOccupiedError,
)

# =============== CONFIG ===============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8736321086:AAFdCJOaxq4yXCtabL89MvRC9nin8BEe2zY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7722835349"))
WALLET_INFO = os.environ.get("WALLET_INFO", "💳 شماره کارت: 6037991217102775")
PRICE_PER_MESSAGE = int(os.environ.get("PRICE_PER_MESSAGE", "50000"))
SESSIONS_DIR = os.environ.get("SESSIONS_DIR", "sessions")
DB_PATH = os.environ.get("DB_PATH", "ads_bot.db")
SEND_DELAY = int(os.environ.get("SEND_DELAY", "3"))
PORT = int(os.environ.get("PORT", "10000"))
# ========================================

os.makedirs(SESSIONS_DIR, exist_ok=True)

# لاگ برای Render
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")

# -------- دیتابیس --------
def db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    con = db(); cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY, username TEXT, joined_at INTEGER
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS groups(
        chat_id INTEGER PRIMARY KEY, title TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS orders(
        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, username TEXT, ad_text TEXT,
        status TEXT DEFAULT 'pending', created_at INTEGER
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, phone TEXT, session_file TEXT,
        api_id INTEGER, api_hash TEXT, is_active INTEGER DEFAULT 1,
        UNIQUE(user_id, phone)
    )""")
    con.commit(); con.close()

def add_user(uid, uname):
    con = db(); cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO users(user_id, username, joined_at) VALUES (?,?,?)",
                (uid, uname, int(time.time())))
    con.commit(); con.close()

def add_group(chat_id, title):
    con = db(); cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO groups(chat_id, title) VALUES (?,?)", (chat_id, title))
    con.commit(); con.close()

def remove_group(chat_id):
    con = db(); cur = con.cursor()
    cur.execute("DELETE FROM groups WHERE chat_id=?", (chat_id,))
    con.commit(); con.close()

def get_groups():
    con = db(); cur = con.cursor()
    cur.execute("SELECT chat_id, title FROM groups")
    rows = cur.fetchall(); con.close(); return rows

def create_order(uid, uname, ad_text):
    con = db(); cur = con.cursor()
    cur.execute("INSERT INTO orders(user_id, username, ad_text, created_at) VALUES (?,?,?,?)",
                (uid, uname, ad_text, int(time.time())))
    con.commit(); oid = cur.lastrowid; con.close(); return oid

def get_order(oid):
    con = db(); cur = con.cursor()
    cur.execute("SELECT order_id, user_id, username, ad_text, status FROM orders WHERE order_id=?", (oid,))
    row = cur.fetchone(); con.close(); return row

def set_order_status(oid, status):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE orders SET status=? WHERE order_id=?", (status, oid))
    con.commit(); con.close()

def add_session_db(uid, phone, session_file, api_id, api_hash):
    con = db(); cur = con.cursor()
    cur.execute("""INSERT OR REPLACE INTO sessions
        (user_id, phone, session_file, api_id, api_hash, is_active)
        VALUES (?,?,?,?,?,1)""", (uid, phone, session_file, api_id, api_hash))
    con.commit(); con.close()

def get_user_sessions(uid, active_only=True):
    con = db(); cur = con.cursor()
    if active_only:
        cur.execute("""SELECT id, phone, session_file, api_id, api_hash, is_active
                       FROM sessions WHERE user_id=? AND is_active=1""", (uid,))
    else:
        cur.execute("""SELECT id, phone, session_file, api_id, api_hash, is_active
                       FROM sessions WHERE user_id=?""", (uid,))
    rows = cur.fetchall(); con.close(); return rows

def delete_session_db(uid, phone):
    con = db(); cur = con.cursor()
    cur.execute("SELECT session_file FROM sessions WHERE user_id=? AND phone=?", (uid, phone))
    row = cur.fetchone()
    if row:
        path = os.path.join(SESSIONS_DIR, row[0])
        if os.path.exists(path):
            try: os.remove(path)
            except: pass
    cur.execute("DELETE FROM sessions WHERE user_id=? AND phone=?", (uid, phone))
    con.commit(); con.close()

init_db()

# =============== Telethon Engine ===============
async def send_bulk_async(clients, text, delay):
    sent, failed = 0, 0
    for client, phone in clients:
        try:
            if not client.is_connected():
                await client.connect()
            dialogs = await client.get_dialogs()
            for d in dialogs:
                if d.is_group or d.is_channel:
                    try:
                        await client.send_message(d.id, text)
                        sent += 1
                        await asyncio.sleep(delay)
                    except FloodWaitError as e:
                        log.warning(f"FloodWait {phone}: {e.seconds}s")
                        await asyncio.sleep(e.seconds + 1)
                        await client.send_message(d.id, text)
                        sent += 1
                    except (ChatWriteForbiddenError, UserBannedInChannelError,
                            ChannelPrivateError, UsernameNotOccupiedError):
                        failed += 1
                    except Exception as e:
                        log.error(f"خطا در ارسال {phone}/{d.id}: {e}")
                        failed += 1
        except Exception as e:
            log.error(f"خطا در کلاینت {phone}: {e}")
            failed += 1
        finally:
            try: await client.disconnect()
            except: pass
    return sent, failed

def broadcast_via_telethon(uid, text):
    rows = get_user_sessions(uid, active_only=True)
    if not rows:
        log.info("هیچ سشن فعالی نیست")
        return 0, 0
    clients = []
    for sid, phone, sfile, aid, ahash, _ in rows:
        path = os.path.join(SESSIONS_DIR, sfile)
        if not os.path.exists(path):
            log.warning(f"فایل سشن یافت نشد: {path}")
            continue
        try:
            if sfile.endswith(".txt"):
                session_str = open(path, "r", encoding="utf-8").read().strip()
                client = TelegramClient(StringSession(session_str), int(aid), ahash)
            else:
                client = TelegramClient(path, int(aid), ahash)
            clients.append((client, phone))
        except Exception as e:
            log.error(f"خطا در بارگذاری {phone}: {e}")
    if not clients: return 0, 0
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(send_bulk_async(clients, text, SEND_DELAY))
    finally:
        loop.close()
    return result

# =============== Login Flow ===============
login_states = {}

def is_logging_in(chat_id):
    return chat_id in login_states

@bot.message_handler(commands=["login"])
def cmd_login(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ فقط ادمین.")
        return
    chat_id = message.chat.id
    login_states[chat_id] = {"step": "waiting_file"}
    bot.send_message(chat_id,
        "🔐 **اتصال اکانت**\n\n"
        "۱. فایل سشن بفرست (`.session` یا `.txt`)\n"
        "۲. API ID\n"
        "۳. API Hash\n\n"
        "❌ برای لغو /cancel بزن.",
        parse_mode="Markdown")

@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    chat_id = message.chat.id
    if chat_id in login_states:
        temp = login_states[chat_id].get("temp_file")
        if temp and os.path.exists(temp):
            try: os.remove(temp)
            except: pass
        del login_states[chat_id]
        bot.send_message(chat_id, "🚫 عملیات لغو شد.")
    else:
        bot.send_message(chat_id, "عملیات فعالی برای لغو نیست.")

@bot.message_handler(content_types=["document"])
def handle_document(message):
    chat_id = message.chat.id
    if not is_logging_in(chat_id) or login_states[chat_id].get("step") != "waiting_file":
        return
    if message.from_user.id != ADMIN_ID:
        return

    file_name = message.document.file_name or "session.session"
    ext = os.path.splitext(file_name)[1].lower()

    if ext not in (".session", ".txt"):
        bot.reply_to(message, "❌ فقط فایل `.session` یا `.txt` بفرست.")
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
    except Exception as e:
        bot.reply_to(message, f"❌ خطا در دانلود: {e}")
        return

    tmp_name = f"temp_{chat_id}_{int(time.time())}{ext}"
    src = os.path.join(SESSIONS_DIR, tmp_name)

    if ext == ".session":
        with open(src, "wb") as f:
            f.write(downloaded)
    else:
        try:
            content = downloaded.decode("utf-8")
        except UnicodeDecodeError:
            bot.reply_to(message, "❌ فایل متنی UTF-8 معتبر نیست.")
            return
        with open(src, "w", encoding="utf-8") as f:
            f.write(content)
        if len(content.strip()) < 50:
            if os.path.exists(src): os.remove(src)
            bot.reply_to(message, "❌ محتوای `.txt` معتبر نیست.")
            return

    login_states[chat_id]["temp_file"] = src
    login_states[chat_id]["ext"] = ext
    login_states[chat_id]["step"] = "waiting_api_id"
    bot.send_message(chat_id, f"✅ فایل `{ext}` دریافت شد.\n\n🔢 **API ID** رو بفرست:")

@bot.message_handler(func=lambda m: m.content_type == "text" and is_logging_in(m.chat.id)
                                  and login_states[m.chat.id].get("step") in ("waiting_api_id", "waiting_api_hash"))
def handle_creds(message):
    chat_id = message.chat.id
    state = login_states[chat_id]
    step = state.get("step")

    if step == "waiting_api_id":
        text = (message.text or "").strip()
        if not text.isdigit():
            bot.reply_to(message, "❌ API ID باید عدد باشه:")
            return
        state["api_id"] = int(text)
        state["step"] = "waiting_api_hash"
        bot.send_message(chat_id, "✅ ذخیره شد.\n\n🔑 **API Hash** رو بفرست:")

    elif step == "waiting_api_hash":
        text = (message.text or "").strip()
        if len(text) != 32:
            bot.reply_to(message, "❌ API Hash باید ۳۲ کاراکتر باشه:")
            return
        state["api_hash"] = text
        finalize_login(chat_id, message)

def finalize_login(chat_id, message):
    state = login_states[chat_id]
    ext = state["ext"]
    api_id = state["api_id"]
    api_hash = state["api_hash"]
    src = state["temp_file"]

    try:
        if ext == ".txt":
            session_str = open(src, "r", encoding="utf-8").read().strip()
            client = TelegramClient(StringSession(session_str), api_id, api_hash)
        else:
            client = TelegramClient(src, api_id, api_hash)

        client.connect()
        if not client.is_user_authorized():
            client.disconnect()
            if os.path.exists(src): os.remove(src)
            del login_states[chat_id]
            bot.reply_to(message, "❌ سشن معتبر نیست یا منقضی شده.")
            return
        me = client.get_me()
        phone = me.phone or f"id_{me.id}"
        client.disconnect()

        safe_phone = phone.replace("+", "").replace(" ", "")
        final_file = f"sess_{chat_id}_{safe_phone}{ext}"
        final_path = os.path.join(SESSIONS_DIR, final_file)
        if os.path.exists(final_path):
            os.remove(final_path)
        shutil.move(src, final_path)

        add_session_db(message.from_user.id, phone, final_file, api_id, api_hash)
        del login_states[chat_id]
        bot.send_message(message.chat.id,
            f"✅ **اکانت متصل شد!**\n\n"
            f"📱 `{phone}`\n"
            f"👤 {me.first_name or '—'}\n"
            f"🆔 `{me.id}`\n"
            f"📁 `{ext}`",
            parse_mode="Markdown")
    except Exception as e:
        if os.path.exists(src):
            try: os.remove(src)
            except: pass
        if chat_id in login_states: del login_states[chat_id]
        bot.reply_to(message, f"❌ خطا: {e}")

# =============== مدیریت اکانت‌ها ===============
@bot.message_handler(commands=["sessions"])
def cmd_sessions(message):
    rows = get_user_sessions(message.from_user.id, active_only=False)
    if not rows:
        bot.reply_to(message, "هیچ اکانتی وصل نیست. /login بزن.")
        return
    txt = "📱 **اکانت‌های متصل:**\n\n"
    kb = types.InlineKeyboardMarkup()
    for sid, phone, sfile, aid, ahash, is_active in rows:
        status = "✅" if is_active else "❌"
        txt += f"{status} `{phone}`\n"
        kb.add(types.InlineKeyboardButton(f"🗑 {phone}", callback_data=f"del_{sid}"))
    bot.send_message(message.chat.id, txt, parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("del_"))
def delete_session_cb(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔️ فقط ادمین.")
        return
    sid = int(call.data.split("_")[1])
    con = db(); cur = con.cursor()
    cur.execute("SELECT user_id, phone FROM sessions WHERE id=?", (sid,))
    row = cur.fetchone(); con.close()
    if not row:
        bot.answer_callback_query(call.id, "یافت نشد.")
        return
    uid, phone = row
    delete_session_db(uid, phone)
    bot.answer_callback_query(call.id, f"حذف شد: {phone}")
    try:
        bot.edit_message_text(f"🗑 اکانت `{phone}` حذف شد.",
                              call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except: pass

# =============== دستورات اصلی ===============
@bot.message_handler(commands=["start"])
def cmd_start(message):
    add_user(message.from_user.id, message.from_user.username or "")
    bot.send_message(message.chat.id,
        "👋 به ربات تبلیغات خوش اومدی!\n\n"
        "📣 /buy - ثبت سفارش تبلیغ\n"
        "🔐 /login - اتصال اکانت\n"
        "📱 /sessions - لیست اکانت‌ها\n"
        "🆘 /support - پشتیبانی")

@bot.my_chat_member_handler()
def on_bot_status_change(update: types.ChatMemberUpdated):
    chat = update.chat
    new_status = update.new_chat_member.status
    if chat.type in ("group", "supergroup"):
        if new_status in ("member", "administrator"):
            add_group(chat.id, chat.title or str(chat.id))
        elif new_status in ("left", "kicked"):
            remove_group(chat.id)

@bot.message_handler(commands=["buy"])
def cmd_buy(message):
    msg = bot.send_message(message.chat.id,
        f"📝 متن تبلیغ رو بفرست.\nهزینه هر پیام: {PRICE_PER_MESSAGE} تومان")
    bot.register_next_step_handler(msg, process_ad_text)

def process_ad_text(message):
    uid = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or ""
    order_id = create_order(uid, username, message.text)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ پرداخت کردم", callback_data=f"paid_{order_id}"))
    bot.send_message(message.chat.id,
        f"✅ سفارش #{order_id} ثبت شد.\n\n{WALLET_INFO}\n\nبعد از واریز دکمه رو بزن.",
        reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("paid_"))
def on_user_paid(call):
    order_id = int(call.data.split("_")[1])
    order = get_order(order_id)
    if not order:
        bot.answer_callback_query(call.id, "سفارش پیدا نشد.")
        return
    _, user_id, username, ad_text, status = order
    bot.answer_callback_query(call.id, "به ادمین اطلاع داده شد ⏳")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ تایید", callback_data=f"approve_{order_id}"),
        types.InlineKeyboardButton("❌ رد", callback_data=f"reject_{order_id}")
    )
    bot.send_message(ADMIN_ID, f"📥 سفارش #{order_id}\n@{username} (ID: {user_id})\n\n{ad_text}", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith(("approve_", "reject_")))
def on_admin_decision(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔️ فقط ادمین.")
        return
    action, oid = call.data.split("_"); oid = int(oid)
    order = get_order(oid)
    if not order:
        bot.answer_callback_query(call.id, "سفارش نیست.")
        return
    _, user_id, username, ad_text, status = order
    if action == "approve":
        set_order_status(oid, "approved")
        sent, failed = broadcast_via_telethon(ADMIN_ID, ad_text)
        bot.edit_message_text(f"✅ سفارش #{oid} تایید شد.\n📤 ارسال: {sent} موفق / {failed} ناموفق",
                              call.message.chat.id, call.message.message_id)
        bot.send_message(user_id, f"🎉 تبلیغ شما در {sent} چت ارسال شد!")
    else:
        set_order_status(oid, "rejected")
        bot.edit_message_text(f"❌ سفارش #{oid} رد شد.", call.message.chat.id, call.message.message_id)
        bot.send_message(user_id, "❌ پرداخت تایید نشد. /support")
    bot.answer_callback_query(call.id, "ثبت شد.")

@bot.message_handler(commands=["support"])
def cmd_support(message):
    msg = bot.send_message(message.chat.id,
        f"🆘 پشتیبانی: <code>{ADMIN_ID}</code>\n\nپیامت رو بنویس:")
    bot.register_next_step_handler(msg, forward_support_message)

def forward_support_message(message):
    bot.send_message(ADMIN_ID,
        f"🆘 از @{message.from_user.username or message.from_user.first_name} "
        f"(ID: {message.from_user.id}):\n\n{message.text}")
    bot.send_message(message.chat.id, "✅ پیامت ارسال شد.")

# =============== HTTP Server (برای Render) ===============
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        # لاگ‌های HTTP رو خفه کن چون زیادن
        pass

def start_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info(f"🌐 HTTP server on port {PORT}")
    server.serve_forever()

# =============== Run ===============
if __name__ == "__main__":
    log.info("🚀 ربات در حال راه‌اندازی...")
    # HTTP server رو توی thread جدا بالا بیار
    http_thread = Thread(target=start_http_server, daemon=True)
    http_thread.start()
    log.info("✅ ربات فعال شد")
    try:
        bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
    except Exception as e:
        log.error(f"خطا: {e}")
        time.sleep(5)
        bot.infinity_polling(skip_pending=True)
