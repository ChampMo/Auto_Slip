import hashlib
import logging

from pyzbar.pyzbar import ZBarSymbol, decode
from PIL import Image


logger = logging.getLogger(__name__)

# อ่านเฉพาะ QR เท่านั้น ไม่รับบาร์โค้ดแบบแท่ง
#
# ปกติ decode() จะพยายามอ่านทุกรูปแบบรวมถึงบาร์โค้ด 1 มิติ ซึ่งลายเส้นบนสลิป
# (กรอบ ตาราง เงาลายน้ำ) หลอกให้มันอ่านออกมาเป็นตัวเลขสั้นๆ ได้บ่อย
# ผลคือรูปที่มีสลิปใบเดียวถูกนับเป็นสองใบ แล้วเข้าแมนนวลทั้งที่ทุกอย่างตรง
_QR_ONLY = [ZBarSymbol.QRCODE]

# ความยาวขั้นต่ำที่ยอมรับว่าเป็น payload ของสลิปได้
# QR สลิปเป็น EMV ยาวกว่า 50 ตัวเสมอ ตั้งไว้ต่ำกว่านั้นมากเพื่อกันของสั้นๆ ที่อ่านมาผิด
# โดยไม่เสี่ยงตัดของจริงทิ้ง ถ้าตัดพลาดจริงจะกลายเป็น "ไม่พบ QR" ซึ่งเข้าแมนนวล ไม่ใช่รับเงินผิด
_MIN_PAYLOAD_LENGTH = 20


def read_qr_code(image_path: str):
    try:
        img = Image.open(image_path)
        decoded_objects = decode(img, symbols=_QR_ONLY)

        if decoded_objects:
            # เก็บ QR Code ทั้งหมดที่เจอใส่ List
            # QR ตัวเดียวกันอาจถูกอ่านซ้ำได้ถ้ารูปมีเงาหรือถูกครอบตัด จึงตัดตัวซ้ำออก
            qr_list = []
            for obj in decoded_objects:
                payload = obj.data.decode("utf-8")
                if len(payload) < _MIN_PAYLOAD_LENGTH:
                    logger.info("Ignored a QR that is too short to be a slip | image=%s | value=%r",
                                image_path, payload)
                    continue
                if payload not in qr_list:
                    qr_list.append(payload)
            logger.info("QR decode success | image=%s | qr_count=%s", image_path, len(qr_list))
            return qr_list  # ส่งคืนเป็น List เช่น ['QR1', 'QR2']

        logger.info("QR decode found no codes | image=%s", image_path)
        return []
    except Exception as e:
        logger.exception("QR decode failed | image=%s | error=%s", image_path, e)
        return []

def file_sha256(image_path: str) -> str:
    """แฮชเนื้อไฟล์ ใช้เป็นตัวระบุรูปแทน QR ตอนที่อ่าน QR ไม่ออก

    แฮชเองแทนการใช้ file_unique_id ของ Telegram เพราะรูปเดียวกันที่ส่งแบบรูป
    กับส่งแบบไฟล์ จะได้ file_unique_id คนละค่า ทั้งที่เนื้อไฟล์เหมือนกันเป๊ะ

    อ่านทีละก้อน ไม่โหลดทั้งไฟล์ขึ้นหน่วยความจำ เผื่อมีคนส่งรูปความละเอียดสูงมา
    """
    digest = hashlib.sha256()
    try:
        with open(image_path, "rb") as image:
            for chunk in iter(lambda: image.read(65536), b""):
                digest.update(chunk)
    except OSError as exc:
        logger.warning("Could not hash the image | image=%s | error=%s", image_path, exc)
        return ""
    return digest.hexdigest()
