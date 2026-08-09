#!/usr/bin/env bash
#
# กู้คืนฐานข้อมูล Auto Slip จากไฟล์สำรอง
#
# ตรวจว่าไฟล์สำรองใช้ได้จริง (ปลอดภัย ไม่แตะฐานข้อมูลจริง):
#   ./scripts/restore.sh --check /var/backups/auto_slip/autoslip-XXXX.sql.gz
#
# กู้คืนจริง (เขียนทับข้อมูลปัจจุบันทั้งหมด):
#   ./scripts/restore.sh /var/backups/auto_slip/autoslip-XXXX.sql.gz
#
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"

log()  { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() { log "ล้มเหลว: $*" >&2; exit 1; }

MODE="restore"
FILE=""
for arg in "$@"; do
    case "$arg" in
        --check) MODE="check" ;;
        -h|--help)
            sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) FILE="$arg" ;;
    esac
done

[[ -n "$FILE" ]] || fail "ต้องระบุไฟล์สำรอง (ใช้ --help เพื่อดูวิธีใช้)"
[[ -f "$FILE" ]] || fail "ไม่พบไฟล์ $FILE"
gzip -t "$FILE" 2>/dev/null || fail "ไฟล์บีบอัดเสียหาย ใช้กู้คืนไม่ได้"

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
    # compose v1 ใช้ขีดล่าง (auto_slip_db_1) ส่วน v2 ใช้ขีดกลาง (auto_slip-db-1)
    [[ -n "$id" ]] || id=$(docker ps -qf 'name=[-_]db[-_]1$' | head -1)
    printf '%s' "$id"
}

CONTAINER="$(find_db_container)"
[[ -n "$CONTAINER" ]] || fail "หาคอนเทนเนอร์ฐานข้อมูลไม่เจอ"

psql_in() { docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U "$PG_USER" "$@"; }

report_counts() {
    local db="$1"
    printf '\n  %-14s %s\n' "ตาราง" "จำนวนแถว"
    printf '  %-14s %s\n' "──────────────" "────────"
    for table in transactions used_qrs approvers audit_logs; do
        local n
        n=$(docker exec "$CONTAINER" psql -tAc "select count(*) from $table" \
              -U "$PG_USER" -d "$db" 2>/dev/null | tr -d '[:space:]' || echo '-')
        printf '  %-14s %s\n' "$table" "$n"
    done
    printf '\n'
}

# ── โหมดตรวจสอบ: กู้ลงฐานข้อมูลชั่วคราวแล้วลบทิ้ง ──────────────
# ไฟล์สำรองที่กู้คืนไม่ได้ ไม่นับว่าเป็นไฟล์สำรอง จึงควรตรวจเป็นระยะ
if [[ "$MODE" == "check" ]]; then
    SCRATCH="autoslip_check_$$"
    cleanup() {
        docker exec "$CONTAINER" psql -U "$PG_USER" -d postgres \
            -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT

    log "ตรวจไฟล์ $(basename "$FILE") โดยกู้ลงฐานข้อมูลชั่วคราว"
    psql_in -d postgres -c "CREATE DATABASE $SCRATCH" >/dev/null
    zcat "$FILE" | psql_in -d "$SCRATCH" >/dev/null 2>&1 \
        || fail "กู้คืนไม่สำเร็จ — ไฟล์สำรองนี้ใช้ไม่ได้"

    log "กู้คืนสำเร็จ ข้อมูลที่อยู่ในไฟล์:"
    report_counts "$SCRATCH"
    log "ไฟล์สำรองนี้ใช้งานได้ (ฐานข้อมูลจริงไม่ถูกแตะต้อง)"
    exit 0
fi

# ── โหมดกู้คืนจริง ────────────────────────────────────────────
cat <<WARNING

  ⚠  กำลังจะเขียนทับฐานข้อมูล "$PG_DB" ทั้งหมดด้วยไฟล์
     $(basename "$FILE")

     ข้อมูลปัจจุบันทั้งหมดจะหายไป รวมถึงสลิปที่บันทึกหลังไฟล์นี้ถูกสร้าง
     ควรหยุดบอทก่อน:  docker compose stop bot

WARNING
printf '  พิมพ์ชื่อฐานข้อมูลเพื่อยืนยัน (%s): ' "$PG_DB"
read -r answer
[[ "$answer" == "$PG_DB" ]] || fail "ยกเลิก (พิมพ์ไม่ตรง)"

log "ข้อมูลก่อนกู้คืน:"
report_counts "$PG_DB"

log "กำลังกู้คืน..."
zcat "$FILE" | psql_in -d "$PG_DB" >/dev/null || fail "กู้คืนไม่สำเร็จ"

log "กู้คืนเสร็จ ข้อมูลหลังกู้คืน:"
report_counts "$PG_DB"
log "อย่าลืมเปิดบอทกลับ:  docker compose start bot"
