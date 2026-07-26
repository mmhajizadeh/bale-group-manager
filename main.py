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

# هندلر شمارش کل پیام‌های گروه
async def count_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        # شمردن تمام ردیف ‌هایی که آیدی گروهشون با گروه فعلی یکیه
        count = await conn.fetchval('SELECT COUNT(*) FROM messages WHERE chat_id = $1', chat_id)
        await conn.close()
        
        await update.message.reply_text(f"📊 تعداد کل پیام‌های ثبت شده گروه تا این لحظه: {count}")
    except Exception as e:
        logging.error(f"Error in count_group: {e}")
        await update.message.reply_text("❌ خطایی در ارتباط با دیتابیس رخ داد.")

# هندلر شمارش پیام‌های یک کاربر (خودش یا شخص دیگر)
async def count_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # اگر کاربر جلوی دستور چیزی نوشته بود (مثلا /count_user @ali)
    if context.args:
        # حذف کاراکتر @ در صورت وجود
        target_username = context.args[0].replace('@', '') 
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM messages WHERE chat_id = $1 AND username = $2', 
                chat_id, target_username
            )
            await conn.close()
            
            await update.message.reply_text(f"👤 تعداد پیام‌ های @{target_username} در این گروه: {count}")
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
            
            await update.message.reply_text(f"👤 شما تا به حال {count} پیام در این گروه ارسال کرده‌ اید.")
        except Exception as e:
            logging.error(f"Error in count_user (self): {e}")

# هندلر حذف N پیام آخر گروه
async def delete_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # بررسی اینکه آیا کاربر تعداد را وارد کرده یا نه (مثلا /delete_last 10)
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ لطفاً تعداد پیام را وارد کنید. مثال: /delete_last 10")
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
        await update.message.reply_text(f"✅ تعداد {deleted_count} پیام آخر گروه با موفقیت حذف شد.")
    except Exception as e:
        logging.error(f"Error in delete_last: {e}")
        await update.message.reply_text("❌ خطایی رخ داد. آیا من در گروه ادمین هستم؟")

# هندلر حذف N پیام آخر یک کاربر خاص
async def delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # بررسی ورودی‌ها (باید حداقل ۲ کلمه باشد: یوزرنیم و تعداد)
    if len(context.args) < 2 or not context.args[1].isdigit():
        await update.message.reply_text("❌ فرمت اشتباه است. مثال: /delete_user @mmhajizadeh 5")
        return
        
    target_username = context.args[0].replace('@', '')
    limit = int(context.args[1])
    if limit > 1000:
        limit = 1000
        
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        records = await conn.fetch('''
            SELECT id, message_id FROM messages 
            WHERE chat_id = $1 AND username = $2
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
        await update.message.reply_text(f"✅ تعداد {deleted_count} پیام آخر از کاربر @{target_username} حذف شد.")
    except Exception as e:
        logging.error(f"Error in delete_user: {e}")
        await update.message.reply_text("❌ خطایی رخ داد.")

# بدنه اصلی برنامه
if __name__ == '__main__':
    # ساخت اپلیکیشن با توکن و بیس‌ یوآرال بله
    application = ApplicationBuilder().token(TOKEN).base_url(BALE_BASE_URL).post_init(post_init).build()

    # اضافه کردن دستورات به ربات
    application.add_handler(CommandHandler("start", start))

    # این خط به ربات میگه: تمام پیام‌های متنی که با اسلش (دستور) شروع نمیشن رو بفرست به handle_messages
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))

    application.add_handler(CommandHandler("count_group", count_group))
    application.add_handler(CommandHandler("count_user", count_user))

    application.add_handler(CommandHandler("delete_last", delete_last))
    application.add_handler(CommandHandler("delete_user", delete_user))

    # اجرای ربات روی سیستم شما
    logging.info("Starting bot in polling mode. Press Ctrl+C to stop.")
    application.run_polling()