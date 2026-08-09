#!/usr/bin/env bash
#
# สำรองฐานข้อมูล Auto Slip
#
# ใช้: ./scripts/backup.sh
# ตั้งเวลา: ดูวิธีติดตั้ง cron ท้ายไฟล์นี้
#
# ออกแบบให้ "ล้มเหลวแล้วรู้ตัว" — ถ้าดัมป์ไม่สำเร็จจะไม่ทับไฟล์เดิม
# ไม่ลบไฟล์เก่า และคืนค่า exit ที่ไม่ใช่ 0 เพื่อให้ cron แจ้งเตือน
#
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/auto_slip}"
KEEP_DAYS="${KEEP_DAYS:-14}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"

log()  { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() { log "ล้มเหลว: $*" >&2; exit 1; }

# อ่านค่าจาก .env โดยไม่ source เพราะ source จะรันคำสั่งที่อยู่ในไฟล์ด้วย
env_value() {
    local key="$1" default="$2" line value
    [[ -f "$ENV_FILE" ]] || { printf '%s' "$default"; return; }
    line=$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -1 || true)
    [[ -n "$line" ]] || { printf '%s' "$default"; return; }
    value="${line#*=}"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    printf '%s' "$value"
}

# ค่าเริ่มต้นต้องตรงกับ docker-compose.yml
PG_USER="$(env_value POSTGRES_USER postgres)"
PG_DB="$(env_value POSTGRES_DB autoslip_db)"

find_db_container() {
    local id=""
    if docker compose version >/dev/null 2>&1; then
        id=$(docker compose -f "$PROJECT_DIR/docker-compose.yml" ps -q db 2>/dev/null || true)
    fi
    if [[ -z "$id" ]] && command -v docker-compose >/dev/null 2>&1; then
        id=$(docker-compose -f "$PROJECT_DIR/docker-compose.yml" ps -q db 2>/dev/null || true)
    fi
    # สำรองสุดท้าย: หาจากชื่อคอนเทนเนอร์ตรงๆ
    # compose v1 ใช้ขีดล่าง (auto_slip_db_1) ส่วน v2 ใช้ขีดกลาง (auto_slip-db-1)
    [[ -n "$id" ]] || id=$(docker ps -qf 'name=[-_]db[-_]1$' | head -1)
    printf '%s' "$id"
}

CONTAINER="$(find_db_container)"
[[ -n "$CONTAINER" ]] || fail "หาคอนเทนเนอร์ฐานข้อมูลไม่เจอ — ฐานข้อมูลกำลังทำงานอยู่หรือเปล่า"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/autoslip-$STAMP.sql.gz"
PARTIAL="$TARGET.partial"

# เขียนลงไฟล์ชั่วคราวก่อน แล้วค่อยเปลี่ยนชื่อเมื่อผ่านการตรวจ
# กันไม่ให้ไฟล์ที่ดัมป์ค้างกลางคันดูเหมือนเป็นไฟล์สำรองที่ใช้ได้
cleanup() { rm -f "$PARTIAL"; }
trap cleanup EXIT

log "กำลังสำรอง $PG_DB จากคอนเทนเนอร์ ${CONTAINER:0:12}"
docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --clean --if-exists \
    | gzip -9 > "$PARTIAL" \
    || fail "pg_dump ไม่สำเร็จ"

# ── ตรวจว่าไฟล์ที่ได้ใช้งานได้จริง ─────────────────────────────
gzip -t "$PARTIAL" 2>/dev/null || fail "ไฟล์บีบอัดเสียหาย"

SIZE=$(stat -c %s "$PARTIAL" 2>/dev/null || stat -f %z "$PARTIAL")
[[ "$SIZE" -gt 1000 ]] || fail "ไฟล์เล็กผิดปกติ ($SIZE ไบต์) น่าจะดัมป์ไม่ครบ"

# อ่านไฟล์รอบเดียวเก็บรายชื่อตารางไว้ก่อน อย่าใช้ `zcat | grep -q` ต่อตาราง
# เพราะ grep -q ออกทันทีที่เจอ ทำให้ zcat โดน SIGPIPE แล้ว pipefail มองว่าล้มเหลว
# ทั้งที่หาเจอแล้ว (จะพลาดเฉพาะตารางที่อยู่ต้นไฟล์ — หาบั๊กยากมาก)
DUMPED=$(zcat "$PARTIAL" | grep -oE 'CREATE TABLE (public\.)?[A-Za-z_][A-Za-z0-9_]*' || true)

for table in transactions used_qrs approvers audit_logs; do
    grep -qE "CREATE TABLE (public\.)?${table}$" <<<"$DUMPED" \
        || fail "ไม่พบตาราง ${table} ในไฟล์สำรอง"
done

mv "$PARTIAL" "$TARGET"
chmod 600 "$TARGET"
trap - EXIT

ROWS=$(docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
        'select count(*) from transactions' 2>/dev/null | tr -d '[:space:]' || echo '?')

log "สำเร็จ: $(basename "$TARGET") ($(numfmt --to=iec "$SIZE" 2>/dev/null || echo "$SIZE B"), $ROWS รายการ)"

# ── ลบไฟล์เก่า (ทำหลังสำรองสำเร็จเท่านั้น) ─────────────────────
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'autoslip-*.sql.gz' -mtime "+$KEEP_DAYS" -print -delete | wc -l)
[[ "$DELETED" -eq 0 ]] || log "ลบไฟล์เก่ากว่า $KEEP_DAYS วัน จำนวน $DELETED ไฟล์"

REMAIN=$(find "$BACKUP_DIR" -maxdepth 1 -name 'autoslip-*.sql.gz' | wc -l)
log "มีไฟล์สำรองทั้งหมด $REMAIN ไฟล์ใน $BACKUP_DIR"

# ─────────────────────────────────────────────────────────────
# ติดตั้งให้ทำงานอัตโนมัติทุกวัน ตี 3 (เวลาเครื่อง)
#
#   sudo crontab -e
#   0 3 * * * /root/Auto_Slip/scripts/backup.sh >> /var/log/autoslip-backup.log 2>&1
#
# ตรวจว่าไฟล์สำรองใช้กู้คืนได้จริง (ไม่แตะฐานข้อมูลจริง):
#   ./scripts/restore.sh --check /var/backups/auto_slip/autoslip-XXXX.sql.gz
# ─────────────────────────────────────────────────────────────
