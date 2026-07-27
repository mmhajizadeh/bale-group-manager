import os
import logging
from dotenv import load_dotenv
from telegram import Update, ChatPermissions
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
import time
from groq import AsyncGroq
from supabase import create_client, Client

# بارگذاری متغیرهای محیطی
load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
AI_API_KEY = os.getenv('AI_API_KEY')

# راه‌اندازی کلاینت‌ها
groq_client = AsyncGroq(api_key=AI_API_KEY)
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ALLOWED_USERS = [1514414705, 941154813, 1219981601]
ghaleb_last_reply = {}
BALE_BASE_URL = "https://tapi.bale.ai/bot"

# تابع ذخیره پیام‌ها
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

# دریافت آیدی از روی یوزرنیم
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user.first_name
    bot_message = await update.message.reply_text(f"🤖 سلام {user}! ربات مدیریت با دیتابیس جدید با موفقیت راه‌ اندازی شد.")
    await save_bot_message(chat_id, bot_message.message_id)

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
    logging.info(f"Message {message_id} from {username} saved.")

    # ۲. بررسی میوت بودن کاربر
    try:
        mute_res = supabase_client.table('muted_users').select('until_timestamp').eq('chat_id', chat_id).eq('user_id', user_id).execute()
        if mute_res.data:
            muted_until = mute_res.data[0]['until_timestamp']
            current_time = int(time.time())
            if current_time < muted_until:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                return
            else:
                supabase_client.table('muted_users').delete().eq('chat_id', chat_id).eq('user_id', user_id).execute()
    except Exception as e:
        logging.error(f"Error checking mute: {e}")

    # ۳. فیلتر هوش مصنوعی و پاسخ‌دهی
    if text:
        try:
            # پرامپت اصلاح شده با لحن صمیمی و فیلتر دقیق
            system_prompt = """تو یک دستیار هوشمند، ناظر گروه، مودب، صمیمی و کمی شوخ‌طبع برای گروه چت "غالبون" هستی.
            فقط یکی از سه کار زیر را انجام بده:
            ۱. اگر متن کاربر دقیقاً شامل الفاظ رکیک مشخص مانند "کیر"، "کون"، "کص" (یا شکل‌های مخفی شده آن‌ها مثل "ک.ی.ر"، "ک ص"، "ک-ی-ر") بود، فقط و فقط بنویس: [DELETE]
            نکته بسیار مهم: برای انتقادات، کلمات منفی عادی یا کل‌کل‌های دوستانه به هیچ وجه پیام را پاک نکن. اصلاً سخت‌گیر نباش!
            ۲. اگر پیام رکیک نبود، اما کاربر در متن از کلمه "غالب" استفاده کرده بود یا مستقیماً از تو سوال پرسیده بود، یک جواب کوتاه، صمیمی، کمی شوخ و با جمله‌بندی روان و جذاب به زبان فارسی بده.
            ۳. در غیر این صورت (پیام‌های عادی که کلمات ممنوعه ندارند و به تو هم ربطی ندارند)، فقط و فقط بنویس: [PASS]
            نکته: هیچ توضیح اضافه‌ای نده."""

            completion = await groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.6,
                max_tokens=150
            )
            
            ai_response = completion.choices[0].message.content.strip()

            if "[DELETE]" in ai_response:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                logging.info(f"Deleted by AI filter.")
                return
                
            elif "[PASS]" not in ai_response:
                current_time = time.time()
                last_time = ghaleb_last_reply.get(user_id, 0) 
                
                if current_time - last_time > 20:
                    bot_msg = await update.message.reply_text(ai_response, reply_to_message_id=message_id)
                    await save_bot_message(chat_id, bot_msg.message_id)
                    ghaleb_last_reply[user_id] = current_time

        except Exception as e:
            logging.error(f"AI API Error: {e}")

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
    except Exception as e:
        logging.error(f"Error count_user: {e}")

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
    except Exception as e:
        logging.error(f"Error delete_last: {e}")

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
    except Exception as e:
        logging.error(f"Error delete_user: {e}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        response = supabase_client.table('messages').select('username').eq('chat_id', chat_id).neq('is_bot', True).execute()
        messages = response.data
        if not messages: return
        
        from collections import Counter
        counts = Counter(m['username'] for m in messages if m['username'])
        top_user, top_count = counts.most_common(1)[0]

        report = f"📈 **آمار گروه:**\n\n💬 تعداد کل پیام‌ ها: {len(messages)}\n👑 فعال‌ترین کاربر: @{top_user} با {top_count} پیام"
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
        bot_msg = await update.message.reply_text(f"✅ کاربر {target} برای {hours} ساعت سکوت شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        logging.error(f"Error mute: {e}")

async def ban_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return

    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id,
            permissions=ChatPermissions(can_send_messages=True, can_send_audios=False, can_send_documents=False, can_send_photos=False, can_send_videos=False, can_send_other_messages=False)
        )
        bot_msg = await update.message.reply_text(f"✅ رسانه برای {target} مسدود شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        logging.error(f"Error ban_media: {e}")

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return

    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return

    try:
        supabase_client.table('muted_users').delete().eq('chat_id', chat_id).eq('user_id', user_id).execute()
        bot_msg = await update.message.reply_text(f"✅ محدودیت‌ های {target} برداشته شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        logging.error(f"Error unmute: {e}")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).base_url(BALE_BASE_URL).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    application.add_handler(CommandHandler("count_group", count_group))
    application.add_handler(CommandHandler("count_user", count_user))
    application.add_handler(CommandHandler("delete_last", delete_last))
    application.add_handler(CommandHandler("delete_user", delete_user))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("ban_media", ban_media))
    application.add_handler(CommandHandler("unmute", unmute_user))

    WEBHOOK_URL = os.getenv('WEBHOOK_URL')

    if WEBHOOK_URL:
        PORT = int(os.environ.get('PORT', 10000))
        logging.info(f"Starting bot in WEBHOOK mode on port {PORT}")
        application.run_webhook(listen='0.0.0.0', port=PORT, webhook_url=f"{WEBHOOK_URL}/webhook", url_path='webhook')
    else:
        logging.info("Starting bot in POLLING mode. Press Ctrl+C to stop.")
        application.run_polling()