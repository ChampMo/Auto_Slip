from enum import Enum


class VerificationDecision(str, Enum):
    AUTO_RECEIVE = "auto_receive"
    AUTO_REJECT = "auto_reject"
    MANUAL_REVIEW = "manual_review"


def determine_verification_action(
    api_success: bool,
    amount_matches: bool,
    name_matches: bool,
    bank_matches: bool,
    multi_slip_batch: bool = False,
    caption_unreliable: bool = False,
) -> VerificationDecision:
    """รับสลิปอัตโนมัติได้ก็ต่อเมื่อผ่านครบทั้ง 3 ข้อ

    1. ชื่อผู้โอน
    2. จำนวนเงิน
    3. บัญชีธนาคารผู้รับ (เลข 4 หลักต้องอยู่ใน ACCOUNT_MAPPING)

    ข้อ 2 หรือ 3 ไม่ตรง = reject อัตโนมัติ
    เหลือแค่ข้อ 1 ที่ไม่ตรง หรือข้อมูลไม่พอจะตัดสิน = ขึ้นปุ่มถามแอดมิน
    """
    # ข้อความในแชทขัดแย้งกันเอง (เช่น 100+100 = 300) เทียบยอดไปก็ไม่มีความหมาย
    # ห้าม reject อัตโนมัติเด็ดขาด เพราะยอดที่ "ไม่ตรง" อาจเกิดจากเขาพิมพ์ผิดเฉยๆ
    if caption_unreliable:
        return VerificationDecision.MANUAL_REVIEW

    # รูปเดียวมีหลายสลิป หรือ API ตรวจไม่ผ่าน = ข้อมูลไม่ครบพอจะตัดสินเอง
    if multi_slip_batch or not api_success:
        return VerificationDecision.MANUAL_REVIEW

    if not bank_matches or not amount_matches:
        return VerificationDecision.AUTO_REJECT

    if not name_matches:
        return VerificationDecision.MANUAL_REVIEW

    return VerificationDecision.AUTO_RECEIVE
