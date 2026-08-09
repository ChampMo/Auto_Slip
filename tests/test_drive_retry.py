"""ทดสอบ: Drive ตอบพลาดชั่วคราว ต้องไม่กลายเป็น 'ไม่พบโฟลเดอร์'

เคสจริง: สลิปที่ควรรับอัตโนมัติกลับขึ้นว่าหาโฟลเดอร์ปีไม่เจอ
แต่พอกด Receive เองกลับบันทึกได้ปกติ — แปลว่าโฟลเดอร์มีอยู่ แค่เรียก API พลาดครั้งเดียว
"""
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
for m in ["telegram", "telegram.ext", "telegram.error", "gspread", "gspread.exceptions",
          "google", "google.oauth2", "google.oauth2.service_account", "googleapiclient",
          "googleapiclient.discovery", "googleapiclient.errors", "requests",
          "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, MagicMock(name=m))
ge = types.ModuleType("gspread.exceptions")
ge.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
ge.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = ge

import services.gdrive as gd  # noqa: E402

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


gd.DRIVE_RETRY_WAIT = 0  # ไม่ต้องรอจริงตอนทดสอบ


class FakeFiles:
    """จำลอง Drive API — กำหนดได้ว่าพลาดกี่ครั้งแรกก่อนจะสำเร็จ"""

    def __init__(self, fail_times=0, found=True):
        self.fail_times = fail_times
        self.found = found
        self.calls = 0

    def list(self, **kwargs):
        return self

    def execute(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("Google บอกว่า rate limit")
        if not self.found:
            return {"files": []}
        return {"files": [{"id": "FOLDER123", "name": "Deposit-2026", "webViewLink": "x"}]}


def drive_with(fake):
    service = gd.GoogleDriveService.__new__(gd.GoogleDriveService)
    service.service = MagicMock()
    service.service.files.return_value = fake
    return service


print("=== เรียกสำเร็จตั้งแต่ครั้งแรก ===")
fake = FakeFiles()
check("เจอโฟลเดอร์", drive_with(fake)._find_child_id("Deposit-2026", "ROOT"), "FOLDER123")
check("  เรียกครั้งเดียวพอ", fake.calls, 1)

print("\n=== พลาดครั้งแรก แล้วสำเร็จ (เคสจากภาพ) ===")
fake = FakeFiles(fail_times=1)
check("ลองซ้ำแล้วเจอ", drive_with(fake)._find_child_id("Deposit-2026", "ROOT"), "FOLDER123")
check("  เรียกทั้งหมด 2 ครั้ง", fake.calls, 2)

print("\n=== พลาด 2 ครั้ง แล้วสำเร็จ ===")
fake = FakeFiles(fail_times=2)
check("ยังกู้สถานการณ์ได้", drive_with(fake)._find_child_id("Deposit-2026", "ROOT"), "FOLDER123")
check("  เรียกทั้งหมด 3 ครั้ง", fake.calls, 3)

print("\n=== พลาดทุกครั้ง -> ต้องโยน error ไม่ใช่คืน None ===")
fake = FakeFiles(fail_times=99)
try:
    drive_with(fake)._find_child_id("Deposit-2026", "ROOT")
    check("โยน DriveUnavailable", False, True)
except gd.DriveUnavailable as exc:
    check("โยน DriveUnavailable", True, True)
    check("  บอกว่าติดต่อ Drive ไม่ได้", "Could not reach Google Drive" in str(exc), True)
    check("  ไม่ได้บอกว่าไม่มีโฟลเดอร์", "not found" in str(exc).lower(), False)
check("  ลองครบตามที่ตั้งไว้", fake.calls, gd.DRIVE_RETRIES)

print("\n=== เรียกสำเร็จแต่ไม่มีจริง -> คืน None ไม่ต้องลองซ้ำ ===")
fake = FakeFiles(found=False)
check("คืน None", drive_with(fake)._find_child_id("Deposit-2099", "ROOT"), None)
check("  ไม่เสียเวลาลองซ้ำ", fake.calls, 1)

print("\n=== ไม่มี parent id -> คืน None ทันที ===")
fake = FakeFiles()
check("คืน None", drive_with(fake)._find_child_id("อะไรก็ตาม", ""), None)
check("  ไม่เรียก API เลย", fake.calls, 0)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
