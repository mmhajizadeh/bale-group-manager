import os
import logging
import asyncpg
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

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

# بدنه اصلی برنامه
if __name__ == '__main__':
    # ساخت اپلیکیشن با توکن و بیس‌ یوآرال بله
    application = ApplicationBuilder().token(TOKEN).base_url(BALE_BASE_URL).post_init(post_init).build()

    # اضافه کردن دستورات به ربات
    application.add_handler(CommandHandler("start", start))

    # اجرای ربات روی سیستم شما
    logging.info("Starting bot in polling mode. Press Ctrl+C to stop.")
    application.run_polling()