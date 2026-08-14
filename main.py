import os
import logging
import re
import io
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

# پرچم فعال بودن هوش مصنوعی
ai_enabled = True

# لیست‌های حافظه مجازات
punished_mutes = {}
punished_media_bans = {}

# سرور وب برای Health Check رندر و UptimeRobot
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Ghaleb Bot is alive and healthy!")

    def log_message(self, format, *args):
        return

def run_health_check_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logging.info(f"Health check server running on port {port}")
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

def get_permanent_memories():
    try:
        res = supabase_client.table('bot_memory').select('key, value').execute()
        if res.data:
            return "\n".join([f"- {row['key']}: {row['value']}" for row in res.data])
        return "هیچ دانشی ثبت نشده است."
    except Exception as e:
        logging.warning(f"Error reading permanent memory: {e}")
        return ""

# --- هندلرهای دستورات ---
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
/count_user - تعداد پیام‌ های شما (یا کاربر خاص: `/count_user @id`)
/memories - مشاهده حافظه بلندمدت و فکت‌های ذخیره‌شده

🔸 **دستورات ادمین:**
/remember [موضوع] : [توضیحات] - سپردن نکته دائمی به حافظه ربات
/forget [موضوع] - پاک کردن موضوع از حافظه
/tagall [متن] - صدا زدن همگانی همه اعضا بر اساس شناسه کاربری
/mute [username] [hours] - سکوت کاربر برای زمان مشخص
/unmute [username] - رفع سکوت کاربر
/ban_media [username] - بستن ارسال عکس/فیلم/استیکر
/delete_last [N] - حذف N پیام آخر گروه
/delete_user [username] [N] - حذف N پیام آخر کاربر
/ai_off - خاموش کردن هوش مصنوعی
/ai_on - روشن کردن هوش مصنوعی
"""
    bot_message = await update.message.reply_text(help_text, parse_mode='Markdown')
    await save_bot_message(chat_id, bot_message.message_id)

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
        bot_msg = await update.message.reply_text(f"🧠 نکته جدید در حافظه ماندگار ثبت شد:\n📌 **{key}**: {val}", parse_mode='Markdown')
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        await update.message.reply_text(f"خطا در ذخیره‌سازی: {e}")

async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if not context.args: return

    key = " ".join(context.args).strip()
    try:
        supabase_client.table('bot_memory').delete().eq('key', key).execute()
        bot_msg = await update.message.reply_text(f"🗑️ موضوع «{key}» از حافظه من پاک شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

async def memories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    mems = get_permanent_memories()
    msg = await update.message.reply_text(f"📋 **حافظه و اطلاعات ماندگار من:**\n\n{mems}", parse_mode='Markdown')
    await save_bot_message(chat_id, msg.message_id)

# تگ کردن همگانی اختصاصی پیام‌رسان بله
async def tag_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS:
        return

    custom_text = " ".join(context.args) if context.args else "توجه همگی!"
    
    try:
        # ۱. خواندن تمام کاربران ثبت‌شده در دیتابیس
        res = supabase_client.table('messages').select('user_id, username').eq('chat_id', chat_id).neq('is_bot', True).execute()
        
        users_dict = {}  # user_id: username_or_name
        if res.data:
            for row in res.data:
                u_id = row.get('user_id')
                u_name = row.get('username') or "کاربر"
                if u_id and int(u_id) > 0:
                    users_dict[u_id] = u_name

        # ۲. اضافه کردن اعضای خاموش ثبت‌شده در حافظه (اگر وجود داشته باشد)
        try:
            mem_res = supabase_client.table('bot_memory').select('value').eq('key', 'silent_members').execute()
            if mem_res.data:
                extra_users = mem_res.data[0]['value'].replace('@', '').split()
                for u in extra_users:
                    # به عنوان یوزرنیم بدون آیدی مشخص اضافه می‌شود
                    users_dict[f"extra_{u}"] = u.strip()
        except Exception:
            pass

        if not users_dict:
            await update.message.reply_text("عضوی برای تگ کردن در دیتابیس ثبت نشده است.")
            return

        # ۳. ساخت لیست تگ سازگار با بله
        mentions_list = []
        for u_id, name in users_dict.items():
            clean_name = re.sub(r'[_*\[\]()~`>#+\-=|{}.!]', '', name).strip()
            
            # اگر یوزرنیم است
            if isinstance(u_id, str) and u_id.startswith("extra_"):
                mentions_list.append(f"@{clean_name}")
            else:
                # تگ متنی با لینک پروتکل بله
                mentions_list.append(f"[{clean_name}](ble://user?id={u_id})")

        # ۴. ارسال دسته‌های ۱۰تایی در بله
        chunks = [mentions_list[i:i + 10] for i in range(0, len(mentions_list), 10)]
        for chunk in chunks:
            mentions_str = "  ".join(chunk)
            full_msg = f"📢 **{custom_text}**\n\n{mentions_str}"
            bot_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=full_msg,
                parse_mode='Markdown'
            )
            await save_bot_message(chat_id, bot_msg.message_id)

    except Exception as e:
        logging.error(f"Error in Bale tag_all: {e}")
        await update.message.reply_text(f"خطا در اجرای دستور: {e}")

async def disable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    ai_enabled = False
    bot_msg = await update.message.reply_text("🛑 هوش مصنوعی *خاموش* شد.", parse_mode='Markdown')
    await save_bot_message(chat_id, bot_msg.message_id)

async def enable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    ai_enabled = True
    bot_msg = await update.message.reply_text("✅ هوش مصنوعی *روشن* شد.", parse_mode='Markdown')
    await save_bot_message(chat_id, bot_msg.message_id)

# --- هندلر پیام‌ها، عکس‌ها و هوش مصنوعی ---
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    display_title = update.effective_user.first_name or update.effective_user.username or "کاربر"
    username = (update.effective_user.username or update.effective_user.first_name).lower() 
    message_id = update.message.message_id
    text = update.message.text or update.message.caption or ""

    # ۱. ذخیره پیام
    await save_message(user_id, display_title, chat_id, message_id, text if text else "[تصویر/مدیا]")

    # ۲. بررسی وضعیت میوت
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

    # ۳. فیلتر کلمات رکیک
    if text:
        bad_words = ["کیر", "کون", "کص", "ک.ی.ر", "ک.و.ن", "ک.ص", "ک.یر", "کی.ر", "ک.ر"]
        text_no_spaces = text.replace(".", "")
        for bw in bad_words:
            if bw in text_no_spaces:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                    logging.info(f"Deleted bad word message: {message_id}")
                except Exception as e:
                    logging.error(f"Failed to delete bad word: {e}")
                return 

    # ۴. بررسی عکس و ریپلای
    is_reply_to_bot = False
    replied_text = ""
    target_photo = None

    if update.message.reply_to_message:
        if update.message.reply_to_message.from_user and update.message.reply_to_message.from_user.id == context.bot.id:
            is_reply_to_bot = True
        replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        if update.message.reply_to_message.photo:
            target_photo = update.message.reply_to_message.photo[-1]

    if update.message.photo:
        target_photo = update.message.photo[-1]

    has_trigger_word = "غالب" in text or "گالب" in text
    has_photo = target_photo is not None

    # ۵. پاسخ هوش مصنوعی
    if ai_enabled and (is_reply_to_bot or has_trigger_word or (has_photo and is_reply_to_bot)):
        try:
            # دریافت سابقه پیام‌های گروه
            history_context = ""
            try:
                recent_msgs = supabase_client.table('messages').select('username, text').eq('chat_id', chat_id).order('timestamp', desc=True).limit(8).execute()
                if recent_msgs.data:
                    chat_history = []
                    for m in reversed(recent_msgs.data):
                        msg_t = m.get('text') or "[مدیا]"
                        chat_history.append(f"{m['username']}: {msg_t}")
                    history_context = "\n".join(chat_history)
            except Exception as e:
                logging.warning(f"Failed to fetch history: {e}")

            permanent_knowledge = get_permanent_memories()

            system_instruction = f"""تو «غالب» هستی؛ دستیار هوش مصنوعی هوشمند، کاردرست، محترم و خوش‌برخورد برای گروه «غالبون».
سازنده و برنامه‌نویس تو «محمد مهدی حاجی زاده» (@mmhajizadeh) است و مدیران گروه «شادکام» و «عشقی» هستند. با مدیران و اعضا با ادب، احترام و لحنی متین و نیمه‌صمیمی صحبت کن.

اطلاعات و حافظه دائمی تو درباره اعضا و قوانین:
{permanent_knowledge}

دستورالعمل‌های حیاتی:
۱. فقط و فقط به زبان فارسی سلیس و روان پاسخ بده.
۲. شوخ‌طبعی و صمیمیت را بسیار ملایم و کنترل‌شده نگه دار و اصلاً در شوخی زیاده‌روی نکن.
۳. حتماً پاسخ خود را دقیقاً با این ساختار آغاز کن: [REACTION: 💡] و به جای لامپ یک ایموجی مناسب بگذار.
۴. اگر تصویری ارسال شده، تمام جزییات، متن‌ها و عناصر تصویر را دقیق و با متانت تحلیل کن.
۵. پاسخ‌ها کوتاه، شسته‌رفته و مفید باشند.
"""

            user_query = text if text else "لطفاً این تصویر را با دقت نگاه کن، تحلیلش کن و نظرت را بگو."
            
            input_text = f"{system_instruction}\n\n"
            if history_context:
                input_text += f"--- سابقه پیام‌های گروه ---\n{history_context}\n\n"
            if replied_text:
                input_text += f"--- پیامی که به آن ریپلای شده ---\n{replied_text}\n\n"
            input_text += f"--- پیام فعلی کاربر ({display_title}) ---\n{user_query}"

            # دریافت مستقیم بایت‌های تصویر از API بله
            image_bytes = None
            if has_photo and target_photo:
                try:
                    file_obj = await context.bot.get_file(target_photo.file_id)
                    file_path = file_obj.file_path
                    
                    # هندل کردن ساختار آدرس دانلود فایل در بله
                    if file_path.startswith("http"):
                        download_url = file_path
                    else:
                        download_url = f"https://tapi.bale.ai/file/bot{TOKEN}/{file_path}"
                    
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp = await client.get(download_url)
                        if resp.status_code == 200 and len(resp.content) > 0:
                            image_bytes = resp.content
                            logging.info(f"Successfully fetched image: {len(image_bytes)} bytes")
                except Exception as img_err:
                    logging.error(f"Image fetch failed: {img_err}")

            # ارسال ورودی به Gemini
            if image_bytes:
                prompt_input = [
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    input_text
                ]
            else:
                prompt_input = input_text

            interaction = gemini_client.interactions.create(
                model="gemini-3.5-flash-lite",
                input=prompt_input
            )

            ai_response = interaction.output_text.strip() if interaction.output_text else ""

            # استخراج و اعمال ری‌اکشن
            reaction_match = re.search(r'\[REACTION:\s*(.+?)\]', ai_response)
            reaction_emoji = "🤖"
            if reaction_match:
                reaction_emoji = reaction_match.group(1).strip()
                ai_response = ai_response.replace(reaction_match.group(0), "").strip()

            try:
                await context.bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=reaction_emoji)
            except Exception:
                pass

            current_time = time.time()
            last_time = ghaleb_last_reply.get(user_id, 0)
            if current_time - last_time > 4 and ai_response:
                bot_msg = await update.message.reply_text(ai_response, reply_to_message_id=message_id)
                await save_bot_message(chat_id, bot_msg.message_id)
                ghaleb_last_reply[user_id] = current_time

        except Exception as e:
            logging.error(f"Gemini Processing Error: {e}")

# --- دستورات آماری و مدیریتی ---
async def count_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        res = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).execute()
        msg = await update.message.reply_text(f"📊 تعداد کل پیام‌ های ثبت‌ شده: {res.count}")
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
    except Exception: pass

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        res = supabase_client.table('messages').select('username').eq('chat_id', chat_id).neq('is_bot', True).execute()
        if not res.data: return
        from collections import Counter
        counts = Counter(m['username'] for m in res.data if m['username'])
        top_users = counts.most_common(5)
        report = f"📈 **آمار گروه:**\n\n💬 تعداد کل پیام‌ ها: {len(res.data)}\n\n🏆 **۵ کاربر فعال برتر:**\n"
        for i, (u, c) in enumerate(top_users, 1):
            report += f"{i}. @{u} : {c} پیام\n"
        msg = await update.message.reply_text(report, parse_mode='Markdown')
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e:
        logging.error(f"Error stats: {e}")

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

    if user_id == 1514414705:
        punisher_id = update.effective_user.id
        until_timestamp = int(time.time()) + 3600
        try:
            supabase_client.table('muted_users').upsert({'chat_id': chat_id, 'user_id': punisher_id, 'until_timestamp': until_timestamp}).execute()
            punished_mutes[punisher_id] = until_timestamp
            bot_msg = await update.message.reply_text("❌ قصد داشتی خالق من رو محدود کنی؟ حالا خودت ۱ ساعت سکوت می کنی و فقط حاجی‌ زاده می‌ تونه آزادت کنه!")
            await save_bot_message(chat_id, bot_msg.message_id)
        except Exception: pass
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
        if user_id in punished_mutes: del punished_mutes[user_id]
        if user_id in punished_media_bans: del punished_media_bans[user_id]
    except Exception: pass

if __name__ == '__main__':
    # اجرای وب‌سرور برای زنده نگه‌داشتن کانتینر
    web_thread = threading.Thread(target=run_health_check_server, daemon=True)
    web_thread.start()

    # ساخت اپلیکیشن
    application = ApplicationBuilder().token(TOKEN).base_url(BALE_BASE_URL).build()

    # ثبت هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("remember", remember_command))
    application.add_handler(CommandHandler("forget", forget_command))
    application.add_handler(CommandHandler("memories", memories_command))
    application.add_handler(CommandHandler("tagall", tag_all))
    application.add_handler(CommandHandler("ai_off", disable_ai))
    application.add_handler(CommandHandler("ai_on", enable_ai))
    application.add_handler(CommandHandler("count_group", count_group))
    application.add_handler(CommandHandler("count_user", count_user))
    application.add_handler(CommandHandler("delete_last", delete_last))
    application.add_handler(CommandHandler("delete_user", delete_user))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("ban_media", ban_media))
    application.add_handler(CommandHandler("unmute", unmute_user))

    # پشتیبانی از پیام‌های متنی و تصاویر
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_messages))

    logging.info("Starting bot in POLLING mode. Press Ctrl+C to stop.")
    application.run_polling()
