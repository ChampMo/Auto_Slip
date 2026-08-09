"""รันชุดทดสอบทั้งหมด — python tests/run_all.py

แต่ละไฟล์รันเป็น process แยก เพราะหลายชุดตั้งค่า DATABASE_URL และแทนโมดูล
ของ telegram/google ด้วยของปลอมคนละแบบ ถ้ารันรวม process เดียวจะกวนกันเอง
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")

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
