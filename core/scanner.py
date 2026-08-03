import logging

from pyzbar.pyzbar import decode
from PIL import Image


logger = logging.getLogger(__name__)

def read_qr_code(image_path: str):
    try:
        img = Image.open(image_path)
        decoded_objects = decode(img)
        
        if decoded_objects:
            # เก็บ QR Code ทั้งหมดที่เจอใส่ List
            qr_list = []
            for obj in decoded_objects:
                qr_list.append(obj.data.decode('utf-8'))
            logger.info("QR decode success | image=%s | qr_count=%s", image_path, len(qr_list))
            return qr_list  # ส่งคืนเป็น List เช่น ['QR1', 'QR2']
            
        logger.info("QR decode found no codes | image=%s", image_path)
        return []
    except Exception as e:
        logger.exception("QR decode failed | image=%s | error=%s", image_path, e)
        return []