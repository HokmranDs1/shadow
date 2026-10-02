#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ربات تبلیغاتی - Sync Mode (بدون async)
# pip install pyTelegramBotAPI telethon cryptg

import os, time, shutil
import sqlite3
from telebot import TeleBot, types
from telethon.sync import TelegramClient
from telethon.errors import (
    FloodWaitError, ChatWriteForbiddenError,
    UserBannedInChannelError, ChannelPrivateError,
    UsernameNotOccupiedError
)

# =============== CONFIG ===============
BOT_TOKEN = "8736321086:AAFdCJOaxq4yXCtabL89MvRC9nin8BEe2zY"
ADMIN_ID = 7722835349
WALLET_INFO = "💳 شماره کارت: <code>6037991217102775</code>\nبنام: علیرضا منظم"
PRICE_PER_MESSAGE = 50000
SESSIONS_DIR = "sessions"
DB_PATH = "ads_bot.db"
SEND_DELAY = 3
DEFAULT_API_ID = 25819499
DEFAULT_API_HASH = "5d03a36fef9939800be13b7f843e0b12"
# ========================================

os.makedirs(SESSIONS_DIR, exist_ok=True)
bot = TeleBot(BOT_TOKEN, parse_mode="HTML")


# =============== DATABASE ===============
def db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    con = db()
    cur = con.cursor()
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                joined_at INTEGER
            )
        """)
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='groups'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE groups(
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )
        """)
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE orders(
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                ad_text TEXT,
                status TEXT DEFAULT 'pending',
                created_at INTEGER
            )
        """)
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                phone TEXT,
                session_file TEXT,
                api_id INTEGER,
                api_hash TEXT,
                is_active INTEGER DEFAULT 1,
                UNIQUE(user_id, phone)
            )
        """)
    else:
        cur.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in cur.fetchall()}
        
        if 'session_file' not in columns:
            cur.execute("ALTER TABLE sessions ADD COLUMN session_file TEXT")
        if 'api_id' not in columns:
            cur.execute("ALTER TABLE sessions ADD COLUMN api_id INTEGER")
        if 'api_hash' not in columns:
            cur.execute("ALTER TABLE sessions ADD COLUMN api_hash TEXT")
    
    con.commit()
    con.close()


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


def add_session_file(uid, phone, session_file, api_id, api_hash):
    con = db(); cur = con.cursor()
    cur.execute("""INSERT OR REPLACE INTO sessions
        (user_id, phone, session_file, api_id, api_hash, is_active)
        VALUES (?,?,?,?,?,1)""",
        (uid, phone, session_file, int(api_id), api_hash))
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


def delete_session_file(sid):
    con = db(); cur = con.cursor()
    cur.execute("SELECT phone, session_file FROM sessions WHERE id=?", (sid,))
    row = cur.fetchone()
    if row:
        phone, sess_file = row
        cur.execute("DELETE FROM sessions WHERE id=?", (sid,))
        con.commit(); con.close()
        if sess_file and os.path.exists(sess_file):
            try:
                os.remove(sess_file)
            except Exception:
                pass
        return phone
    con.close()
    return None


init_db()


# =============== Telethon Broadcast (SYNC) ===============
def broadcast_via_telethon(uid, text, message_chat_id, message_id):
    """ارسال تبلیغ به تمام گروه‌ها و کانال‌ها (Sync) + بروزرسانی لحظه‌ای"""
    rows = get_user_sessions(uid, active_only=True)
    if not rows:
        return 0, 0
    
    sent = 0
    total_dialogs = 0
    
    for sid, phone, sess_file, aid, ahash, _ in rows:
        if not os.path.exists(sess_file):
            print(f"⚠️  فایل {sess_file} موجود نیست")
            continue
        
        try:
            # Sync mode - بدون await
            client = TelegramClient(sess_file, int(aid), ahash)
            client.start()
            
            dialogs = client.get_dialogs()
            
            for dialog in dialogs:
                if dialog.is_group or dialog.is_channel:
                    total_dialogs += 1
                    try:
                        client.send_message(dialog.id, text)
                        sent += 1
                        
                        # بروزرسانی پیام لحظه‌ای
                        progress_text = (
                            f"📤 <b>ارسال تبلیغ...</b>\n\n"
                            f"✅ {sent} / {total_dialogs} چت\n\n"
                            f"⏳ درحال پردازش..."
                        )
                        try:
                            bot.edit_message_text(
                                progress_text,
                                message_chat_id,
                                message_id,
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
                        
                        time.sleep(SEND_DELAY)
                    except FloodWaitError as e:
                        time.sleep(e.seconds + 1)
                        try:
                            client.send_message(dialog.id, text)
                            sent += 1
                            
                            # بروزرسانی پس از retry
                            progress_text = (
                                f"📤 <b>ارسال تبلیغ...</b>\n\n"
                                f"✅ {sent} / {total_dialogs} چت\n\n"
                                f"⏳ درحال پردازش..."
                            )
                            try:
                                bot.edit_message_text(
                                    progress_text,
                                    message_chat_id,
                                    message_id,
                                    parse_mode="HTML"
                                )
                            except Exception:
                                pass
                        except Exception:
                            pass
                    except (ChatWriteForbiddenError, UserBannedInChannelError,
                            ChannelPrivateError, UsernameNotOccupiedError):
                        pass
                    except Exception:
                        pass
            
            client.disconnect()
        except Exception as e:
            print(f"❌ خطا با {phone}: {type(e).__name__}")
    
    return sent, total_dialogs


# =============== Login Flow ===============
login_states = {}


@bot.message_handler(commands=["login"])
def cmd_login(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ فقط ادمین.")
        return
    msg = bot.send_message(
        message.chat.id,
        "🔐 <b>اتصال اکانت</b>\n\n"
        "🔹 فایل <code>.session</code> بفرست\n"
        "🔹 بعد API ID و API Hash رو بفرست"
    )
    login_states[message.from_user.id] = {"step": "waiting_file"}


@bot.message_handler(content_types=["document"])
def handle_document(message):
    uid = message.from_user.id
    if uid not in login_states or login_states[uid].get("step") != "waiting_file":
        return

    file_name = message.document.file_name or "session"
    
    if not file_name.endswith(".session"):
        bot.reply_to(message, "❌ فقط فایل `.session` بفرست.")
        return

    try:
        bot.send_message(message.chat.id, "⏳ در حال دانلود...")
        
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        
        tmp_name = f"temp_{uid}_{int(time.time())}.session"
        src = os.path.join(SESSIONS_DIR, tmp_name)
        
        with open(src, "wb") as f:
            f.write(downloaded)
        
        login_states[uid]["temp_file"] = src
        login_states[uid]["step"] = "waiting_api_id"
        
        bot.send_message(
            message.chat.id,
            f"✅ فایل دریافت شد ({len(downloaded)} بایت).\n\n"
            f"🔢 حالا <b>API ID</b> رو بفرست:"
        )
    except Exception as e:
        print(f"Download error: {type(e).__name__}")
        bot.send_message(
            message.chat.id, 
            "❌ خطا در دانلود"
        )
        if uid in login_states:
            del login_states[uid]


@bot.message_handler(func=lambda m: m.from_user.id in login_states
                     and login_states[m.from_user.id].get("step") in ("waiting_api_id", "waiting_api_hash"))
def handle_creds(message):
    uid = message.from_user.id
    state = login_states[uid]
    step = state.get("step")
    text = (message.text or "").strip()

    if step == "waiting_api_id":
        if not text.isdigit():
            bot.reply_to(message, "❌ API ID باید عدد باشه:")
            return
        state["api_id"] = int(text)
        state["step"] = "waiting_api_hash"
        bot.send_message(message.chat.id, "✅ ذخیره شد.\n\n🔑 حالا <b>API Hash</b> رو بفرست:")

    elif step == "waiting_api_hash":
        api_hash = text
        if len(api_hash) != 32:
            bot.reply_to(message, "❌ API Hash باید ۳۲ کاراکتر باشه:")
            return
        state["api_hash"] = api_hash
        finalize_login(uid, message)


def finalize_login(uid, message):
    """تأیید لاگین و اتصال اکانت (SYNC)"""
    state = login_states[uid]
    api_id = state["api_id"]
    api_hash = state["api_hash"]
    src = state["temp_file"]

    try:
        # Sync mode - بدون await
        client = TelegramClient(src, api_id, api_hash)
        client.start()
        
        # بررسی authorization
        if not client.is_user_authorized():
            client.disconnect()
            if os.path.exists(src):
                os.remove(src)
            if uid in login_states:
                del login_states[uid]
            bot.send_message(
                message.chat.id,
                "❌ این سشن معتبر نیست\n\n"
                "💡 یک سشن معتبر ارسال کن"
            )
            return
        
        me = client.get_me()
        phone = me.phone or f"id_{me.id}"
        first_name = me.first_name or "—"
        user_id = me.id
        
        client.disconnect()
        
        # ذخیره فایل با نام دقیق
        final_name = f"session_{uid}_{int(time.time())}.session"
        final_path = os.path.join(SESSIONS_DIR, final_name)
        shutil.move(src, final_path)
        
        add_session_file(uid, phone, final_path, api_id, api_hash)
        del login_states[uid]
        
        bot.send_message(
            message.chat.id,
            f"✅ <b>اکانت متصل شد!</b>\n\n"
            f"📱 <code>{phone}</code>\n"
            f"👤 {first_name}\n"
            f"🆔 <code>{user_id}</code>\n\n"
            f"🎉 حالا آماده‌ای برای ارسال تبلیغات!",
            parse_mode="HTML"
        )
    except Exception as e:
        if os.path.exists(src):
            try:
                os.remove(src)
            except Exception:
                pass
        if uid in login_states:
            del login_states[uid]
        
        print(f"Login error: {type(e).__name__}: {e}")
        
        bot.send_message(
            message.chat.id,
            "❌ خطا در اتصال\n\n"
            "💡 این موارد چک کن:\n"
            "✓ API ID/Hash درست باشه\n"
            "✓ فایل .session معتبر باشه\n"
            "✓ دوباره سعی کن"
        )


# =============== Account Management ===============
@bot.message_handler(commands=["sessions"])
def cmd_sessions(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ فقط ادمین.")
        return
    rows = get_user_sessions(message.from_user.id, active_only=False)
    if not rows:
        bot.reply_to(message, "هیچ اکانتی وصل نیست. /login بزن.")
        return
    txt = "📱 <b>اکانت‌های متصل:</b>\n\n"
    kb = types.InlineKeyboardMarkup()
    for sid, phone, _, _, _, is_active in rows:
        status = "✅" if is_active else "❌"
        txt += f"{status} <code>{phone}</code>\n"
        kb.add(types.InlineKeyboardButton(f"🗑 حذف {phone}", callback_data=f"del_{sid}"))
    bot.send_message(message.chat.id, txt, parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("del_"))
def delete_session_cb(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔️ فقط ادمین.")
        return
    sid = int(call.data.split("_")[1])
    phone = delete_session_file(sid)
    if phone:
        bot.answer_callback_query(call.id, f"حذف شد: {phone}")
        bot.edit_message_text(
            f"🗑 اکانت <code>{phone}</code> حذف شد.",
            call.message.chat.id, call.message.message_id, parse_mode="HTML"
        )


# =============== Main Commands ===============
@bot.message_handler(commands=["start"])
def cmd_start(message):
    add_user(message.from_user.id, message.from_user.username or "")
    bot.send_message(
        message.chat.id,
        "👋 به ربات تبلیغات Shadow خوش امدید!\n\n"
        "📣 /buy - ثبت سفارش تبلیغ\n"
        "🆘 /support - پشتیبانی"
    )


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
    msg = bot.send_message(
        message.chat.id,
        f"📝 متن تبلیغ رو بفرست.\n"
        f"💰 هزینه هر پیام: {PRICE_PER_MESSAGE:,} تومان"
    )
    bot.register_next_step_handler(msg, process_ad_text)


def process_ad_text(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ لطفا فقط متن بفرست.")
        return
    uid = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or ""
    order_id = create_order(uid, username, message.text)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ پرداخت کردم", callback_data=f"paid_{order_id}"))
    bot.send_message(
        message.chat.id,
        f"✅ سفارش <b>#{order_id}</b> ثبت شد.\n\n{WALLET_INFO}\n\nبعد از واریز دکمه رو بزن 👇",
        reply_markup=kb,
        parse_mode="HTML"
    )


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
        types.InlineKeyboardButton("✅ تایید و ارسال", callback_data=f"approve_{order_id}"),
        types.InlineKeyboardButton("❌ رد", callback_data=f"reject_{order_id}")
    )
    bot.send_message(
        ADMIN_ID,
        f"📥 <b>سفارش #{order_id}</b>\n"
        f"👤 @{username} (ID: <code>{user_id}</code>)\n\n"
        f"📝 <b>متن تبلیغ:</b>\n{ad_text}",
        reply_markup=kb,
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith(("approve_", "reject_")))
def on_admin_decision(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔️ فقط ادمین.")
        return
    action, oid = call.data.split("_")
    oid = int(oid)
    order = get_order(oid)
    if not order:
        bot.answer_callback_query(call.id, "سفارش نیست.")
        return
    _, user_id, username, ad_text, status = order

    if action == "approve":
        set_order_status(oid, "approved")
        
        # ارسال پیام لحظه‌ای
        progress_msg = bot.send_message(
            call.message.chat.id, 
            "📤 <b>ارسال تبلیغ...</b>\n\n✅ 0 / 0 چت\n\n⏳ درحال شروع...",
            parse_mode="HTML"
        )
        
        # ارسال تبلیغ با بروزرسانی
        sent, total = broadcast_via_telethon(
            ADMIN_ID, 
            ad_text,
            call.message.chat.id,
            progress_msg.message_id
        )
        
        # پیام نهایی
        bot.edit_message_text(
            f"✅ <b>سفارش #{oid} تایید شد!</b>\n\n"
            f"📤 ارسال: <b>{sent} / {total}</b> چت",
            call.message.chat.id, 
            progress_msg.message_id,
            parse_mode="HTML"
        )
        
        try:
            bot.send_message(user_id, f"🎉 تبلیغ شما در {sent} چت ارسال شد!")
        except Exception:
            pass
    else:
        set_order_status(oid, "rejected")
        bot.edit_message_text(f"❌ سفارش #{oid} رد شد.", call.message.chat.id, call.message.message_id)
        try:
            bot.send_message(user_id, "❌ سفارش تایید نشد. /support")
        except Exception:
            pass
    bot.answer_callback_query(call.id, "ثبت شد.")


@bot.message_handler(commands=["support"])
def cmd_support(message):
    msg = bot.send_message(
        message.chat.id,
        "🆘 پشتیبانی: <code>@shikh3</code>\n\nپیامت رو بنویس:"
    )
    bot.register_next_step_handler(msg, forward_support_message)


def forward_support_message(message):
    if not message.text:
        bot.send_message(message.chat.id, "❌ لطفا فقط متن بفرست.")
        return
    try:
        bot.send_message(
            ADMIN_ID,
            f"🆘 پشتیبانی از "
            f"@{message.from_user.username or message.from_user.first_name} "
            f"(ID: <code>{message.from_user.id}</code>):\n\n"
            f"<blockquote>{message.text}</blockquote>",
            parse_mode="HTML"
        )
        bot.send_message(message.chat.id, "✅ پیامت ارسال شد. به‌زودی پاسخ میدیم.")
    except Exception:
        bot.send_message(message.chat.id, f"❌ خطا در ارسال")


@bot.message_handler(commands=["orders"])
def cmd_orders(message):
    con = db()
    cur = con.cursor()
    if message.from_user.id == ADMIN_ID:
        cur.execute("SELECT order_id, user_id, username, status, created_at FROM orders ORDER BY order_id DESC LIMIT 20")
    else:
        cur.execute("SELECT order_id, user_id, username, status, created_at FROM orders WHERE user_id=? ORDER BY order_id DESC LIMIT 20", (message.from_user.id,))
    rows = cur.fetchall()
    con.close()
    if not rows:
        bot.reply_to(message, "سفارشی نیست.")
        return
    txt = "📊 <b>سفارش‌ها:</b>\n\n"
    for oid, uid, uname, st, ts in rows:
        st_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(st, "❓")
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        txt += f"{st_emoji} <b>#{oid}</b> | @{uname} | {date}\n"
    bot.send_message(message.chat.id, txt, parse_mode="HTML")


@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ فقط ادمین.")
        return
    con = db(); cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users"); users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM orders"); orders = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM orders WHERE status='approved'"); approved = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sessions WHERE is_active=1"); sessions = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM groups"); groups = cur.fetchone()[0]
    con.close()
    txt = (
        "📊 <b>آمار ربات:</b>\n\n"
        f"👥 کاربران: <b>{users}</b>\n"
        f"📦 سفارش‌ها: <b>{orders}</b>\n"
        f"✅ تاییدشده: <b>{approved}</b>\n"
        f"🔐 اکانت‌های فعال: <b>{sessions}</b>\n"
        f"📢 گروه‌ها: <b>{groups}</b>"
    )
    bot.send_message(message.chat.id, txt, parse_mode="HTML")


if __name__ == "__main__":
    print("=" * 40)
    print("🚀 ربات تبلیغاتی (Sync Mode)")
    print(f"👤 Admin ID: {ADMIN_ID}")
    print(f"💰 قیمت هر پیام: {PRICE_PER_MESSAGE:,} تومان")
    print("=" * 40)
    try:
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
    except KeyboardInterrupt:
        print("\n❌ ربات متوقف شد.")
