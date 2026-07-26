import os
import logging
import asyncpg
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import time
from telegram.ext import MessageHandler, filters

# بارگذاری متغیرهای محیطی از فایل .env
load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# دریافت اطلاعات از فایل .env
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# آدرس سرور پیام‌ رسان بله
BALE_BASE_URL = "https://tapi.bale.ai/bot"

# تابع اتصال و ایجاد جدول دیتابیس
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
            )
        ''')
        logging.info("Database 'messages' table initialized successfully.")
        await conn.close()
    except Exception as e:
        logging.error(f"Database connection failed: {e}")

# هندلر دستور /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    await update.message.reply_text(f"🤖 سلام {user}! ربات مدیریت گروه بله با موفقیت در حالت لوکال راه‌ اندازی شد.")

# اجرای تابع دیتابیس در زمان استارت ربات
async def post_init(application):
    await init_db()

ghaleb_last_reply = {}

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # اگر کاربر یوزرنیم نداشت، نام اولش رو می‌گیریم
    username = update.effective_user.username or update.effective_user.first_name 
    message_id = update.message.message_id
    
    # متن پیام (اگر استیکر یا عکس باشه، متن خالی در نظر گرفته میشه)
    text = update.message.text or ""

    # ۲. ذخیره پیام در دیتابیس
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        # استفاده از 1$ و 2$ برای جلوگیری از باگ‌های امنیتی (SQL Injection) است
        await conn.execute('''
            INSERT INTO messages (chat_id, user_id, username, message_id)
            VALUES ($1, $2, $3, $4)
        ''', chat_id, user_id, username, message_id)
        logging.info(f"Message {message_id} from {username} saved.")
        await conn.close()
    except Exception as e:
        logging.error(f"Database insertion failed: {e}")

    # ۳. بررسی کلمه "غالب" و اعمال محدودیت ۳۰ ثانیه‌ای
    if "غالب" in text:
        current_time = time.time()
        # گرفتن زمان آخرین درخواست کاربر، اگر نبود 0 در نظر می‌گیریم
        last_time = ghaleb_last_reply.get(user_id, 0) 
        
        if current_time - last_time > 10: # اگر بیشتر از ۳۰ ثانیه گذشته بود
            # Reply به همون پیامی که توش نوشته "غالب"
            await update.message.reply_text("بله در خدمتم", reply_to_message_id=message_id)
            # بروزرسانی زمان آخرین درخواست این کاربر
            ghaleb_last_reply[user_id] = current_time

# بدنه اصلی برنامه
if __name__ == '__main__':
    # ساخت اپلیکیشن با توکن و بیس‌ یوآرال بله
    application = ApplicationBuilder().token(TOKEN).base_url(BALE_BASE_URL).post_init(post_init).build()

    # اضافه کردن دستورات به ربات
    application.add_handler(CommandHandler("start", start))

    # این خط به ربات میگه: تمام پیام‌های متنی که با اسلش (دستور) شروع نمیشن رو بفرست به handle_messages
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))

    # اجرای ربات روی سیستم شما
    logging.info("Starting bot in polling mode. Press Ctrl+C to stop.")
    application.run_polling()