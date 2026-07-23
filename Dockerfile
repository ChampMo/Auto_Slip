# ใช้ Python 3.10 (หรือเวอร์ชันที่คุณใช้) แบบ slim เพื่อให้ไฟล์มีขนาดเล็ก
FROM python:3.10-slim

# ตั้งค่า Working Directory ใน Container
WORKDIR /app

# คัดลอกไฟล์ requirements.txt ไปก่อนเพื่อติดตั้ง Dependencies
COPY requirements.txt .

# ติดตั้งแพ็กเกจที่จำเป็น
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกโค้ดทั้งหมดในโปรเจคไปไว้ใน /app
COPY . .

# คำสั่งสำหรับรันบอท
CMD ["python", "main.py"]