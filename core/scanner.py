from pyzbar.pyzbar import decode
from PIL import Image

def read_qr_code(image_path: str):
    try:
        img = Image.open(image_path)
        decoded_objects = decode(img)
        
        if decoded_objects:
            # เก็บ QR Code ทั้งหมดที่เจอใส่ List
            qr_list = []
            for obj in decoded_objects:
                qr_list.append(obj.data.decode('utf-8'))
            return qr_list  # ส่งคืนเป็น List เช่น ['QR1', 'QR2']
            
        return []
    except Exception as e:
        print(f"⚠️ อ่าน QR Code ไม่สำเร็จ: {e}")
        return []