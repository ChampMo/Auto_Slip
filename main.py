from core.config import config
from core.version import CHANGELOG, __version__, describe_version
from database.session import init_db
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram import BotCommand
from telegram.ext import Defaults
from bot.commands import (
    approver_add_command,
    approver_remove_command,
    approvers_command,
    health_command,
    help_command,
    myid_command,
    pending_command,
    recheck_command,
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
from services.relay import relay_enabled, start_relay, stop_relay
from services.easyslip import validate_company_accounts
from scheduler import start_scheduler

# รายการที่จะขึ้นในเมนูตอนพิมพ์ "/" ในแชท
BOT_COMMANDS = [
    BotCommand("status", "How did this slip end? (reply to it)"),
    BotCommand("pending", "Slips still waiting for a decision"),
    BotCommand("recheck", "Send a rejected slip back for checking (reply to it)"),
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

    # ตัวฟังเปิดไม่ได้ก็ต้องไม่ลากบอทหลักล้มไปด้วย สลิปที่คนส่งเองยังต้องทำงานปกติ
    if relay_enabled():
        try:
            await start_relay(application.bot)
        except Exception as exc:
            print(f"⚠️ เปิดตัวฟังข้อความจากบอทอื่นไม่สำเร็จ: {exc}")
            print("   สลิปจากบอทตัวอื่นจะไม่เข้าระบบ ต้องให้คนฟอร์เวิร์ดเข้ามาแทน")
    else:
        print("ℹ️ ยังไม่ได้ตั้งค่าตัวฟังข้อความจากบอทอื่น (RELAY_API_ID / RELAY_API_HASH)")


async def on_shutdown(application) -> None:
    """เคลียร์อัลบั้มที่ยังรอรวมอยู่ก่อนปิด ไม่ให้สลิปหายตอน deploy/restart"""
    await flush_pending_media_groups(application.bot)
    await stop_relay()


if __name__ == "__main__":
    print(f"🚀 กำลังเริ่มต้นระบบ Slip Matching Bot {describe_version()}")
    # ลิสต์สิ่งที่เปลี่ยนในรุ่นนี้ ไว้ยืนยันว่าไฟล์ที่อัปโหลดขึ้นมามีตัวแก้ที่ต้องการจริง
    for change in CHANGELOG.get(__version__, []):
        print(f"   • {change}")

    init_db()

    if not config.BOT_TOKEN:
        print("❌ ไม่พบ BOT_TOKEN ระบบไม่สามารถทำงานได้")
        exit()

    # ตารางบัญชีตั้งผิด = เงินเข้าบัญชีนั้นจะถูกปฏิเสธเงียบๆ ต้องรู้ตั้งแต่ตอนบูต
    account_problems = validate_company_accounts()
    if account_problems:
        print("⚠️ ตารางบัญชีบริษัทมีจุดที่ต้องดู:")
        for problem in account_problems:
            print(f"   • {problem}")

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

    # Telegram ไม่ยอมให้บอทตอบกลับข้อความที่บอทตัวอื่นโพสต์ (มองไม่เห็นข้อความนั้น)
    # ถ้าไม่ตั้งค่านี้ ข้อความผลตรวจของสลิปที่ตัวฟังจับมาจะส่งไม่ออกเลยทั้งใบ
    # ตั้งไว้แล้วจะส่งแบบไม่ผูก reply แทนการโยน error ทิ้ง
    #
    # ห่อ try ไว้เพราะพารามิเตอร์นี้ถูกประกาศเลิกใช้ใน PTB รุ่นใหม่
    # ถ้าวันหนึ่งมันถูกถอดออก บอทต้องยังบูตขึ้นได้ ไม่ใช่ล้มทั้งตัว
    try:
        reply_defaults = Defaults(allow_sending_without_reply=True)
    except TypeError:
        print("⚠️ PTB รุ่นนี้ไม่รองรับ allow_sending_without_reply")
        print("   สลิปที่ตัวฟังจับมาจะตอบกลับไม่ได้ ต้องแก้เป็นส่งแบบไม่ผูก reply")
        reply_defaults = None

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .defaults(reply_defaults)
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
    app.add_handler(CommandHandler("recheck", recheck_command))
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