from pyzbar.pyzbar import decode
from PIL import Image

def read_qr_code(image_path: str) -> str:
    try:
        img = Image.open(image_path)
        decoded_objects = decode(img)
        if decoded_objects:
            # คืนค่า String ที่แกะได้จาก QR Code
            return decoded_objects[0].data.decode('utf-8') 
        return None
    except Exception as e:
        print(f"⚠️ อ่าน QR Code ไม่สำเร็จ: {e}")
        return None