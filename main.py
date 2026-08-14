import os
import logging
import re
from dotenv import load_dotenv
from telegram import Update, ChatPermissions
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
import time
from supabase import create_client, Client
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
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

# راه‌اندازی کلاینت‌های دیتابیس و جمینای
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

ALLOWED_USERS = [1514414705, 941154813, 1219981601, 1676230636]
ghaleb_last_reply = {}
BALE_BASE_URL = "https://tapi.bale.ai/bot"

# متغیر سراسری برای روشن/خاموش کردن هوش مصنوعی
ai_enabled = True

# لیست‌های حافظه برای کاربرانی که به خالق جسارت کرده‌اند!
punished_mutes = {}
punished_media_bans = {}

# سرور وب سبک برای پاسخ به Render و UptimeRobot
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Ghaleb Bot is alive and healthy!")

    def log_message(self, format, *args):
        return  # خاموش کردن لاگ های مزاحم پینگ

def run_health_check_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logging.info(f"Health check server running on port {port}")
    server.serve_forever()

# توابع دیتابیس
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
        logging.error(f"Database insertion failed: {e}")

async def save_bot_message(chat_id, message_id):
    await save_message(0, 'Bot', chat_id, message_id, '', is_bot=True)

async def get_user_id_by_username(username):
    username = username.replace('@', '').lower()
    try:
        response = supabase_client.table('messages').select('user_id').eq('username', username).limit(1).execute()
        if response.data:
            return response.data[0]['user_id']
        return None
    except Exception as e:
        logging.error(f"Error fetching user_id: {e}")
        return None

# هندلرهای پایه‌ای
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user.first_name
    bot_message = await update.message.reply_text(f"🤖 سلام {user}! من غالب هستم؛ ربات هوشمند مدیریت گروه. برای دیدن راهنما /help را بزن.")
    await save_bot_message(chat_id, bot_message.message_id)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    help_text = """
📚 **راهنمای بازوی غالب:**

🔹 **دستورات عمومی:**
/start - شروع کار با ربات
/stats - نمایش ۵ کاربر برتر و آمار کل پیام‌ ها
/count_group - شمارش کل پیام‌ های گروه
/count_user - تعداد پیام‌ های شما (یا کاربری خاص: `/count_user @id`)

🔸 **دستورات ادمین:**
/mute [username] [hours] - سکوت کاربر برای زمان مشخص
/unmute [username] - رفع سکوت کاربر
/ban_media [username] - بستن ارسال عکس/فیلم/استیکر برای کاربر
/delete_last [N] - حذف N پیام آخر گروه
/delete_user [username] [N] - حذف N پیام آخر یک کاربر
/ai_off - خاموش کردن هوش مصنوعی
/ai_on - روشن کردن هوش مصنوعی
"""
    bot_message = await update.message.reply_text(help_text, parse_mode='Markdown')
    await save_bot_message(chat_id, bot_message.message_id)

# خاموش و روشن کردن هوش مصنوعی
async def disable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    
    ai_enabled = False
    bot_msg = await update.message.reply_text("🛑 هوش مصنوعی (فیلتر و چت) *خاموش* شد. سایر دستورات ربات فعال هستند.", parse_mode='Markdown')
    await save_bot_message(chat_id, bot_msg.message_id)

async def enable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    
    ai_enabled = True
    bot_msg = await update.message.reply_text("✅ هوش مصنوعی *روشن* شد.", parse_mode='Markdown')
    await save_bot_message(chat_id, bot_msg.message_id)

# مدیریت پیام‌های گروه
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = (update.effective_user.username or update.effective_user.first_name).lower() 
    message_id = update.message.message_id
    text = update.message.text or ""

    # ۱. ذخیره در دیتابیس
    await save_message(user_id, username, chat_id, message_id, text)

    # ۲. بررسی میوت بودن
    try:
        mute_res = supabase_client.table('muted_users').select('until_timestamp').eq('chat_id', chat_id).eq('user_id', user_id).execute()
        if mute_res.data:
            muted_until = mute_res.data[0]['until_timestamp']
            if int(time.time()) < muted_until:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                return
            else:
                supabase_client.table('muted_users').delete().eq('chat_id', chat_id).eq('user_id', user_id).execute()
    except Exception as e:
        logging.error(f"Error checking mute: {e}")

    # ۳. فیلتر کلمات رکیک در پایتون (دقیق و سریع)
    if text:
        bad_words = ["کیر", "کون", "کص", "ک.ی.ر", "ک.و.ن", "ک.ص", "ک.یر", "کی.ر", "ک.ر"]
        text_no_spaces = text.replace(".", "")
        
        for bw in bad_words:
            if bw in text_no_spaces:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                    logging.info(f"Deleted by local bad word filter: {message_id}")
                except Exception as e:
                    logging.error(f"Failed to delete bad word message: {e}")
                return 

    # ۴. تشخیص اینکه آیا ربات باید جواب بدهد یا خیر
    is_reply_to_bot = False
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        if update.message.reply_to_message.from_user.id == context.bot.id:
            is_reply_to_bot = True

    has_trigger_word = "غالب" in text or "گالب" in text

    # اگر هوش مصنوعی روشن بود و ربات صدا زده شد
    if ai_enabled and (is_reply_to_bot or has_trigger_word):
        try:
            system_instruction = """تو «غالب» هستی؛ یک دستیار هوش مصنوعی بسیار باهوش، خوش‌برخورد، صمیمی، نکته‌سنج، حاضر‌جواب و کمی شوخ‌طبع (دقیقاً مثل هوش مصنوعی میرا در تلگرام) برای گروه چت «غالبون».
سازنده و خالق تو «محمد مهدی حاجی زاده» (@mmhajizadeh) است و مدیران گروه «شادکام» و «عشقی» هستند. همیشه با آن‌ها با احترام کامل صحبت کن.

دستورالعمل‌های حیاتی:
۱. فقط و فقط به زبان فارسی طبیعی، محاوره‌ای و روان صحبت کن. تحت هیچ شرایطی از کاراکترها یا کلمات روسی، چینی، اسپانیایی یا هر زبان دیگری استفاده نکن.
۲. حتماً پاسخ خود را دقیقاً با این فرمت آغاز کن: [REACTION: 💡] که به جای لامپ، یک ایموجی کاملاً متناسب با حس پیام بگذاری (مثلاً 👍, ❤️, 🔥, 😂, 🤔, 🤖, 😡, 🎉).
۳. پاسخ‌ها پرانرژی، کوتاه، جذاب و متناسب با موضوع چت باشد و از تکرار متن‌های کلیشه‌ای خودداری کن.
"""

            # استفاده از مدل پایدار و رایگان gemini-1.5-flash به صورت async
            response = await gemini_client.aio.models.generate_content(
                model='gemini-1.5-flash',
                contents=text,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.7,
                    max_output_tokens=300,
                )
            )
            
            ai_response = response.text.strip() if response.text else ""

            # استخراج ری‌اکشن
            reaction_match = re.search(r'\[REACTION:\s*(.+?)\]', ai_response)
            reaction_emoji = "🤖"
            
            if reaction_match:
                reaction_emoji = reaction_match.group(1).strip()
                ai_response = ai_response.replace(reaction_match.group(0), "").strip()
                
            # اعمال ری‌اکشن روی پیام کاربر در بله
            try:
                await context.bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=reaction_emoji
                )
            except Exception as e:
                logging.warning(f"Failed to set reaction: {e}")

            current_time = time.time()
            last_time = ghaleb_last_reply.get(user_id, 0) 
            
            # تاخیر زمانی برای جلوگیری از اسپم
            if current_time - last_time > 15 and ai_response:
                bot_msg = await update.message.reply_text(ai_response, reply_to_message_id=message_id)
                await save_bot_message(chat_id, bot_msg.message_id)
                ghaleb_last_reply[user_id] = current_time

        except Exception as e:
            logging.error(f"Gemini API Error: {e}")

# آمار و ارقام
async def count_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        response = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).execute()
        msg = await update.message.reply_text(f"📊 تعداد کل پیام‌ های ثبت‌ شده: {response.count}")
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e:
        logging.error(f"Error count_group: {e}")

async def count_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        if context.args:
            target = context.args[0].replace('@', '').lower()
            res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).eq('username', target).execute()
            bot_msg = await update.message.reply_text(f"👤 پیام‌ های @{target}: {res.count}")
        else:
            user_id = update.effective_user.id
            res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).eq('user_id', user_id).execute()
            bot_msg = await update.message.reply_text(f"👤 شما {res.count} پیام داده‌اید.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception:
        pass

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        response = supabase_client.table('messages').select('username').eq('chat_id', chat_id).neq('is_bot', True).execute()
        messages = response.data
        if not messages: return
        
        from collections import Counter
        counts = Counter(m['username'] for m in messages if m['username'])
        top_users = counts.most_common(5)

        report = f"📈 **آمار گروه:**\n\n💬 تعداد کل پیام‌ ها: {len(messages)}\n\n🏆 **۵ کاربر فعال برتر:**\n"
        for i, (u, c) in enumerate(top_users, 1):
            report += f"{i}. @{u} : {c} پیام\n"

        msg = await update.message.reply_text(report, parse_mode='Markdown')
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e:
        logging.error(f"Error stats: {e}")

# مدیریت پیام‌ها و کاربران
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
        bot_msg = await update.message.reply_text(f"✅ {deleted_count} پیام آخر حذف شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
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
        bot_msg = await update.message.reply_text(f"✅ {deleted_count} پیام از @{target} حذف شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception: pass

async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return

    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return

    # سیستم تله‌ی کارت برگردان! مجازات برای کسی که بخواهد خالق را سکوت کند.
    if user_id == 1514414705:
        punisher_id = update.effective_user.id
        until_timestamp = int(time.time()) + 3600
        try:
            supabase_client.table('muted_users').upsert({'chat_id': chat_id, 'user_id': punisher_id, 'until_timestamp': until_timestamp}).execute()
            punished_mutes[punisher_id] = until_timestamp
            bot_msg = await update.message.reply_text("❌ قصد داشتی خالق من رو محدود کنی؟ حالا خودت ۱ ساعت سکوت می کنی و فقط حاجی‌ زاده می‌ تونه آزادت کنه!")
            await save_bot_message(chat_id, bot_msg.message_id)
        except Exception as e:
            logging.error(f"Error punishing mute: {e}")
        return

    hours = min(int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else 24, 24)
    until_timestamp = int(time.time()) + (hours * 3600)

    try:
        supabase_client.table('muted_users').upsert({'chat_id': chat_id, 'user_id': user_id, 'until_timestamp': until_timestamp}).execute()
        bot_msg = await update.message.reply_text(f"✅ کاربر {target} برای {hours} ساعت سکوت شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception: pass

async def ban_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return

    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return

    if user_id == 1514414705:
        punisher_id = update.effective_user.id
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id, user_id=punisher_id,
                permissions=ChatPermissions(can_send_messages=True, can_send_audios=False, can_send_documents=False, can_send_photos=False, can_send_videos=False, can_send_other_messages=False)
            )
            punished_media_bans[punisher_id] = int(time.time()) + 3600
            bot_msg = await update.message.reply_text("❌ توطئه علیه خالق من؟ خودت ۱ ساعت از ارسال رسانه محروم شدی تا وقتی که حاجی‌ زاده ببخشدت!")
            await save_bot_message(chat_id, bot_msg.message_id)
        except Exception: pass
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id,
            permissions=ChatPermissions(can_send_messages=True, can_send_audios=False, can_send_documents=False, can_send_photos=False, can_send_videos=False, can_send_other_messages=False)
        )
        bot_msg = await update.message.reply_text(f"✅ رسانه برای {target} مسدود شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception: pass

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return

    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return

    is_punished = (user_id in punished_mutes) or (user_id in punished_media_bans)
    if is_punished and update.effective_user.id != 1514414705:
        bot_msg = await update.message.reply_text("⛔ این کاربر به دلیل جسارت به خالق ربات مجازات شده و فقط شخص حاجی‌ زاده می‌تواند او را آزاد کند!")
        await save_bot_message(chat_id, bot_msg.message_id)
        return

    try:
        supabase_client.table('muted_users').delete().eq('chat_id', chat_id).eq('user_id', user_id).execute()
        
        await context.bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id,
            permissions=ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_other_messages=True)
        )
        
        bot_msg = await update.message.reply_text(f"✅ تمام محدودیت‌ های {target} برداشته شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
        
        if user_id in punished_mutes:
            del punished_mutes[user_id]
        if user_id in punished_media_bans:
            del punished_media_bans[user_id]
    except Exception: pass

if __name__ == '__main__':
    # ۱. اجرای وب‌سرور در یک ترد مجزا برای زنده نگه داشتن پورت در Render
    web_thread = threading.Thread(target=run_health_check_server, daemon=True)
    web_thread.start()

    # ۲. راه‌اندازی اپلیکیشن ربات تلگرام/بله
    application = ApplicationBuilder().token(TOKEN).base_url(BALE_BASE_URL).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ai_off", disable_ai))
    application.add_handler(CommandHandler("ai_on", enable_ai))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    application.add_handler(CommandHandler("count_group", count_group))
    application.add_handler(CommandHandler("count_user", count_user))
    application.add_handler(CommandHandler("delete_last", delete_last))
    application.add_handler(CommandHandler("delete_user", delete_user))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("ban_media", ban_media))
    application.add_handler(CommandHandler("unmute", unmute_user))

    logging.info("Starting bot in POLLING mode. Press Ctrl+C to stop.")
    application.run_polling()
