import os
import logging
import re
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import httpx

from telegram import Update, ChatPermissions
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

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# راه‌اندازی کلاینت‌ها
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

ALLOWED_USERS = [1514414705, 941154813, 1219981601, 1676230636]
ghaleb_last_reply = {}
BALE_BASE_URL = "https://tapi.bale.ai/bot"

ai_enabled = True
punished_mutes = {}
punished_media_bans = {}

# --- وب‌سرور ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Ghaleb Bot is alive!")
    def log_message(self, format, *args):
        return

def run_health_check_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- دیتابیس ---
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
        # جستجو در دیتابیس (چون یوزرنیم واقعی با @ ذخیره می‌شود، آن را بررسی می‌کنیم)
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
    help_text = "📚 **راهنمای بازوی غالب:**\n\n/stats - آمار گروه\n/count_group - شمارش کل پیام‌ها\n/count_user - پیام‌های کاربر\n/memories - حافظه ماندگار\n/tagall - تگ همگانی\n/mute | /unmute | /ban_media | /delete_last | /delete_user\n/ai_on | /ai_off"
    msg = await update.message.reply_text(help_text, parse_mode='Markdown')
    await save_bot_message(chat_id, msg.message_id)

async def count_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).execute()
        msg = await update.message.reply_text(f"📊 تعداد کل پیام‌ های گروه تا این لحظه: {res.count}")
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e:
        logging.error(f"Error count_group: {e}")

async def count_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        if context.args:
            target = context.args[0].replace('@', '').lower()
            res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).ilike('username', f'%{target}%').execute()
            bot_msg = await update.message.reply_text(f"👤 پیام‌ های @{target}: {res.count}")
        else:
            user_id = update.effective_user.id
            res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).eq('user_id', user_id).execute()
            bot_msg = await update.message.reply_text(f"👤 شما {res.count} پیام داده‌اید.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception: pass

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        # افزایش لیمیت برای خواندن آمار کل گروه
        res = supabase_client.table('messages').select('username').eq('chat_id', chat_id).neq('is_bot', True).limit(50000).execute()
        if not res.data: return
        from collections import Counter
        counts = Counter(m['username'].replace('@', '') for m in res.data if m['username'])
        top_users = counts.most_common(10)
        report = f"📈 **آمار کل گروه:**\n\n💬 تعداد کل پیام‌ ها: {len(res.data)}\n\n🏆 **کاربران برتر:**\n"
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
        msg = await update.message.reply_text(f"✅ تمام محدودیت‌ های {target} برداشته شد.")
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
            # تشخیص اینکه نام واقعی یوزرنیم است یا اسم نمایشی
            if name.startswith('@'):
                mentions_list.append(name)
            else:
                safe_name = re.sub(r'[\[\]()]', '', name) or "کاربر"
                mentions_list.append(f"[{safe_name}](uid:{u_id})")

        mentions_list = list(dict.fromkeys(mentions_list))
        chunks = [mentions_list[i:i + 12] for i in range(0, len(mentions_list), 12)]
        
        for idx, chunk in enumerate(chunks):
            mentions_str = "  ".join(chunk)
            header = f"📢 **{custom_text}**\n\n" if idx == 0 else ""
            bot_msg = await context.bot.send_message(chat_id=chat_id, text=f"{header}{mentions_str}", parse_mode='Markdown')
            await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        await update.message.reply_text(f"خطا در تگ: {e}")

# --- مجری دستورات هوش مصنوعی ---
async def execute_ai_command(cmd_str, update, context):
    parts = cmd_str.split()
    if not parts: return
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

# --- هندلر اصلی ---
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    display_name = update.effective_user.first_name or "کاربر"
    
    # 💡 ذخیره اصولی برای سیستم تگ: اگر یوزرنیم داشت با @ ذخیره می‌شود، در غیر این‌صورت اسم معمولی
    real_username = update.effective_user.username
    db_username = f"@{real_username}" if real_username else display_name
    
    message_id = update.message.message_id
    text = update.message.text or update.message.caption or ""

    # ۱. ذخیره پیام
    await save_message(user_id, db_username, chat_id, message_id, text if text else "[تصویر/مدیا]")

    # ۲. فیلتر کلمات رکیک جنسی (کاملاً هوشمند فقط برای کلمات مستقل)
    if text:
        bad_words = {"کیر", "کون", "کص", "کیرم", "کونت", "جنده", "کصکش", "ک.ی.ر", "ک.و.ن"}
        # متن با اسپیس، خط تیره و نقطه جدا می‌شود تا کلماتی مثل "سکونت" فیلتر نشوند
        words_in_text = re.split(r'[\s\.\-_]+', text)
        if any(w in bad_words for w in words_in_text):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception: pass
            return 

    # ۳. بررسی وضعیت میوت
    try:
        mute_res = supabase_client.table('muted_users').select('until_timestamp').eq('chat_id', chat_id).eq('user_id', user_id).execute()
        if mute_res.data and int(time.time()) < mute_res.data[0]['until_timestamp']:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            return
    except Exception: pass

    # ۴. بررسی عکس و ریپلای
    is_reply_to_bot = False
    replied_text = ""
    target_photo = None

    if update.message.reply_to_message:
        replied_user_name = update.message.reply_to_message.from_user.first_name
        if update.message.reply_to_message.from_user.id == context.bot.id:
            is_reply_to_bot = True
        
        r_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or "[مدیا]"
        replied_text = f"پیام از طرف {replied_user_name}:\n{r_text}"
        
        if update.message.reply_to_message.photo:
            target_photo = update.message.reply_to_message.photo[-1]

    if update.message.photo:
        target_photo = update.message.photo[-1]

    has_trigger_word = "غالب" in text or "گالب" in text
    has_photo = target_photo is not None

    # ۵. هوش مصنوعی
    if ai_enabled and (is_reply_to_bot or has_trigger_word or (has_photo and is_reply_to_bot)):
        try:
            # حافظه عمیق‌تر (۲۵ پیام اخیر)
            history_context = ""
            try:
                recent_msgs = supabase_client.table('messages').select('username, text').eq('chat_id', chat_id).order('timestamp', desc=True).limit(25).execute()
                if recent_msgs.data:
                    chat_history = [f"{m['username']}: {m.get('text') or '[مدیا]'}" for m in reversed(recent_msgs.data)]
                    history_context = "\n".join(chat_history)
            except Exception: pass

            permanent_knowledge = get_permanent_memories()

            system_instruction = f"""تو «غالب» هستی؛ دستیار هوش مصنوعی گروه «غالبون». سازنده تو محمد مهدی حاجی زاده (@mmhajizadeh) است. مدیران: شادکام و عشقی. لحن تو محترمانه، نیمه‌صمیمی و بسیار هوشمند است.
حافظه دائمی گروه:
{permanent_knowledge}

دستورالعمل‌ها:
۱. فارسی سلیس و روان پاسخ بده.
۲. حتماً پاسخ خود را با [REACTION: 💡] آغاز کن و یک ایموجی مناسب بگذار.
۳. تو می‌توانی دستورات مدیریت گروه را به جای مدیران اجرا کنی! اگر کاربر (به خصوص ادمین‌ها) از تو خواستند آماری بدهی یا کسی را محدود کنی، در انتهای پیامِ خود، این کدها را قرار بده تا من آن را اجرا کنم:
- برای شمارش کل پیام‌ها: [COMMAND: count_group]
- برای تعداد پیام‌های کاربر: [COMMAND: count_user @username] (اگر نام نیاورد آیدی خودش را بگذار)
- برای میوت/سکوت: [COMMAND: mute @username 2] (عدد ساعت است)
- برای بن کردن رسانه: [COMMAND: ban_media @username]
- برای آن‌میوت: [COMMAND: unmute @username]
"""
            user_query = text if text else "لطفاً این تصویر را ببین و نظرت را بگو."
            input_text = f"{system_instruction}\n\n--- 25 پیام اخیر گروه ---\n{history_context}\n\n"
            if replied_text:
                input_text += f"--- پیامی که مستقیماً به آن ریپلای شده ---\n{replied_text}\n\n"
            input_text += f"--- پیام فعلی کاربر ({db_username}) ---\n{user_query}"

            # پردازش تصویر
            image_bytes = None
            if has_photo and target_photo:
                try:
                    file_obj = await context.bot.get_file(target_photo.file_id)
                    dl_url = file_obj.file_path if file_obj.file_path.startswith("http") else f"https://tapi.bale.ai/file/bot{TOKEN}/{file_obj.file_path}"
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp = await client.get(dl_url)
                        if resp.status_code == 200: image_bytes = resp.content
                except Exception: pass

            prompt_input = [types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), input_text] if image_bytes else input_text
            
            interaction = gemini_client.interactions.create(model="gemini-3.5-flash-lite", input=prompt_input)
            ai_response = interaction.output_text.strip() if interaction.output_text else ""

            # ۱. استخراج ری‌اکشن
            reaction_match = re.search(r'\[REACTION:\s*(.+?)\]', ai_response)
            reaction_emoji = "🤖"
            if reaction_match:
                reaction_emoji = reaction_match.group(1).strip()
                ai_response = ai_response.replace(reaction_match.group(0), "").strip()

            # ۲. استخراج و اجرای کامند
            command_match = re.search(r'\[COMMAND:\s*(.+?)\]', ai_response)
            cmd_str = None
            if command_match:
                cmd_str = command_match.group(1).strip()
                ai_response = ai_response.replace(command_match.group(0), "").strip()

            try:
                await context.bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=reaction_emoji)
            except Exception: pass

            current_time = time.time()
            if current_time - ghaleb_last_reply.get(user_id, 0) > 4 and ai_response:
                bot_msg = await update.message.reply_text(ai_response, reply_to_message_id=message_id)
                await save_bot_message(chat_id, bot_msg.message_id)
                ghaleb_last_reply[user_id] = current_time

            # اجرای کامند پس از پاسخ دادن
            if cmd_str:
                await execute_ai_command(cmd_str, update, context)

        except Exception as e:
            logging.error(f"Gemini Error: {e}")

if __name__ == '__main__':
    threading.Thread(target=run_health_check_server, daemon=True).start()
    application = ApplicationBuilder().token(TOKEN).base_url(BALE_BASE_URL).build()

    application.add_handler(CommandHandler(["start", "help", "count_group", "count_user", "stats", "mute", "unmute", "ban_media", "tagall", "ai_on", "ai_off"], lambda update, context: None)) # To prevent double execution of commands handled manually or just register them
    # ثبت دستی هندلرها
    application.add_handler(CommandHandler("count_group", count_group))
    application.add_handler(CommandHandler("count_user", count_user))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("unmute", unmute_user))
    application.add_handler(CommandHandler("ban_media", ban_media))
    application.add_handler(CommandHandler("tagall", tag_all))
    
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_messages))
    logging.info("Starting bot in POLLING mode. Press Ctrl+C to stop.")
    application.run_polling()
