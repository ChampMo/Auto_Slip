"""ทดสอบว่าอ่านเฉพาะ QR จริง ไม่นับของที่อ่านมาผิดเป็นสลิปอีกใบ

เคสจริง: สลิป K+ ใบเดียว แต่ used_qrs เก็บสองแถว — payload จริง กับเลข "136243"
ที่ zbar อ่านลายเส้นบนสลิปเป็นบาร์โค้ดแบบแท่ง
ผลคือถูกนับเป็นสองใบ แล้วเข้าแมนนวลทั้งที่ทุกข้อตรง
"""
import sys
import types

REAL = "0041000600000101030040220016220190420BTF063745102TH9104D975"


def install_fake_pyzbar(payloads):
    """แทน pyzbar/PIL ด้วยของปลอม เพื่อทดสอบตัวกรองโดยไม่ต้องมีไลบรารีจริง"""
    captured = {}

    class ZBarSymbol:
        QRCODE = "QRCODE"

    def decode(img, symbols=None):
        captured["symbols"] = symbols
        return [types.SimpleNamespace(data=p.encode()) for p in payloads]

    pzp = types.ModuleType("pyzbar.pyzbar")
    pzp.decode, pzp.ZBarSymbol = decode, ZBarSymbol
    sys.modules["pyzbar"] = types.ModuleType("pyzbar")
    sys.modules["pyzbar.pyzbar"] = pzp

    pil = types.ModuleType("PIL")
    pil.Image = types.SimpleNamespace(open=lambda path: object())
    sys.modules["PIL"] = pil

    sys.modules.pop("core.scanner", None)
    from core.scanner import read_qr_code
    return read_qr_code, captured


failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


print("=== กรองสิ่งที่ไม่ใช่ QR สลิป ===")
read_qr_code, captured = install_fake_pyzbar([REAL, "136243", REAL])
result = read_qr_code("slip.jpg")
check("ขอ zbar เฉพาะ QR", captured["symbols"], ["QRCODE"])
check("  สลิปใบเดียวต้องนับได้ 1", len(result), 1)
check("  ได้ payload จริง", result[0], REAL)

print("\n=== หลายสลิปในรูปเดียวยังต้องนับครบ ===")
second = REAL.replace("BTF06374", "BTF09999")
read_qr_code, _ = install_fake_pyzbar([REAL, second])
check("สอง QR จริง -> นับ 2", len(read_qr_code("slip.jpg")), 2)

print("\n=== ไม่พบอะไรเลย ===")
read_qr_code, _ = install_fake_pyzbar([])
check("คืนลิสต์ว่าง", read_qr_code("slip.jpg"), [])

read_qr_code, _ = install_fake_pyzbar(["136243", "99"])
check("เจอแต่ของสั้นๆ -> เท่ากับไม่พบ QR", read_qr_code("slip.jpg"), [])

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
