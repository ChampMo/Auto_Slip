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
    bank_resolved: bool = True,
    transfer_time_known: bool = True,
) -> VerificationDecision:
    """รับสลิปอัตโนมัติได้ก็ต่อเมื่อผ่านครบทั้ง 3 ข้อ

    1. ชื่อผู้โอน
    2. จำนวนเงิน
    3. บัญชีธนาคารผู้รับ (ต้องตรงกับบัญชีบริษัทใน COMPANY_ACCOUNTS)

    ข้อ 2 หรือ 3 ไม่ตรง = reject อัตโนมัติ
    เหลือแค่ข้อ 1 ที่ไม่ตรง หรือข้อมูลไม่พอจะตัดสิน = ขึ้นปุ่มถามแอดมิน

    bank_resolved = อ่านบัญชีผู้รับได้ชัดพอจะฟันธงไหม (สลิปที่ปิดบังเลขมากจะชี้ไม่ได้)
    transfer_time_known = อ่านเวลาโอนจากสลิปได้ไหม (ต้องใช้เขียนช่อง Time ในชีท)
    """
    # รูปเดียวมีหลายสลิป หรือ API ตรวจไม่ผ่าน = ข้อมูลไม่ครบพอจะตัดสินเอง
    if multi_slip_batch or not api_success:
        return VerificationDecision.MANUAL_REVIEW

    # บัญชีผู้รับไม่ใช่ของบริษัท = เงินไม่ได้เข้าเรา ปฏิเสธได้เลย
    #
    # ต้องตัดสินก่อนเช็คคุณภาพข้อความ เพราะเป็นคนละเรื่องกัน — ข้อความในแชทจะพิมพ์ผิด
    # หรืออ่านไม่ออกยังไง ก็ไม่ทำให้เงินที่เข้าบัญชีคนอื่นกลายเป็นเข้าบัญชีเราได้
    # (เคยพลาดตรงนี้: ธงเรื่องข้อความไปบังการปฏิเสธเรื่องบัญชี)
    #
    # แต่ต้องชี้ได้ชัดก่อน ถ้าสลิปปิดบังเลขจนบอกไม่ได้ว่าบัญชีไหน ให้คนดูแทน
    if bank_resolved and not bank_matches:
        return VerificationDecision.AUTO_REJECT

    # ข้อความในแชทขัดแย้งกันเอง (เช่น 100+100 = 300) เทียบยอดไปก็ไม่มีความหมาย
    # ห้าม reject อัตโนมัติเด็ดขาด เพราะยอดที่ "ไม่ตรง" อาจเกิดจากเขาพิมพ์ผิดเฉยๆ
    if caption_unreliable:
        return VerificationDecision.MANUAL_REVIEW

    if not bank_matches:
        # ชี้ไม่ได้ว่าเป็นบัญชีไหน ให้คนดู ไม่ปฏิเสธเอง
        return VerificationDecision.MANUAL_REVIEW

    if not amount_matches:
        return VerificationDecision.AUTO_REJECT

    if not name_matches:
        return VerificationDecision.MANUAL_REVIEW

    # ไม่รู้เวลาโอน = เขียนช่อง Time ในชีทไม่ได้ ต้องให้คนอ่านจากหน้าสลิปมาให้
    # เช็คท้ายสุด เพราะเป็นเรื่องข้อมูลไม่ครบ ไม่ใช่เหตุให้ปฏิเสธ
    if not transfer_time_known:
        return VerificationDecision.MANUAL_REVIEW

    return VerificationDecision.AUTO_RECEIVE
