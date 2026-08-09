"""ทดสอบชื่อภาษาอังกฤษบนสลิป — คำนำหน้า MR/MRS/MISS/MS/DR"""
import sys

from core.names import get_first_name, get_first_names, split_bank_name, strip_name_title

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


def names_match(api_name, reported_name):
    """สำเนาลอจิกใน bot/handlers.py (เลี่ยง import telegram ที่ยังไม่ได้ติดตั้ง)"""
    api_first, api_initial = split_bank_name(api_name)
    rep_first, rep_initial = split_bank_name(reported_name)
    if not api_first or not rep_first:
        return False
    if api_first != rep_first:
        return False
    if not api_initial or not rep_initial:
        return True
    return api_initial == rep_initial


print("=== ตัดคำนำหน้าภาษาอังกฤษ ===")
check("เคสในรูป", strip_name_title("MR ATHIWAT N"), "ATHIWAT N")
check("  มีจุด", strip_name_title("MR. ATHIWAT N"), "ATHIWAT N")
check("  ตัวพิมพ์เล็ก", strip_name_title("mr athiwat n"), "athiwat n")
check("  ผสม", strip_name_title("Mr. Athiwat N"), "Athiwat N")
check("  MRS ต้องไม่โดน MR ตัดหน้า", strip_name_title("MRS SOMYING S"), "SOMYING S")
check("  MISS", strip_name_title("MISS SOMYING S"), "SOMYING S")
check("  MS", strip_name_title("MS SOMYING S"), "SOMYING S")
check("  DR", strip_name_title("DR SOMCHAI J"), "SOMCHAI J")
check("  ช่องว่างหลายตัว", strip_name_title("MR   ATHIWAT N"), "ATHIWAT N")

print("\n=== ต้องไม่ตัดผิด ===")
check("ไม่มีคำนำหน้า", strip_name_title("ATHIWAT N"), "ATHIWAT N")
check("  ชื่อขึ้นต้น MR แต่ติดกัน", strip_name_title("MRIDULA S"), "MRIDULA S")
check("  ชื่อขึ้นต้น MS แต่ติดกัน", strip_name_title("MSAENG K"), "MSAENG K")
check("  ชื่อขึ้นต้น DR แต่ติดกัน", strip_name_title("DRAKE W"), "DRAKE W")
check("  มีแต่คำนำหน้าไม่มีชื่อ", strip_name_title("MR"), "MR")
check("  ค่าว่าง", strip_name_title(""), "")
check("  None", strip_name_title(None), "")

print("\n=== ภาษาไทยต้องไม่พัง ===")
check("นาย", strip_name_title("นาย ดุลยฤทธิ์ ส"), "ดุลยฤทธิ์ ส")
check("  นางสาว", strip_name_title("นางสาว ปิยะดา ก"), "ปิยะดา ก")
check("  เขียนติดกัน", strip_name_title("นายสมชาย"), "สมชาย")
check("  น.ส.", strip_name_title("น.ส.สมหญิง จ"), "สมหญิง จ")

print("\n=== ชื่อที่จะลงชีท (ช่อง Trans ID) ===")
check("เคสในรูป: ต้องได้ ATHIWAT ไม่ใช่ MR", get_first_name("MR ATHIWAT N"), "ATHIWAT")
check("  MRS", get_first_name("MRS SOMYING S"), "SOMYING")
check("  ไทยเหมือนเดิม", get_first_name("นาย ดุลยฤทธิ์ ส"), "ดุลยฤทธิ์")
check("  หลายสลิป", get_first_names("MR ATHIWAT N, MISS SOMYING S"), "ATHIWAT, SOMYING")
check("  API อ่านชื่อไม่ได้", get_first_name("Unknown"), "")

print("\n=== แยกชื่อ/อักษรย่อนามสกุล ===")
check("เคสในรูป", split_bank_name("MR ATHIWAT N"), ("athiwat", "n"))
check("  ไม่มีคำนำหน้า", split_bank_name("ATHIWAT N"), ("athiwat", "n"))
check("  ชื่อเดียว", split_bank_name("MR ATHIWAT"), ("athiwat", ""))

print("\n=== เทียบชื่อ ===")
check("แชทพิมพ์ชื่อจริงอย่างเดียว", names_match("MR ATHIWAT N", "ATHIWAT"), True)
check("  แชทพิมพ์พร้อมอักษรย่อ", names_match("MR ATHIWAT N", "ATHIWAT N"), True)
check("  แชทพิมพ์นามสกุลเต็ม", names_match("MR ATHIWAT N", "Athiwat Nakorn"), True)
check("  แชทพิมพ์คำนำหน้ามาด้วย", names_match("MR ATHIWAT N", "MR ATHIWAT N"), True)
check("  ตัวพิมพ์ไม่ตรงกัน", names_match("MR ATHIWAT N", "athiwat n"), True)
check("  นามสกุลคนละตัว", names_match("MR ATHIWAT N", "ATHIWAT K"), False)
check("  คนละคน", names_match("MR ATHIWAT N", "SOMCHAI N"), False)
check("  MRS เทียบถูกคน", names_match("MRS SOMYING S", "SOMYING"), True)
check("  MR vs MRS คนละคน", names_match("MR SOMYING S", "SOMCHAI"), False)

print("\n=== ก่อนแก้จะเป็นแบบนี้ (ยืนยันว่าเคยพัง) ===")
check("MR เคยถูกนับเป็นชื่อจริง", split_bank_name("MR ATHIWAT N") != ("mr", "n"), True)
check("  ช่อง Trans ID เคยขึ้น MR", get_first_name("MR ATHIWAT N") != "MR", True)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
