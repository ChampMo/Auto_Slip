"""รันชุดทดสอบทั้งหมด — python tests/run_all.py

แต่ละไฟล์รันเป็น process แยก เพราะหลายชุดตั้งค่า DATABASE_URL และแทนโมดูล
ของ telegram/google ด้วยของปลอมคนละแบบ ถ้ารันรวม process เดียวจะกวนกันเอง
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# ค่าที่ .env ของเครื่องนักพัฒนาอาจตั้งไว้แล้วทำให้ผลเทสต์เปลี่ยนไปคนละอย่าง
# (เคยเจอ: ตั้ง VIP_WE_TOPIC_ID ไว้ในเครื่อง แล้วเทสต์ที่ไม่รู้เรื่องหัวข้อพังหมด)
# บังคับให้เป็นค่าว่างเสมอ เทสต์ไหนต้องการค่าไหนให้ตั้งเองในไฟล์นั้น
NEUTRAL = {
    "VIP_WE_TOPIC_ID": "",
    "VIP_12_TOPIC_ID": "",
    "RELAY_API_ID": "",
    "RELAY_API_HASH": "",
    "API_ID": "",
    "API_HASH": "",
    "RELAY_SOURCE_BOTS": "",
}

env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8", **NEUTRAL)

files = sorted(p for p in (ROOT / "tests").glob("test_*.py"))
failed = []

for path in files:
    done = subprocess.run([sys.executable, str(path)], env=env,
                          capture_output=True, text=True, encoding="utf-8")
    if done.returncode == 0:
        print(f"[PASS] {path.name}")
    else:
        failed.append(path.name)
        print(f"[FAIL] {path.name}")
        for line in (done.stdout or "").splitlines():
            if "FAIL" in line:
                print(f"       {line.strip()}")
        tail = (done.stderr or "").strip().splitlines()[-3:]
        for line in tail:
            print(f"       {line}")

print(f"\nผ่าน {len(files) - len(failed)} / {len(files)} ชุด")
if failed:
    print("ไม่ผ่าน: " + ", ".join(failed))
sys.exit(1 if failed else 0)
