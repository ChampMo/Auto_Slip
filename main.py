from core.config import config
from database.session import init_db
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, filters
from bot.handlers import handle_photo, button_callback
from scheduler import start_scheduler


if __name__ == "__main__":
    print("🚀 กำลังเริ่มต้นระบบ Slip Matching Bot...")

    init_db()

    if not config.BOT_TOKEN:
        print("❌ ไม่พบ BOT_TOKEN ระบบไม่สามารถทำงานได้")
        exit()
        
    # สร้างตัวควบคุม Bot
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🤖 Bot กำลังทำงาน... ส่งสลิปเข้ากลุ่มเพื่อทดสอบได้เลย! (กด Ctrl+C เพื่อหยุด)")
    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("🛑 กำลังหยุดระบบ...")
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)