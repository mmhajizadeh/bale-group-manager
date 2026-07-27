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

# متغیر سراسری برای روشن/خاموش کردن هوش مصنوعی
ai_enabled = True

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
/stats - نمایش ۵ کاربر برتر و آمار کل پیام‌ها
/count_group - شمارش کل پیام‌های گروه
/count_user - تعداد پیام‌های شما (یا کاربری خاص: `/count_user @id`)

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

    # ۳. فیلتر هوش مصنوعی و پاسخ‌دهی (فقط در صورت روشن بودن)
    if text and ai_enabled:
        try:
            # پرامپت انگلیسی بسیار دقیق برای جلوگیری از رفتار اشتباه
            system_prompt = """You are 'Ghaleb', a highly intelligent, polite, and slightly humorous AI assistant for the 'Ghaleboun' chat group.
            Your creator is Mohammad Mahdi Hajizadeh (@mmhajizadeh). The group admins are Shadkam and Eshghi. You must treat them with the utmost respect and apologize if they are upset.
            You are highly capable of analyzing news, participating in scientific discussions, and helping members. Always respond in fluent, engaging Persian.

            CRITICAL RULES FOR YOUR RESPONSE (Follow strictly):
            1. CENSORSHIP (DELETE): If the user's message contains explicit severe Persian profanity (e.g., "کیر", "کون", "کص" or their variations like "ک.ی.ر"), you MUST output EXACTLY and ONLY the word "[DELETE]". Do not censor normal arguments, mild anger, or regular words. Be extremely lenient unless it is a severe swear word.
            2. IGNORE (PASS): If the message is NOT profane, AND does NOT explicitly mention your name ("غالب"), AND is NOT a direct question addressed to you, you MUST output EXACTLY and ONLY the word "[PASS]".
            3. REPLY: If the message is NOT profane, AND the user explicitly mentions your name ("غالب") or directly talks to you, provide a high-quality, thoughtful, and engaging response in Persian based on your persona.
            """

            completion = await groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile", # تغییر مدل به نسخه 70 میلیاردی بسیار هوشمند
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.5,
                max_tokens=300
            )
            
            ai_response = completion.choices[0].message.content.strip()

            if "[DELETE]" in ai_response:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                logging.info(f"Deleted by AI filter: {message_id}")
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

# آمار و ارقام
async def count_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        response = supabase_client.table('messages').select('id', count='exact').eq('chat_id', chat_id).execute()
        msg = await update.message.reply_text(f"📊 تعداد کل پیام ‌های ثبت‌ شده: {response.count}")
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
        top_users = counts.most_common(5) # دریافت ۵ کاربر برتر

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

    try:
        supabase_client.table('muted_users').delete().eq('chat_id', chat_id).eq('user_id', user_id).execute()
        bot_msg = await update.message.reply_text(f"✅ محدودیت‌ های {target} برداشته شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception: pass

if __name__ == '__main__':
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

    WEBHOOK_URL = os.getenv('WEBHOOK_URL')

    if WEBHOOK_URL:
        PORT = int(os.environ.get('PORT', 10000))
        logging.info(f"Starting bot in WEBHOOK mode on port {PORT}")
        application.run_webhook(listen='0.0.0.0', port=PORT, webhook_url=f"{WEBHOOK_URL}/webhook", url_path='webhook')
    else:
        logging.info("Starting bot in POLLING mode. Press Ctrl+C to stop.")
        application.run_polling()