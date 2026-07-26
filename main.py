import os
import logging
import asyncpg
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import time
from telegram.ext import MessageHandler, filters
from telegram import Update, ChatPermissions
from groq import AsyncGroq

# بارگذاری متغیرهای محیطی از فایل .env
load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# دریافت اطلاعات از فایل .env
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
AI_API_KEY = os.getenv('AI_API_KEY')

# راه‌اندازی کلاینت Groq
groq_client = AsyncGroq(api_key=AI_API_KEY)

ALLOWED_USERS = [1514414705, 941154813, 1219981601]

# آدرس سرور پیام‌ رسان بله
BALE_BASE_URL = "https://tapi.bale.ai/bot"

async def init_db():
    logging.info("Connecting to the database...")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                username TEXT,
                message_id BIGINT NOT NULL,
                timestamp TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS muted_users (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                until_timestamp BIGINT NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
        ''')
        logging.info("Database tables initialized successfully.")
        await conn.close()
    except Exception as e:
        logging.error(f"Database connection failed: {e}")

# تابع کمکی برای ذخیره پیام‌های ارسالی خود ربات در دیتابیس
async def save_bot_message(chat_id, message_id):
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        # آیدی کاربر را 0 و نام را Bot می‌گذاریم
        await conn.execute('''
            INSERT INTO messages (chat_id, user_id, username, message_id)
            VALUES ($1, $2, $3, $4)
        ''', chat_id, 0, 'Bot', message_id)
        await conn.close()
    except Exception as e:
        logging.error(f"Failed to save bot message: {e}")

# تابع کمکی برای پیدا کردن آیدی عددی کاربر از روی یوزرنیم
async def get_user_id_by_username(username):
    username = username.replace('@', '').lower()
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        user_id = await conn.fetchval('SELECT user_id FROM messages WHERE chat_id = $1 AND LOWER(username) = $2 LIMIT 1', username)
        await conn.close()
        return user_id
    except Exception as e:
        logging.error(f"Error fetching user_id: {e}")
        return None

# هندلر دستور /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user.first_name
    bot_message = await update.message.reply_text(f"🤖 سلام {user}! ربات مدیریت گروه بله با موفقیت در حالت لوکال راه‌ اندازی شد.")
    
    # آیدی این پیام را به دیتابیس می‌فرستیم
    await save_bot_message(chat_id, bot_message.message_id)

# اجرای تابع دیتابیس در زمان استارت ربات
async def post_init(application):
    await init_db()

ghaleb_last_reply = {}

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ۱. جلوگیری از خطای AttributeError در صورت خالی بودن update.message (مثلا هنگام ادیت پیام)
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # اگر کاربر یوزرنیم نداشت، نام اولش رو می‌گیریم
    username = (update.effective_user.username or update.effective_user.first_name).lower() 
    message_id = update.message.message_id
    
    # متن پیام (اگر استیکر یا عکس باشه، متن خالی در نظر گرفته میشه)
    text = update.message.text or ""

    # ۲. ذخیره پیام در دیتابیس
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('''
            INSERT INTO messages (chat_id, user_id, username, message_id)
            VALUES ($1, $2, $3, $4)
        ''', chat_id, user_id, username, message_id)
        logging.info(f"Message {message_id} from {username} saved.")
        await conn.close()
    except Exception as e:
        logging.error(f"Database insertion failed: {e}")

    # ۳. بررسی هوش مصنوعی (سانسور و پاسخ هوشمند)
    if text:
        try:
            # تعریف شخصیت و قوانین برای مدل
            system_prompt = """تو یک دستیار هوشمند، مودب و ناظر امنیتی گروه چت غالبون هستی.
            فقط یکی از سه کار زیر را انجام بده:
            ۱. اگر متن کاربر حاوی فحاشی، توهین رکیک، کلمات زننده یا نامناسب بود، فقط و فقط بنویس: [DELETE]
            ۲. اگر پیام توهین نداشت، اما کاربر در متن از کلمه "غالب" استفاده کرده بود یا صراحتاً با تو حرف زده بود، یک جواب کوتاه، جذاب و دوستانه به زبان فارسی بده.
            ۳. در غیر این صورت (اگر پیام عادی بود و ربطی به تو نداشت)، فقط و فقط بنویس: [PASS]
            نکته مهم: هیچ توضیح اضافه‌ای نده."""

            completion = await groq_client.chat.completions.create(
                model="llama-3.1-8b-instant", # مدل بسیار سریع و سبک
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.5,
                max_tokens=150
            )
            
            ai_response = completion.choices[0].message.content.strip()

            # بررسی تصمیم هوش مصنوعی
            if "[DELETE]" in ai_response:
                # پیام حاوی توهین بوده است -> حذف پیام
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                logging.info(f"Message {message_id} from {username} deleted by AI filter.")
                return # توقف پردازش و خروج
                
            elif "[PASS]" not in ai_response:
                # هوش مصنوعی یک جواب هوشمندانه تولید کرده است
                current_time = time.time()
                last_time = ghaleb_last_reply.get(user_id, 0) 
                
                # اعمال محدودیت زمانی برای جلوگیری از اسپم شدن ربات (همان ۲۰ ثانیه)
                if current_time - last_time > 20:
                    bot_msg = await update.message.reply_text(ai_response, reply_to_message_id=message_id)
                    await save_bot_message(chat_id, bot_msg.message_id)
                    ghaleb_last_reply[user_id] = current_time

        except Exception as e:
            logging.error(f"AI API Error: {e}")
    
    # چک کردن وضعیت میوت کاربر
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        muted_until = await conn.fetchval(
            'SELECT until_timestamp FROM muted_users WHERE chat_id = $1 AND user_id = $2', 
            chat_id, user_id
        )
        await conn.close()

        if muted_until:
            current_time = int(time.time())
            if current_time < muted_until:
                # کاربر هنوز میوت است -> پاک کردن پیام
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                return
            else:
                # زمان میوت تمام شده -> حذف از دیتابیس
                conn = await asyncpg.connect(DATABASE_URL)
                await conn.execute('DELETE FROM muted_users WHERE chat_id = $1 AND user_id = $2', chat_id, user_id)
                await conn.close()
    except Exception as e:
        logging.error(f"Error checking mute status: {e}")

# هندلر شمارش کل پیام‌های گروه
async def count_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        # شمردن تمام ردیف ‌هایی که آیدی گروهشون با گروه فعلی یکیه
        count = await conn.fetchval('SELECT COUNT(*) FROM messages WHERE chat_id = $1', chat_id)
        await conn.close()
        
        bot_msg = await update.message.reply_text(f"📊 تعداد کل پیام‌های ثبت شده گروه تا این لحظه: {count}")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        logging.error(f"Error in count_group: {e}")
        bot_msg = await update.message.reply_text("❌ خطایی در ارتباط با دیتابیس رخ داد.")
        await save_bot_message(chat_id, bot_msg.message_id)

# هندلر شمارش پیام‌های یک کاربر (خودش یا شخص دیگر)
async def count_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # اگر کاربر جلوی دستور چیزی نوشته بود (مثلا /count_user @ali)
    if context.args:
        # حذف کاراکتر @ در صورت وجود
        target_username = context.args[0].replace('@', '').lower()
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM messages WHERE chat_id = $1 AND LOWER(username) = $2', 
                chat_id, target_username
            )
            await conn.close()
            
            bot_msg = await update.message.reply_text(f"👤 تعداد پیام‌ های @{target_username} در این گروه: {count}")
            await save_bot_message(chat_id, bot_msg.message_id)
        except Exception as e:
            logging.error(f"Error in count_user (target): {e}")
            
    # اگر جلوی دستور چیزی نبود (شمارش پیام‌های خودش)
    else:
        user_id = update.effective_user.id
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM messages WHERE chat_id = $1 AND user_id = $2', 
                chat_id, user_id
            )
            await conn.close()
            
            bot_msg = await update.message.reply_text(f"👤 شما تا به حال {count} پیام در این گروه ارسال کرده‌ اید.")
            await save_bot_message(chat_id, bot_msg.message_id)
        except Exception as e:
            logging.error(f"Error in count_user (self): {e}")

# هندلر حذف N پیام آخر گروه
async def delete_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if update.effective_user.id not in ALLOWED_USERS:
        bot_msg = await update.message.reply_text("⛔ شما دسترسی لازم برای این دستور را ندارید.")
        await save_bot_message(chat_id, bot_msg.message_id)
        return
    
    # بررسی اینکه آیا کاربر تعداد را وارد کرده یا نه (مثلا /delete_last 10)
    if not context.args or not context.args[0].isdigit():
        bot_msg = await update.message.reply_text("❌ لطفاً تعداد پیام را وارد کنید. مثال: /delete_last 10")
        await save_bot_message(chat_id, bot_msg.message_id)
        return
        
    limit = int(context.args[0])
    if limit > 1000:
        limit = 1000 # اعمال محدودیت ۱۰۰۰ تایی طبق مستندات
        
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        # گرفتن آیدی پیام‌ های آخر گروه به ترتیب زمان (جدیدترین‌ها)
        records = await conn.fetch('''
            SELECT id, message_id FROM messages 
            WHERE chat_id = $1 
            ORDER BY timestamp DESC, id DESC LIMIT $2
        ''', chat_id, limit)
        
        deleted_count = 0
        for record in records:
            try:
                # ۱. حذف پیام از گروه بله
                await context.bot.delete_message(chat_id=chat_id, message_id=record['message_id'])
                # ۲. حذف پیام از دیتابیس خودمان
                await conn.execute('DELETE FROM messages WHERE id = $1', record['id'])
                deleted_count += 1
            except Exception as e:
                logging.warning(f"Could not delete message {record['message_id']} from Bale: {e}")
                
        await conn.close()
        bot_msg = await update.message.reply_text(f"✅ تعداد {deleted_count} پیام آخر گروه با موفقیت حذف شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        logging.error(f"Error in delete_last: {e}")
        bot_msg = await update.message.reply_text("❌ خطایی رخ داد. آیا من در گروه ادمین هستم؟")
        await save_bot_message(chat_id, bot_msg.message_id)

# هندلر حذف N پیام آخر یک کاربر خاص
async def delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if update.effective_user.id not in ALLOWED_USERS:
        bot_msg = await update.message.reply_text("⛔ شما دسترسی لازم برای این دستور را ندارید.")
        await save_bot_message(chat_id, bot_msg.message_id)
        return
    
    # بررسی ورودی‌ها (باید حداقل ۲ کلمه باشد: یوزرنیم و تعداد)
    if len(context.args) < 2 or not context.args[1].isdigit():
        bot_msg = await update.message.reply_text("❌ فرمت اشتباه است. مثال: /delete_user @mmhajizadeh 5")
        await save_bot_message(chat_id, bot_msg.message_id)
        return
        
    target_username = context.args[0].replace('@', '').lower()
    limit = int(context.args[1])
    if limit > 1000:
        limit = 1000
        
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        records = await conn.fetch('''
            SELECT id, message_id FROM messages 
            WHERE chat_id = $1 AND LOWER(username) = $2
            ORDER BY timestamp DESC, id DESC LIMIT $3
        ''', chat_id, target_username, limit)
        
        deleted_count = 0
        for record in records:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=record['message_id'])
                await conn.execute('DELETE FROM messages WHERE id = $1', record['id'])
                deleted_count += 1
            except Exception as e:
                logging.warning(f"Could not delete message {record['message_id']} from Bale: {e}")
                
        await conn.close()
        bot_msg = await update.message.reply_text(f"✅ تعداد {deleted_count} پیام آخر از کاربر @{target_username} حذف شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        logging.error(f"Error in delete_user: {e}")
        bot_msg = await update.message.reply_text("❌ خطایی رخ داد.")
        await save_bot_message(chat_id, bot_msg.message_id)

# هندلر نمایش آمار ۵ کاربر برتر
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # کنترل دسترسی: فقط کاربران مجاز
    if user_id not in ALLOWED_USERS:
        bot_msg = await update.message.reply_text("⛔ شما دسترسی لازم برای این دستور را ندارید.")
        await save_bot_message(chat_id, bot_msg.message_id)
        return

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        # استخراج کل پیام‌ها
        total_msgs = await conn.fetchval('SELECT COUNT(*) FROM messages WHERE chat_id = $1', chat_id)
        
        # استخراج ۵ کاربر برتر (ربات را با شرط user_id != 0 حذف می‌کنیم)
        top_users = await conn.fetch('''
            SELECT username, COUNT(*) as msg_count 
            FROM messages 
            WHERE chat_id = $1 AND user_id != 0 
            GROUP BY username 
            ORDER BY msg_count DESC 
            LIMIT 5
        ''', chat_id)
        
        await conn.close()
        
        # ساختن متن گزارش
        report = f"📊 آمار کلی گروه:\nتعداد کل پیام ‌ها: {total_msgs}\n\n🏆 ۵ کاربر فعال برتر:\n"
        for i, user in enumerate(top_users, 1):
            report += f"{i}. {user['username']}: {user['msg_count']} پیام\n"
            
        bot_msg = await update.message.reply_text(report)
        await save_bot_message(chat_id, bot_msg.message_id)
        
    except Exception as e:
        logging.error(f"Error in stats: {e}")
        bot_msg = await update.message.reply_text("❌ خطایی در گرفتن آمار رخ داد.")
        await save_bot_message(chat_id, bot_msg.message_id)

# هندلر میوت کردن کاربر (سکوت اجباری)
async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if update.effective_user.id not in ALLOWED_USERS:
        bot_msg = await update.message.reply_text("⛔ شما دسترسی لازم برای این دستور را ندارید.")
        await save_bot_message(chat_id, bot_msg.message_id)
        return

    if len(context.args) < 1:
        bot_msg = await update.message.reply_text("❌ فرمت اشتباه است. مثال: /mute @username 2")
        await save_bot_message(chat_id, bot_msg.message_id)
        return

    target_username = context.args[0]
    user_id = await get_user_id_by_username(target_username)
    
    if not user_id:
        bot_msg = await update.message.reply_text("❌ کاربر در دیتابیس یافت نشد.")
        await save_bot_message(chat_id, bot_msg.message_id)
        return

    hours = 24
    if len(context.args) > 1 and context.args[1].isdigit():
        hours = int(context.args[1])
        if hours > 24:
            hours = 24

    until_timestamp = int(time.time()) + (hours * 3600)

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('''
            INSERT INTO muted_users (chat_id, user_id, until_timestamp)
            VALUES ($1, $2, $3)
            ON CONFLICT (chat_id, user_id) 
            DO UPDATE SET until_timestamp = $3
        ''', chat_id, user_id, until_timestamp)
        await conn.close()

        bot_msg = await update.message.reply_text(f"✅ کاربر {target_username} برای {hours} ساعت سکوت شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        logging.error(f"Error in mute: {e}")
        bot_msg = await update.message.reply_text("❌ خطایی در ثبت سکوت رخ داد.")
        await save_bot_message(chat_id, bot_msg.message_id)

# هندلر بستن قابلیت ارسال گیف، استیکر و رسانه
async def ban_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if update.effective_user.id not in ALLOWED_USERS:
        bot_msg = await update.message.reply_text("⛔ شما دسترسی لازم را ندارید.")
        await save_bot_message(chat_id, bot_msg.message_id)
        return

    if len(context.args) < 1:
        bot_msg = await update.message.reply_text("❌ فرمت اشتباه است. مثال: /ban_media @username")
        await save_bot_message(chat_id, bot_msg.message_id)
        return

    target_username = context.args[0]
    user_id = await get_user_id_by_username(target_username)
    
    if not user_id:
        bot_msg = await update.message.reply_text("❌ کاربر در دیتابیس یافت نشد.")
        await save_bot_message(chat_id, bot_msg.message_id)
        return

    try:
        # فقط اجازه ارسال متن می‌دهیم و بقیه موارد (عکس، فیلم، استیکر و گیف) مسدود می‌شوند
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_other_messages=False # این گزینه استیکر و گیف را مسدود می‌کند
            )
        )
        bot_msg = await update.message.reply_text(f"✅ ارسال رسانه، استیکر و گیف برای کاربر {target_username} مسدود شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        logging.error(f"Error in ban_media: {e}")
        bot_msg = await update.message.reply_text("❌ خطا در اعمال محدودیت رسانه.")
        await save_bot_message(chat_id, bot_msg.message_id)

# هندلر رفع محدودیت (آن‌میوت کردن)
async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if update.effective_user.id not in ALLOWED_USERS:
        return

    if len(context.args) < 1:
        return

    target_username = context.args[0]
    user_id = await get_user_id_by_username(target_username)
    
    if not user_id:
        bot_msg = await update.message.reply_text("❌ کاربر در دیتابیس یافت نشد.")
        await save_bot_message(chat_id, bot_msg.message_id)
        return

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('DELETE FROM muted_users WHERE chat_id = $1 AND user_id = $2', chat_id, user_id)
        await conn.close()

        bot_msg = await update.message.reply_text(f"✅ تمام محدودیت‌های کاربر {target_username} برداشته شد.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        logging.error(f"Error in unmute: {e}")

# بدنه اصلی برنامه
if __name__ == '__main__':
    # ساخت اپلیکیشن با توکن و بیس‌ یوآرال بله
    application = ApplicationBuilder().token(TOKEN).base_url(BALE_BASE_URL).post_init(post_init).build()

    # اضافه کردن دستورات به ربات
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

    # تعیین محیط اجرا (لوکال یا سرور) با چک کردن متغیر WEBHOOK_URL
    WEBHOOK_URL = os.getenv('WEBHOOK_URL')

    if WEBHOOK_URL:
        # حالت سرور (اجرا روی Render)
        PORT = int(os.environ.get('PORT', 10000))
        logging.info(f"Starting bot in WEBHOOK mode on port {PORT}")
        application.run_webhook(
            listen='0.0.0.0',
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/webhook",
            url_path='webhook'
        )
    else:
        # حالت لوکال (اجرا روی سیستم شما)
        logging.info("Starting bot in POLLING mode. Press Ctrl+C to stop.")
        application.run_polling()