import logging

from core.matcher import GROUP_CATEGORY
from database.crud import add_audit_log, find_interrupted_batches, SHEET_REOPEN_ACTION
from database.models import Transaction
from database.session import SessionLocal
from services.notifier import send_telegram_message

logger = logging.getLogger(__name__)

# ส่งรายชื่อในข้อความเดียว ไม่ให้ยาวเกินไปถ้าค้างเยอะ
MAX_LISTED_SLIPS = 10


def recover_interrupted_slips() -> int:
    """กู้รายการที่ค้างเพราะบอทดับระหว่างเขียนลงชีท แล้วแจ้งกลุ่มให้ส่งใหม่

    เรียกตอนบอทบูต — รายการเหล่านี้ถูกจองสถานะไว้แล้วแต่ยังไม่ได้ลงชีท
    ถ้าปล่อยไว้จะค้างถาวร (ปุ่มก็หายไปแล้ว ส่งซ้ำก็ติด duplicate)
    """
    with SessionLocal() as db:
        batch_ids = find_interrupted_batches(db)
        if not batch_ids:
            return 0

        recovered = []
        for batch_id in batch_ids:
            txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
            if txn is None:
                continue

            # interrupted อยู่ในกลุ่มสถานะที่ยอมให้ส่งสลิปใบเดิมเข้ามาใหม่ได้
            txn.status = "interrupted"
            add_audit_log(db, batch_id, SHEET_REOPEN_ACTION)
            recovered.append({
                "batch_id": batch_id,
                "chat_id": txn.chat_id,
                "trans_id": txn.chat_trans_id or txn.chat_user_id or "-",
                "amount": txn.api_total_amount if txn.api_total_amount is not None else txn.chat_amount,
            })

    if not recovered:
        return 0

    logger.warning(
        "Recovered %s slip(s) interrupted mid-save: %s",
        len(recovered), ", ".join(item["batch_id"][:12] for item in recovered),
    )
    _notify_groups(recovered)
    return len(recovered)


def _notify_groups(recovered: list) -> None:
    """แจ้งเฉพาะกลุ่มที่มีรายการค้างจริง ไม่ยิงทุกกลุ่ม"""
    by_chat = {}
    for item in recovered:
        by_chat.setdefault(str(item["chat_id"]), []).append(item)

    for chat_id, items in by_chat.items():
        if chat_id not in GROUP_CATEGORY:
            continue

        listed = items[:MAX_LISTED_SLIPS]
        lines = [
            f"• ID {item['trans_id']} — "
            f"{('%.2f THB' % item['amount']) if item['amount'] is not None else 'amount unknown'}"
            for item in listed
        ]
        if len(items) > len(listed):
            lines.append(f"• ...and {len(items) - len(listed)} more")

        send_telegram_message(
            chat_id,
            "⚠️ Some slips were interrupted while being saved\n\n"
            "The bot restarted before these slips reached today's sheet, "
            "so they were NOT recorded:\n\n"
            + "\n".join(lines)
            + "\n\nPlease send these slips again — they will be accepted as new.",
        )
