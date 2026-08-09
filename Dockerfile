# ใช้ Python 3.10 (หรือเวอร์ชันที่คุณใช้) แบบ slim เพื่อให้ไฟล์มีขนาดเล็ก
FROM python:3.10-slim

# ตั้งค่า Working Directory ใน Container
WORKDIR /app

# ไม่ให้ Python กันข้อความไว้ในบัฟเฟอร์
# ถ้าไม่ตั้ง ข้อความจาก print() จะไม่โผล่ใน docker logs จนกว่าบัฟเฟอร์จะเต็มหรือโปรแกรมปิด
# ทำให้ข้อความตอนบูต (ขยายคอลัมน์ / จำนวนผู้อนุมัติ / ปัญหาตารางบัญชี) หายไปทั้งหมด
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y libzbar0
# คัดลอกไฟล์ requirements.txt ไปก่อนเพื่อติดตั้ง Dependencies
COPY requirements.txt .

# ติดตั้งแพ็กเกจที่จำเป็น
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกโค้ดทั้งหมดในโปรเจคไปไว้ใน /app
COPY . .

# คำสั่งสำหรับรันบอท
CMD ["python", "main.py"]