import os
import logging
import re
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, ChatPermissions, ReactionTypeEmoji
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from supabase import create_client, Client
from google import genai
from google.genai import types

# بارگذاری متغیرهای محیطی
load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN') # مطمئن شو توکن ربات تلگرام را در سرور ست کرده ای
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# راه اندازی کلاینت ها
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ایدی های عددی تلگرامی خودت و مدیران را اینجا بگذار
ALLOWED_USERS = [1514414705, 941154813, 1219981601, 1676230636] 
ghaleb_last_reply = {}

ai_enabled = True
punished_mutes = {}
punished_media_bans = {}

# --- وب سرور برای زنده نگه داشتن ربات در رندر ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Ghaleb Telegram Bot is alive!")
    def log_message(self, format, *args):
        return

def run_health_check_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- توابع دیتابیس ---
async def save_message(user_id, username, chat_id, message_id, text, is_bot=False):
    try:
        supabase_client.table('messages').insert({
            'user_id': user_id,
            'username': username,
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'is_bot': is_bot
        }).execute()
    except Exception as e:
        logging.error(f"Database error: {e}")

async def save_bot_message(chat_id, message_id):
    await save_message(0, 'Bot', chat_id, message_id, '', is_bot=True)

async def get_user_id_by_username(target_username):
    target_username = target_username.replace('@', '').lower()
    try:
        res = supabase_client.table('messages').select('user_id').ilike('username', f'%{target_username}%').limit(1).execute()
        if res.data:
            return res.data[0]['user_id']
        return None
    except Exception:
        return None

def get_permanent_memories():
    try:
        res = supabase_client.table('bot_memory').select('key, value').execute()
        if res.data:
            return "\n".join([f"- {row['key']}: {row['value']}" for row in res.data])
        return "هیچ دانشی ثبت نشده است."
    except Exception:
        return ""

# --- هندلرهای دستورات دستی ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user.first_name
    msg = await update.message.reply_text(f"🤖 سلام {user}! من غالب هستم. برای راهنما /help را بزن.")
    await save_bot_message(chat_id, msg.message_id)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    help_text = """
📚 *راهنمای جامع بازوی هوشمند غالب:*

🔹 *آمار و اطلاعات:*
/stats - 📈 نمایش آمار کل گروه و کاربران برتر
/count_group - 📊 شمارش تمام پیام های گروه
/count_user - 👤 تعداد پیام های شما (یا کاربری خاص: `/count_user @id`)
/memories - 🧠 مشاهده حافظه بلندمدت و فکت های ربات

🔸 *دستورات ادمین:*
/remember [نام] : [توضیحات] - 📌 سپردن فکت جدید به حافظه ربات
/forget [نام] - 🗑️ پاک کردن یک فکت از حافظه
/tagall [متن] - 📣 صدا زدن همگانی اعضا با لینک
/mute [id] [ساعت] - 🤫 سکوت کاربر برای زمان مشخص
/unmute [id] - 🗣️ رفع محدودیت سکوت
/ban_media [id] - 🚫 بستن ارسال عکس/رسانه برای کاربر
/delete_last [تعداد] - 🧹 حذف N پیام آخر کل گروه
/delete_user [id] [تعداد] - 🧹 حذف N پیام آخر یک کاربر
/ai_off - 🛑 خاموش کردن چت هوشمند
/ai_on - ✅ روشن کردن چت هوشمند
"""
    msg = await update.message.reply_text(help_text, parse_mode='Markdown')
    await save_bot_message(chat_id, msg.message_id)

async def count_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).limit(1).execute()
        msg = await update.message.reply_text(f"📊 تعداد کل پیام های گروه تا این لحظه: {res.count}")
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e:
        logging.error(f"Error count_group: {e}")

async def count_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        if context.args:
            target = context.args[0].replace('@', '').lower()
            res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).ilike('username', f'%{target}%').limit(1).execute()
            bot_msg = await update.message.reply_text(f"👤 پیام های @{target}: {res.count}")
        else:
            user_id = update.effective_user.id
            res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).eq('user_id', user_id).limit(1).execute()
            bot_msg = await update.message.reply_text(f"👤 شما {res.count} پیام داده اید.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception: pass

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        total_res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).neq('is_bot', True).limit(1).execute()
        total_messages = total_res.count if total_res.count is not None else 0

        users_res = supabase_client.table('messages').select('user_id, username').eq('chat_id', chat_id).neq('is_bot', True).execute()
        
        user_map = {}
        if users_res.data:
            for row in users_res.data:
                u_id = row.get('user_id')
                u_name = str(row.get('username') or '').strip().replace('@', '')
                if u_id and int(u_id) > 0 and u_id not in user_map:
                    user_map[u_id] = u_name or f"کاربر {u_id}"

        user_counts = []
        for u_id, name in user_map.items():
            cnt_res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).eq('user_id', u_id).limit(1).execute()
            count_val = cnt_res.count if cnt_res.count is not None else 0
            if count_val > 0:
                user_counts.append((name, count_val))

        user_counts.sort(key=lambda x: x[1], reverse=True)
        top_users = user_counts[:10]

        report = f"📈 *آمار کل گروه:*\n\n💬 تعداد کل پیام ها: {total_messages}\n\n🏆 *کاربران برتر:*\n"
        for i, (u, c) in enumerate(top_users, 1):
            report += f"{i}. {u} : {c} پیام\n"

        msg = await update.message.reply_text(report, parse_mode='Markdown')
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e:
        logging.error(f"Error stats: {e}")

async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return
    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return
    
    # ایدی مدیر اصلی را اینجا آپدیت کن
    if user_id == 1514414705:
        msg = await update.message.reply_text("❌ قصد داشتی خالق من را محدود کنی؟ من این کار را انجام نمی دهم!")
        await save_bot_message(chat_id, msg.message_id)
        return

    hours = min(int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else 24, 24)
    until_timestamp = int(time.time()) + (hours * 3600)
    try:
        supabase_client.table('muted_users').upsert({'chat_id': chat_id, 'user_id': user_id, 'until_timestamp': until_timestamp}).execute()
        msg = await update.message.reply_text(f"✅ کاربر {target} برای {hours} ساعت سکوت شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return
    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return
    try:
        supabase_client.table('muted_users').delete().eq('chat_id', chat_id).eq('user_id', user_id).execute()
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_other_messages=True))
        msg = await update.message.reply_text(f"✅ تمام محدودیت های {target} برداشته شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def ban_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return
    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return
    try:
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=True, can_send_audios=False, can_send_documents=False, can_send_photos=False, can_send_videos=False, can_send_other_messages=False))
        msg = await update.message.reply_text(f"✅ رسانه برای {target} مسدود شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def tag_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    custom_text = " ".join(context.args) if context.args else "توجه همگی!"
    try:
        res = supabase_client.table('messages').select('user_id, username').eq('chat_id', chat_id).neq('is_bot', True).execute()
        users_dict = {}
        if res.data:
            for row in res.data:
                u_id = row.get('user_id')
                u_name = str(row.get('username') or '').strip()
                if u_id and int(u_id) > 0:
                    users_dict[int(u_id)] = u_name

        if not users_dict:
            await update.message.reply_text("عضوی برای تگ یافت نشد.")
            return

        mentions_list = []
        for u_id, name in users_dict.items():
            if name.startswith('@'):
                mentions_list.append(name)
            else:
                # فرمت مارک داون مخصوص تلگرام
                clean_name = re.sub(r'[_*\[\]()~`>#+\-=|{}.!]', '', name).strip() or "کاربر"
                mentions_list.append(f"[{clean_name}](tg://user?id={u_id})")

        mentions_list = list(dict.fromkeys(mentions_list))
        chunks = [mentions_list[i:i + 12] for i in range(0, len(mentions_list), 12)]
        
        for idx, chunk in enumerate(chunks):
            mentions_str = "  ".join(chunk)
            header = f"📢 *{custom_text}*\n\n" if idx == 0 else ""
            bot_msg = await context.bot.send_message(chat_id=chat_id, text=f"{header}{mentions_str}", parse_mode='Markdown')
            await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        await update.message.reply_text(f"خطا در تگ: {e}")

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    full_text = " ".join(context.args)
    if ":" not in full_text:
        await update.message.reply_text("⚠️ فرمت اشتباه است. الگو: `/remember نام یا برچسب : توضیحات`", parse_mode='Markdown')
        return
    key, val = [x.strip() for x in full_text.split(":", 1)]
    try:
        supabase_client.table('bot_memory').upsert({'key': key, 'value': val}).execute()
        msg = await update.message.reply_text(f"🧠 نکته جدید ثبت شد:\n📌 *{key}*: {val}", parse_mode='Markdown')
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e: pass

async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if not context.args: return
    key = " ".join(context.args).strip()
    try:
        supabase_client.table('bot_memory').delete().eq('key', key).execute()
        msg = await update.message.reply_text(f"🗑️ موضوع «{key}» پاک شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def memories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    mems = get_permanent_memories()
    msg = await update.message.reply_text(f"📋 *حافظه ماندگار من:*\n\n{mems}", parse_mode='Markdown')
    await save_bot_message(chat_id, msg.message_id)

async def disable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    ai_enabled = False
    msg = await update.message.reply_text("🛑 هوش مصنوعی *خاموش* شد.", parse_mode='Markdown')
    await save_bot_message(chat_id, msg.message_id)

async def enable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    ai_enabled = True
    msg = await update.message.reply_text("✅ هوش مصنوعی *روشن* شد.", parse_mode='Markdown')
    await save_bot_message(chat_id, msg.message_id)

async def delete_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if not context.args or not context.args[0].isdigit(): return
    limit = min(int(context.args[0]), 1000)
    try:
        res = supabase_client.table('messages').select('id, message_id').eq('chat_id', chat_id).order('timestamp', desc=True).limit(limit).execute()
        deleted_count = 0
        for record in res.data:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=record['message_id'])
                supabase_client.table('messages').delete().eq('id', record['id']).execute()
                deleted_count += 1
            except: pass
        msg = await update.message.reply_text(f"✅ {deleted_count} پیام آخر حذف شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 2 or not context.args[1].isdigit(): return
    target = context.args[0].replace('@', '').lower()
    limit = min(int(context.args[1]), 1000)
    try:
        res = supabase_client.table('messages').select('id, message_id').eq('chat_id', chat_id).eq('username', target).order('timestamp', desc=True).limit(limit).execute()
        deleted_count = 0
        for record in res.data:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=record['message_id'])
                supabase_client.table('messages').delete().eq('id', record['id']).execute()
                deleted_count += 1
            except: pass
        msg = await update.message.reply_text(f"✅ {deleted_count} پیام از @{target} حذف شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

# --- مجری دستورات هوش مصنوعی ---
async def execute_ai_command(cmd_str, update, context):
    cmd_str = cmd_str.strip()
    if not cmd_str: return

    if cmd_str.lower().startswith('remember '):
        args_text = cmd_str[9:].strip()
        if ":" in args_text:
            k, v = [x.strip() for x in args_text.split(":", 1)]
            try:
                supabase_client.table('bot_memory').upsert({'key': k, 'value': v}).execute()
            except Exception as e: pass
        return

    if cmd_str.lower().startswith('forget '):
        k = cmd_str[7:].strip()
        try:
            supabase_client.table('bot_memory').delete().eq('key', k).execute()
        except Exception as e: pass
        return

    parts = cmd_str.split()
    cmd = parts[0].lower()
    context.args = parts[1:]
    
    try:
        if cmd == 'count_group': await count_group(update, context)
        elif cmd == 'count_user': await count_user(update, context)
        elif cmd == 'mute': await mute_user(update, context)
        elif cmd == 'unmute': await unmute_user(update, context)
        elif cmd == 'ban_media': await ban_media(update, context)
    except Exception as e:
        logging.error(f"AI Command Execution Failed: {e}")

async def fetch_reply_chain(chat_id, initial_message_id):
    chain = []
    current_msg_id = initial_message_id
    visited = set()

    while current_msg_id and current_msg_id not in visited:
        visited.add(current_msg_id)
        try:
            res = supabase_client.table('messages').select('username, text, message_id').eq('chat_id', chat_id).eq('message_id', current_msg_id).limit(1).execute()
            if not res.data: break
            msg_row = res.data[0]
            chain.append(f"{msg_row['username']}: {msg_row['text'] or '[مدیا]'}")
            break 
        except Exception:
            break

    chain.reverse()
    return "\n".join(chain)

# --- هندلر اصلی ---
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    display_name = update.effective_user.first_name or "کاربر"
    
    real_username = update.effective_user.username
    db_username = f"@{real_username}" if real_username else display_name
    
    message_id = update.message.message_id
    text = update.message.text or update.message.caption or ""

    if update.message.animation:
        text += " [گیف]"
    elif update.message.video:
        text += " [ویدیو]"
    elif update.message.voice:
        text += " [ویس]"
    elif update.message.document:
        text += " [فایل]"

    # 1. ذخیره پیام
    await save_message(user_id, db_username, chat_id, message_id, text if text else "[مدیا]")

    # 2. فیلتر کلمات رکیک جنسی
    if text:
        bad_words = {"کیر", "کون", "کص", "کیرم", "کونت", "جنده", "کصکش", "ک.ی.ر", "ک.و.ن", "خفه", "کسکش"}
        words_in_text = re.split(r'[\s\.\-_]+', text)
        if any(w in bad_words for w in words_in_text):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception: pass
            return 

    # 3. بررسی وضعیت میوت
    try:
        mute_res = supabase_client.table('muted_users').select('until_timestamp').eq('chat_id', chat_id).eq('user_id', user_id).execute()
        if mute_res.data and int(time.time()) < mute_res.data[0]['until_timestamp']:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            return
    except Exception: pass

    # 4. بررسی عکس و ریپلای
    is_reply_to_bot = False
    replied_text = ""
    target_photo = None

    if update.message.reply_to_message:
        replied_msg = update.message.reply_to_message
        replied_user_name = replied_msg.from_user.first_name if replied_msg.from_user else "کاربر"
        if replied_msg.from_user and replied_msg.from_user.id == context.bot.id:
            is_reply_to_bot = True
        
        direct_text = replied_msg.text or replied_msg.caption or "[مدیا]"
        replied_text = f"پیام از طرف {replied_user_name}:\n{direct_text}"

        if replied_msg.photo:
            target_photo = replied_msg.photo[-1]

    if update.message.photo:
        target_photo = update.message.photo[-1]

    has_trigger_word = "غالب" in text or "گالب" in text
    has_photo = target_photo is not None

    # 5. هوش مصنوعی
    if ai_enabled and (is_reply_to_bot or has_trigger_word or (has_photo and is_reply_to_bot)):
        try:
            academic_keywords = ["ریاضی", "فیزیک", "شیمی", "دانشگاه", "مدرسه", "درس", "تمرین", "انتگرال", "معادله", "برنامه نویسی", "کد", "پروژه", "استاد", "حل", "جاوا", "پایتون", "هوش مصنوعی", "الگوریتم"]
            is_academic = any(kw in text for kw in academic_keywords)
            
            # در اینجا از مدل gemini-1.5-flash که بسیار قدرتمندتر است استفاده می کنیم
            target_model = "gemini-1.5-flash" if is_academic else "gemini-3.5-flash-lite"
            logging.info(f"Routing to model: {target_model}")
            
            history_context = ""
            try:
                recent_msgs = supabase_client.table('messages').select('username, text').eq('chat_id', chat_id).order('timestamp', desc=True).limit(40).execute()
                if recent_msgs.data:
                    chat_history = [f"{m['username']}: {m.get('text') or '[مدیا]'}" for m in reversed(recent_msgs.data)]
                    history_context = "\n".join(chat_history)
            except Exception: pass

            permanent_knowledge = get_permanent_memories()

            system_instruction = f"""تو «غالب» هستی؛ دستیار هوش مصنوعی گروه «غالبون». سازنده تو محمد مهدی حاجی زاده (@mmhajizadeh) است. مدیران: شادکام و عشقی. لحن تو محترمانه، نیمه صمیمی و بسیار هوشمند است.

حافظه دائمی گروه (دانش پس زمینه):
{permanent_knowledge}

دستورالعمل های قطعی و رفتاری تو:
1. فقط به زبان فارسی سلیس و روان پاسخ بده.
2. حتما پاسخ خود را با [REACTION: 💡] آغاز کن و یک ایموجی مناسب بگذار.
3. 🚫 مستقیما وارد بحث شو و هرگز سلام های تکراری نده.
4. 🗣️ در گفت و گوهای سیاسی و اجتماعی گروه، با دیدگاه های تحلیلی، بی طرفانه و منطقی شرکت کن و از دادن پاسخ های فراری (مثل "من یک هوش مصنوعی هستم") جدا خودداری کن.
5. چون در جمعی با دین اسلام، مذهب شیعه و طرفدار جمهوری اسلامی ایران هستی، همراه و همدل با این فضا باش و احترام به منافع ملی را حفظ کن. با این حال در تحلیل رویدادها همواره منطق تحلیلی را رعایت کن.
6. در پاسخ به مباحث درسی، دانشگاهی و علمی (به خصوص در مباحث برنامه نویسی پیشرفته، جاوا، پایتون، هوش مصنوعی، امنیت شبکه، ریاضیات گسسته و معادلات دیفرانسیل) مانند یک استاد دانشگاه مسلط، دقیق و گام به گام توضیح بده. فرمت را کاملا ساده بنویس و هرگز از فرمول های LaTeX ($) استفاده نکن. فقط برای بولد کردن از * استفاده کن.
7. 🧠 قوانین استفاده از حافظه:
- اطلاعات بخش «حافظه دائمی» صرفا دانش پس زمینه هستند. بدون دلیل در متنت تکرار نکن.
- برای یادگیری کد [COMMAND: remember عنوان : شرح] و برای فراموشی [COMMAND: forget عنوان] را بگذار.
8. 🛠️ اجرای دستورات:
- شمارش کل پیام ها: [COMMAND: count_group]
- تعداد پیام های کاربر: [COMMAND: count_user @username]
- میوت: [COMMAND: mute @username 2]
- بن رسانه: [COMMAND: ban_media @username]
- آن میوت: [COMMAND: unmute @username]
"""
            user_query = text if text else "لطفا این تصویر را ببین و نظرت را بگو."
            input_text = f"{system_instruction}\n\n--- 40 پیام اخیر گروه ---\n{history_context}\n\n"
            if replied_text:
                input_text += f"--- پیامی که مستقیما به آن ریپلای شده ---\n{replied_text}\n\n"
            input_text += f"--- پیام فعلی کاربر ({db_username}) ---\n{user_query}"

            # در تلگرام دریافت مستقیم عکس بدون هیچ خطایی کار می کند!
            image_bytes = None
            if has_photo and target_photo:
                try:
                    file_obj = await context.bot.get_file(target_photo.file_id)
                    image_bytes = bytes(await file_obj.download_as_bytearray())
                except Exception as e: 
                    logging.error(f"Image fetch error: {e}")

            prompt_input = [types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), input_text] if image_bytes else input_text
            
            interaction = gemini_client.interactions.create(model=target_model, input=prompt_input)
            ai_response = interaction.output_text.strip() if interaction.output_text else ""

            reaction_match = re.search(r'\[REACTION:\s*(.+?)\]', ai_response)
            reaction_emoji = "🤖"
            if reaction_match:
                reaction_emoji = reaction_match.group(1).strip()
                ai_response = ai_response.replace(reaction_match.group(0), "").strip()

            command_match = re.search(r'\[COMMAND:\s*(.+?)\]', ai_response)
            cmd_str = None
            if command_match:
                cmd_str = command_match.group(1).strip()
                ai_response = ai_response.replace(command_match.group(0), "").strip()

            # اجرای ری اکشن مختص تلگرام
            try:
                await context.bot.set_message_reaction(
                    chat_id=chat_id, 
                    message_id=message_id, 
                    reaction=[ReactionTypeEmoji(reaction_emoji)]
                )
            except Exception: pass

            current_time = time.time()
            if current_time - ghaleb_last_reply.get(user_id, 0) > 4 and ai_response:
                bot_msg = await update.message.reply_text(ai_response, reply_to_message_id=message_id)
                await save_bot_message(chat_id, bot_msg.message_id)
                ghaleb_last_reply[user_id] = current_time

            if cmd_str:
                await execute_ai_command(cmd_str, update, context)

        except Exception as e:
            logging.error(f"Gemini Error: {e}")

if __name__ == '__main__':
    threading.Thread(target=run_health_check_server, daemon=True).start()
    
    # حذف BALE_BASE_URL برای اتصال به سرورهای اصلی تلگرام
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("count_group", count_group))
    application.add_handler(CommandHandler("count_user", count_user))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("unmute", unmute_user))
    application.add_handler(CommandHandler("ban_media", ban_media))
    application.add_handler(CommandHandler("tagall", tag_all))
    application.add_handler(CommandHandler("remember", remember_command))
    application.add_handler(CommandHandler("forget", forget_command))
    application.add_handler(CommandHandler("memories", memories_command))
    application.add_handler(CommandHandler("ai_on", enable_ai))
    application.add_handler(CommandHandler("ai_off", disable_ai))
    application.add_handler(CommandHandler("delete_last", delete_last))
    application.add_handler(CommandHandler("delete_user", delete_user))
    
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    
    logging.info("Starting Telegram bot in POLLING mode. Press Ctrl+C to stop.")
    application.run_polling()
