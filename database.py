import sqlite3

def init_db():
    conn = sqlite3.connect('slips.db')
    c = conn.cursor()
    # สร้างตารางอ้างอิงด้วย Ref จาก QR Code
    c.execute('''CREATE TABLE IF NOT EXISTS slip_data
                 (ref_id TEXT PRIMARY KEY, group1_data TEXT, group2_data TEXT, status TEXT)''')
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()