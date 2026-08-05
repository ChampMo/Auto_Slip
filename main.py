from core.config import config
from database.session import init_db
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram import BotCommand
from bot.commands import (
    approver_add_command,
    approver_remove_command,
    approvers_command,
    health_command,
    help_command,
    myid_command,
    pending_command,
    status_command,
    today_command,
)
from bot.handlers import (
    button_callback,
    flush_pending_media_groups,
    handle_amount_reply,
    handle_photo,
    load_approver_ids,
)
from bot.recovery import recover_interrupted_slips
from scheduler import start_scheduler

# รายการที่จะขึ้นในเมนูตอนพิมพ์ "/" ในแชท
BOT_COMMANDS = [
    BotCommand("status", "How did this slip end? (reply to it)"),
    BotCommand("pending", "Slips still waiting for a decision"),
    BotCommand("today", "Today's totals"),
    BotCommand("health", "Check Drive, the sheet and scheduled jobs"),
    BotCommand("approvers", "Who can approve slips"),
    BotCommand("approver_add", "Let someone approve slips"),
    BotCommand("approver_remove", "Take approval rights away"),
    BotCommand("myid", "Show your Telegram ID"),
    BotCommand("help", "List all commands"),
]


async def on_startup(application) -> None:
    """ลงทะเบียนคำสั่งกับ Telegram เพื่อให้ขึ้นเมนูอัตโนมัติ"""
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception as exc:
        # ไม่ใช่เรื่องคอขาดบาดตาย ถ้าลงทะเบียนไม่ได้ก็ยังพิมพ์คำสั่งเองได้
        print(f"⚠️ ลงทะเบียนเมนูคำสั่งไม่สำเร็จ: {exc}")


async def on_shutdown(application) -> None:
    """เคลียร์อัลบั้มที่ยังรอรวมอยู่ก่อนปิด ไม่ให้สลิปหายตอน deploy/restart"""
    await flush_pending_media_groups(application.bot)


if __name__ == "__main__":
    print("🚀 กำลังเริ่มต้นระบบ Slip Matching Bot...")

    init_db()

    if not config.BOT_TOKEN:
        print("❌ ไม่พบ BOT_TOKEN ระบบไม่สามารถทำงานได้")
        exit()

    approver_ids = load_approver_ids()
    owner_count = len(config.SLIP_APPROVER_IDS)
    if not approver_ids:
        print("⚠️ ยังไม่มีใครอยู่ในรายชื่อผู้อนุมัติ — จะไม่มีใครกดปุ่ม Receive/Reject ได้เลย")
        print("   ใส่ SLIP_APPROVER_IDS ใน .env อย่างน้อย 1 คน (หาเลข id ได้ด้วยคำสั่ง /myid)")
    else:
        print(f"🔐 อนุมัติได้ {len(approver_ids)} คน (เจ้าของจาก .env {owner_count} คน, "
              f"เพิ่มผ่านคำสั่ง {len(approver_ids) - owner_count} คน)")
        if not owner_count:
            print("   ⚠️ ไม่มีเจ้าของใน .env เลย — ถ้าฐานข้อมูลหายจะไม่มีใครเข้าถึงได้")

    # สลิปที่ค้างเพราะบอทดับกลางการบันทึกครั้งก่อน ต้องปลดล็อกและแจ้งให้ส่งใหม่
    try:
        recovered = recover_interrupted_slips()
        if recovered:
            print(f"♻️ กู้สลิปที่ค้างจากการปิดครั้งก่อน {recovered} รายการ (แจ้งเข้ากลุ่มแล้ว)")
    except Exception as exc:
        print(f"⚠️ กู้สลิปที่ค้างไม่สำเร็จ: {exc}")

    scheduler = None
    try:
        scheduler = start_scheduler()
    except Exception as exc:
        print(f"⚠️ Scheduler ไม่สามารถเริ่มได้: {exc}")

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(on_startup)
        .post_stop(on_shutdown)
        .build()
    )
    # ให้ /health อ่านตารางงานที่ตั้งไว้ได้
    app.bot_data["scheduler"] = scheduler

    # filters.UpdateType.MESSAGE = ข้อความใหม่เท่านั้น ไม่รับข้อความที่ถูกแก้ไขภายหลัง
    # (ข้อความที่แก้แล้วจะมี update.message เป็น None ทำให้ handler พัง และสลิปก็ถูกตรวจไปแล้ว)
    slip_filter = (filters.PHOTO | filters.Document.IMAGE) & filters.UpdateType.MESSAGE
    app.add_handler(MessageHandler(slip_filter, handle_photo))

    # แอดมินพิมพ์ยอดตอบกลับ ตอนสลิปไม่มียอดให้ใช้เลย
    amount_reply_filter = (
        filters.REPLY & filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE
    )
    app.add_handler(MessageHandler(amount_reply_filter, handle_amount_reply))

    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("approvers", approvers_command))
    app.add_handler(CommandHandler("approver_add", approver_add_command))
    app.add_handler(CommandHandler("approver_remove", approver_remove_command))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🤖 Bot กำลังทำงาน... ส่งสลิปเข้ากลุ่มเพื่อทดสอบได้เลย! (กด Ctrl+C เพื่อหยุด)")
    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("🛑 กำลังหยุดระบบ...")
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)