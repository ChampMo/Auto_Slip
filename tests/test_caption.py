"""ทดสอบตัวอ่าน caption ด้วยข้อความจริง 9 แบบที่ส่งกันในกลุ่ม

นำเข้า core.captions ตรงๆ (ไม่พึ่ง sqlalchemy/telegram) เพื่อทดสอบโค้ดตัวจริง
"""
import sys

from core.captions import extract_data_from_caption, parse_typed_amount

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


def check_caption(label, caption, **expected):
    data = extract_data_from_caption(caption)
    for field, want in expected.items():
        check(f"{label} / {field}", data[field], want)


print("=== 9 format จริงจากกลุ่ม ===")

check_caption(
    "1. Username + บวกเลข",
    "Username: 0811370290\n"
    "Amount 100+100 = 200 HB\n"
    "Deposit Bonus: N/A\n"
    "-eya",
    user_id="0811370290", amount=200.0, trans_id=None, fullname=None, amount_note=None,
)

check_caption(
    "2. TRANS ID + FULL NAME",
    "TRANS ID : 7080338\n"
    "FULL NAME : อานนท์ กำฟิษร\n"
    "AMOUNT THB : 1,100.00\n"
    "\n"
    "Done inform member about\n"
    "minimum deposit, thanks.\n"
    "\n"
    "-Whendz",
    trans_id="7080338", fullname="อานนท์ กำฟิษร", amount=1100.0, user_id=None,
)

check_caption(
    "3. สั้นๆ ไม่มีหางข้อความ",
    "TRANS ID : 7080602\n"
    "FULL NAME : สมปอง มาลากุล\n"
    "AMOUNT THB : 4,000.00",
    trans_id="7080602", fullname="สมปอง มาลากุล", amount=4000.0,
)

check_caption(
    "4. ฟอร์มยาว มีวันที่/เลขบัญชีปนมา",
    "Trx. Date: 2026-08-02\n"
    "Trans ID: 1333805\n"
    "Name: ภานุพงษ์ ภัทรพงษ์เวชน์\n"
    "Bank Name: KBANK / ธ.กสิกรไทย\n"
    "Account No: 2273266332\n"
    "Amount: 1,000.00\n"
    "\n"
    "Please help verify deposit. Thanks\n"
    "-Beverly",
    trans_id="1333805", fullname="ภานุพงษ์ ภัทรพงษ์เวชน์", amount=1000.0, user_id=None,
)

check_caption(
    "5. ป้ายอยู่หลังคำทักทาย",
    "Hi team, User :  ari879565\n"
    "Amount : THB  500.00\n"
    "DP to >>  KKP LEASON\n"
    "Done inform member about Min -\n"
    "Max deposit, thanks\n"
    "\n"
    "-maryann",
    user_id="ari879565", amount=500.0, trans_id=None, fullname=None,
)

check_caption(
    "6. ทักทายคนละบรรทัดกับป้าย",
    "Hi Team, kindly assist to check\n"
    "deposit.\n"
    "Username: som0956191\n"
    "Amount: 300 THB\n"
    "Deposit Bonus: N/A\n"
    "Reminded regarding the minimum\n"
    "deposit change.\n"
    "\n"
    "-maryann",
    user_id="som0956191", amount=300.0,
)

check_caption(
    "7. ไม่มีเครื่องหมาย : หลัง Amount",
    "Username: mongmadona\n"
    "Amount 400 HB\n"
    "\n"
    "please help to check if fund receive\n"
    "team thanks\n"
    "Bryan",
    user_id="mongmadona", amount=400.0,
)

check_caption(
    "8. Trx ID + User + AMOUNT",
    "Hi teams,\n"
    "User :  aoon1234oo\n"
    "Trx ID :  1330995\n"
    "AMOUNT: 4,000.00\n"
    "Pls kindly check member deposit,\n"
    "thanks\n"
    "\n"
    "-maryann",
    user_id="aoon1234oo", trans_id="1330995", amount=4000.0,
)

check_caption(
    "9. ยอดไม่มีทศนิยม",
    "Hi team, User :  hookeemen\n"
    "Amount : THB  2000\n"
    "DP to >>  KKP LEASON\n"
    "Done inform member about Min -\n"
    "Max deposit, thanks",
    user_id="hookeemen", amount=2000.0,
)

print("\n=== ตัวเลขบวกกัน ===")
check_caption("บวกลงตัว", "Amount 100+100 = 200 HB", amount=200.0, amount_note=None)
check_caption("บวกเฉยๆ ไม่เขียนผลรวม", "Amount: 250 + 250", amount=500.0, amount_note=None)
check_caption("สามก้อน", "Amount: 100+200+300 = 600", amount=600.0, amount_note=None)

data = extract_data_from_caption("Amount 100+100 = 300 HB")
check("บวกไม่ลง -> ยึดยอดที่เขาเขียน", data["amount"], 300.0)
check("  ติดธงไว้ว่าไม่ตรง", data["amount_note"] is not None, True)
check("  บอกทั้งผลบวกจริงและยอดที่เขียน",
      "100 + 100 = 200" in (data["amount_note"] or "")
      and "says 300" in (data["amount_note"] or ""), True)

print("\n=== เคสจริงที่เคยพลาด: มีอะไรนำหน้าป้าย ===")
# เจอในกลุ่มจริง: บรรทัด AMOUNT มีช่องว่างนำหน้า ทำให้อ่านยอดไม่เจอ
# ทั้งที่ชื่ออ่านออกปกติ (บรรทัดชื่อไม่มีช่องว่างนำหน้า)
BASE = "TRANS ID : 0000012\nFULL NAME : มณฑล สุขจินดา\n{}AMOUNT THB : 200.00"
for label, prefix in [
    ("ช่องว่าง 1 ที", " "),
    ("ช่องว่าง 2 ที", "  "),
    ("แท็บ", "\t"),
    ("NBSP", " "),
    ("ขีดนำหน้า", "- "),
    ("bullet", "• "),
]:
    check_caption(f"{label} หน้าป้าย AMOUNT", BASE.format(prefix),
                  amount=200.0, fullname="มณฑล สุขจินดา", trans_id="0000012")

check_caption(
    "พิมพ์รวมมาบรรทัดเดียว",
    "TRANS ID : 0000012 FULL NAME : มณฑล สุขจินดา AMOUNT THB : 200.00",
    amount=200.0, fullname="มณฑล สุขจินดา", trans_id="0000012",
)

print("\n=== เคสจริง: ป้ายเว้นค่าว่างไว้ ห้ามดูดค่าจากบรรทัดถัดไป ===")
# เคยเป็นบั๊ก: "Trans ID:" เว้นว่าง แล้วระบบข้ามบรรทัดไปหยิบ "Name:" มาเป็นรหัสรายการ
# กรณียอดเงินร้ายแรงกว่า เพราะหยิบเลขบัญชีมาเป็นจำนวนเงินได้
check_caption(
    "ฟอร์มยาวที่ Trans ID เว้นว่าง",
    "Trx. Date:  2026-08-07\n"
    "Trans ID:          \n"
    "Name:   วรวุฒิ เงาโงน\n"
    "Bank Name:  TTB / ทีเอ็มบีธนชาต\n"
    "Account No:   7302025429\n"
    "Amount:    50.00\n"
    "\n"
    "Please help verify deposit. Thanks\n"
    "-Beverly",
    trans_id=None, fullname="วรวุฒิ เงาโงน", amount=50.0, user_id=None,
)

check_caption("ยอดเงินเว้นว่าง ห้ามหยิบเลขบัญชีบรรทัดถัดไป",
              "Amount:\nAccount No: 7302025429", amount=None)
check_caption("  ยอดเงินเว้นว่าง บรรทัดถัดไปเป็นเลขล้วน",
              "Amount:\n7302025429", amount=None)
check_caption("  ชื่อผู้ใช้เว้นว่าง", "User:\n7090129", user_id=None)
check_caption("  ชื่อผู้โอนเว้นว่าง", "FULL NAME:\nสมชาย ส", fullname=None)
check_caption("  รหัสรายการเว้นว่าง", "Trans ID:\nName: สมชาย", trans_id=None, fullname="สมชาย")

print("\n=== จุดที่เคยพลาด: ห้ามหยิบตัวเลขที่ไม่ใช่ยอดเงิน ===")
check_caption("เลขบัญชีไม่ใช่ยอด", "Account No: 2273266332", amount=None)
check_caption("  วันที่ไม่ใช่ยอด", "Trx. Date: 2026-08-02", amount=None)
check_caption("  โบนัสไม่ใช่ยอด", "Deposit Bonus: N/A", amount=None)
check_caption("  เบอร์โทรไม่ใช่ยอด", "Username: 0811370290", amount=None, user_id="0811370290")
check_caption("  ไม่มีป้ายเลย = ไม่เดา", "please help check this 500 baht deposit", amount=None)
check_caption("  Bank Name ไม่ใช่ชื่อคน", "Bank Name: KBANK / ธ.กสิกรไทย", fullname=None)
check_caption("  Account ไม่ใช่ Amount", "Account No: 5000", amount=None)

print("\n=== ป้ายแบบอื่นที่ยังอ่านออก ===")
check_caption("USER ID", "User ID: abc123", user_id="abc123")
check_caption("  MEMBER ID", "Member ID: xyz789", user_id="xyz789")
check_caption("  TRANSACTION ID", "Transaction ID: 555", trans_id="555")
check_caption("  TRXID ติดกัน", "TRXID: 777", trans_id="777")
check_caption("  SENDER NAME", "Sender Name: ดุลยฤทธิ์ ส", fullname="ดุลยฤทธิ์ ส")
check_caption("  พิมพ์เล็กพิมพ์ใหญ่ปนกัน", "aMoUnT tHb: 1,234.50", amount=1234.50)
check_caption("  ป้ายไทย", "ยอดเงิน: 900", amount=900.0)
check_caption("  ใช้ = แทน :", "Amount = 750", amount=750.0)
check_caption("  มีสัญลักษณ์บาท", "Amount: ฿1,500", amount=1500.0)

print("\n=== caption ว่าง ===")
for empty in ["", None, "   \n  "]:
    data = extract_data_from_caption(empty)
    check(f"ว่าง {empty!r} -> ไม่ได้อะไรเลย",
          all(value is None for value in data.values()), True)

print("\n=== ยอดที่แอดมินพิมพ์เองตอนกด Receive ===")
check("ตัวเลขล้วน", parse_typed_amount("1500"), 1500.0)
check("  มีคอมมา", parse_typed_amount("1,500.00"), 1500.0)
check("  ติดหน่วยมาด้วย", parse_typed_amount("1500 THB"), 1500.0)
check("  มีช่องว่างหน้าหลัง", parse_typed_amount("  2000  "), 2000.0)
check("  ศูนย์ใช้ไม่ได้", parse_typed_amount("0"), None)
check("  ไม่ใช่ตัวเลข", parse_typed_amount("ไม่รู้"), None)
check("  ว่าง", parse_typed_amount(""), None)
check("  None", parse_typed_amount(None), None)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
