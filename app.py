import os
import sys
import sqlite3
import subprocess
import threading
import time
import webbrowser
import json
import hashlib
import ast
import shutil
import re
import html
import urllib.request
import urllib.error
import io
import zipfile
import uuid
import tempfile
from datetime import datetime, timedelta

# ============================================================
# 1. TỰ KIỂM TRA / CÀI STREAMLIT + PANDAS
# ============================================================

def install_package(import_name, pip_name=None):
    try:
        __import__(import_name)
    except ImportError:
        package_name = pip_name or import_name
        print(f"Đang cài {package_name}...")
        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            package_name
        ])


install_package("streamlit")
install_package("pandas")
install_package("streamlit_paste_button", "streamlit-paste-button")


import pandas as pd
import streamlit as st
from streamlit_paste_button import paste_image_button


# ============================================================
# 2. CẤU HÌNH DATABASE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "lightingsales.db")
PRODUCT_IMAGE_DIR = os.path.join(BASE_DIR, "product_images")
os.makedirs(PRODUCT_IMAGE_DIR, exist_ok=True)
COMPANY_ASSET_DIR = os.path.join(BASE_DIR, "company_assets")
os.makedirs(COMPANY_ASSET_DIR, exist_ok=True)

# Phiên bản hiện tại và cấu hình cập nhật tự động
APP_VERSION = "3.4.1"
UPDATE_CONFIG_FILE = os.path.join(BASE_DIR, "update_config.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
DATA_BACKUP_DIR = os.path.join(BACKUP_DIR, "data")
os.makedirs(DATA_BACKUP_DIR, exist_ok=True)


def load_update_config():
    """Đọc cấu hình nguồn cập nhật."""
    default = {"manifest_url": ""}
    if not os.path.exists(UPDATE_CONFIG_FILE):
        return default
    try:
        with open(UPDATE_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default
        return {"manifest_url": str(data.get("manifest_url", "")).strip()}
    except Exception:
        return default


def save_update_config(manifest_url):
    """Lưu địa chỉ version.json để chỉ cần cấu hình một lần."""
    with open(UPDATE_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"manifest_url": manifest_url.strip()}, f, ensure_ascii=False, indent=2)


def version_tuple(version):
    """Chuyển chuỗi phiên bản 2.10.1 thành tuple để so sánh."""
    nums = re.findall(r"\d+", str(version))
    return tuple(int(x) for x in nums) if nums else (0,)


def download_bytes(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"LightingSales-CRM/{APP_VERSION}"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def check_and_update(manifest_url):
    """
    Kiểm tra version.json; nếu có bản mới thì tải app.py, xác thực, sao lưu và thay thế.
    Trả về (status, message). status: updated / latest / error.
    """
    if not manifest_url.strip():
        return "error", "Chưa cấu hình nguồn cập nhật (URL version.json)."

    try:
        manifest_raw = download_bytes(manifest_url)
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except Exception as e:
        return "error", f"Không đọc được nguồn cập nhật: {e}"

    remote_version = str(manifest.get("version", "")).strip()
    app_url = str(manifest.get("app_url", "")).strip()
    expected_sha256 = str(manifest.get("sha256", "")).strip().lower()

    if not remote_version or not app_url:
        return "error", "version.json thiếu trường 'version' hoặc 'app_url'."

    if version_tuple(remote_version) <= version_tuple(APP_VERSION):
        return "latest", f"Bạn đang dùng phiên bản mới nhất: v{APP_VERSION}."

    try:
        new_code = download_bytes(app_url, timeout=30)
        new_text = new_code.decode("utf-8")
        ast.parse(new_text)

        actual_sha256 = hashlib.sha256(new_code).hexdigest().lower()
        if expected_sha256 and actual_sha256 != expected_sha256:
            return "error", "Checksum SHA-256 không khớp. Đã hủy cập nhật để bảo vệ chương trình."

        current_app = os.path.abspath(__file__)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"app_v{APP_VERSION}_{timestamp}.py")
        shutil.copy2(current_app, backup_path)

        temp_path = current_app + ".new"
        with open(temp_path, "wb") as f:
            f.write(new_code)
        os.replace(temp_path, current_app)

        return "updated", f"Đã cập nhật từ v{APP_VERSION} lên v{remote_version}. Bản cũ đã được sao lưu."
    except Exception as e:
        return "error", f"Cập nhật không thành công: {e}"


@st.cache_resource
def get_connection():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )
    return conn


conn = get_connection()
cursor = conn.cursor()


def ensure_column(table_name, column_name, column_definition):
    """Tự thêm cột mới nếu database cũ chưa có, không làm mất dữ liệu."""
    cols = {row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in cols:
        cursor.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )
        conn.commit()


def save_product_image(uploaded_file, product_code):
    """Lưu ảnh sản phẩm vào thư mục product_images và trả về đường dẫn tương đối."""
    if uploaded_file is None:
        return ""

    ext = os.path.splitext(uploaded_file.name)[1].lower() or ".jpg"
    safe_code = "".join(ch for ch in product_code.upper() if ch.isalnum() or ch in ("-", "_"))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{safe_code}_{timestamp}{ext}"
    abs_path = os.path.join(PRODUCT_IMAGE_DIR, filename)

    with open(abs_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    return os.path.join("product_images", filename)


def save_pasted_product_image(pil_image, product_code):
    """Lưu ảnh PIL dán từ Clipboard vào product_images và trả về đường dẫn tương đối."""
    if pil_image is None:
        return ""

    safe_code = "".join(ch for ch in product_code.upper() if ch.isalnum() or ch in ("-", "_"))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{safe_code}_{timestamp}.png"
    abs_path = os.path.join(PRODUCT_IMAGE_DIR, filename)

    # Clipboard image có thể ở nhiều mode; chuyển RGBA/RGB để lưu PNG ổn định.
    image_to_save = pil_image.copy()
    if image_to_save.mode not in ("RGB", "RGBA"):
        image_to_save = image_to_save.convert("RGBA")
    image_to_save.save(abs_path, format="PNG")
    return os.path.relpath(abs_path, BASE_DIR)


def save_company_logo(uploaded_file):
    """Lưu logo công ty vào company_assets và trả về đường dẫn tương đối."""
    if uploaded_file is None:
        return ""
    ext = os.path.splitext(uploaded_file.name)[1].lower() or ".png"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"company_logo_{timestamp}{ext}"
    abs_path = os.path.join(COMPANY_ASSET_DIR, filename)
    with open(abs_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return os.path.join("company_assets", filename)


# ============================================================
# 3. TẠO DATABASE
# ============================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS khach_hang_goc (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ten TEXT NOT NULL,
    thoai TEXT UNIQUE NOT NULL,
    phan_loai_kh TEXT,
    ngay_tao TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cong_trinh_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ten_du_an TEXT NOT NULL,
    thoai_khach TEXT,
    uu_tien TEXT,
    giai_doan TEXT,
    ngay_khoi_tao TEXT,
    note TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS san_pham (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ma_code TEXT UNIQUE NOT NULL,
    ten_sp TEXT NOT NULL,
    danh_muc TEXT,
    hang TEXT,
    gia_ban REAL DEFAULT 0,
    dvt TEXT DEFAULT 'cái',
    ghi_chu TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cau_hinh_doanh_nghiep (
    id INTEGER PRIMARY KEY,
    ten_cty TEXT DEFAULT '',
    tru_so TEXT DEFAULT '',
    vpdd TEXT DEFAULT '',
    sdt TEXT DEFAULT '',
    email TEXT DEFAULT '',
    website TEXT DEFAULT '',
    facebook TEXT DEFAULT ''
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cong_viec_lich_su (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cong_trinh_id INTEGER,
    ten_du_an TEXT DEFAULT '',
    noi_dung TEXT DEFAULT '',
    ngay_hen TEXT DEFAULT '',
    ghi_chu TEXT DEFAULT '',
    ngay_hoan_thanh TEXT DEFAULT ''
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cong_trinh_hoat_dong (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cong_trinh_id INTEGER NOT NULL,
    loai TEXT DEFAULT 'Follow-up',
    noi_dung TEXT DEFAULT '',
    ngay_hen TEXT DEFAULT '',
    trang_thai TEXT DEFAULT 'Đang làm',
    uu_tien TEXT DEFAULT 'Medium',
    ghi_chu TEXT DEFAULT '',
    ngay_tao TEXT DEFAULT '',
    ngay_hoan_thanh TEXT DEFAULT ''
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS bao_gia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    so_bao_gia TEXT UNIQUE NOT NULL,
    cong_trinh_id INTEGER,
    ten_du_an_snapshot TEXT DEFAULT '',
    thoai_khach TEXT DEFAULT '',
    ten_khach_snapshot TEXT DEFAULT '',
    ngay_bao_gia TEXT DEFAULT '',
    trang_thai TEXT DEFAULT 'Nháp',
    ghi_chu TEXT DEFAULT '',
    vat_percent REAL DEFAULT 0,
    tong_truoc_thue REAL DEFAULT 0,
    tien_vat REAL DEFAULT 0,
    tong_thanh_toan REAL DEFAULT 0,
    ngay_tao TEXT DEFAULT '',
    ngay_cap_nhat TEXT DEFAULT ''
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS bao_gia_chi_tiet (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bao_gia_id INTEGER NOT NULL,
    san_pham_id INTEGER,
    sku TEXT DEFAULT '',
    ma_code_snapshot TEXT DEFAULT '',
    ten_sp_snapshot TEXT DEFAULT '',
    mo_ta_snapshot TEXT DEFAULT '',
    hang_snapshot TEXT DEFAULT '',
    dvt_snapshot TEXT DEFAULT '',
    so_luong REAL DEFAULT 1,
    don_gia REAL DEFAULT 0,
    chiet_khau_percent REAL DEFAULT 0,
    thanh_tien REAL DEFAULT 0,
    ghi_chu TEXT DEFAULT ''
)
""")

# Nền tảng AI Agent: migration, hàng chờ phê duyệt và nhật ký bất biến.
cursor.execute("""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT DEFAULT '',
    applied_at TEXT DEFAULT ''
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS agent_action_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_uuid TEXT UNIQUE NOT NULL,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    summary TEXT DEFAULT '',
    status TEXT DEFAULT 'Chờ duyệt',
    requested_by TEXT DEFAULT 'AI Agent',
    created_at TEXT DEFAULT '',
    reviewed_at TEXT DEFAULT '',
    review_note TEXT DEFAULT '',
    error_message TEXT DEFAULT ''
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS agent_action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_uuid TEXT NOT NULL,
    event_type TEXT NOT NULL,
    action_type TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    payload_json TEXT DEFAULT '',
    result_json TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS idx_agent_queue_status
ON agent_action_queue(status, id)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS idx_agent_log_uuid
ON agent_action_log(action_uuid, id)
""")

cursor.execute("""
INSERT OR IGNORE INTO schema_migrations (version, description, applied_at)
VALUES (3400, 'B1 backup/restore + B2 safe Agent tool layer', ?)
""", (datetime.now().strftime("%d/%m/%Y %H:%M:%S"),))

conn.commit()

cursor.execute("""
INSERT OR IGNORE INTO schema_migrations(version, description, applied_at)
VALUES (340, 'B1 bảo vệ dữ liệu và B2 nền tảng công cụ AI Agent', ?)
""", (datetime.now().strftime("%d/%m/%Y %H:%M:%S"),))
conn.commit()

# Nâng cấp database cũ: chỉ thêm cột, tuyệt đối không xóa dữ liệu hiện có.
ensure_column("khach_hang_goc", "ten_cong_ty", "TEXT DEFAULT ''")
ensure_column("khach_hang_goc", "dia_chi_cong_ty", "TEXT DEFAULT ''")
ensure_column("cong_trinh_new", "dia_chi_cong_trinh", "TEXT DEFAULT ''")
ensure_column("cong_trinh_new", "viec_tiep_theo", "TEXT DEFAULT ''")
ensure_column("cong_trinh_new", "ngay_theo_doi", "TEXT DEFAULT ''")
ensure_column("cong_trinh_new", "ghi_chu_cong_viec", "TEXT DEFAULT ''")
ensure_column("cong_trinh_new", "gia_tri_du_kien", "REAL DEFAULT 0")
ensure_column("san_pham", "mo_ta", "TEXT DEFAULT ''")
ensure_column("san_pham", "hinh_anh", "TEXT DEFAULT ''")
ensure_column("cau_hinh_doanh_nghiep", "logo_path", "TEXT DEFAULT ''")


# Chuyển công việc hiện có sang Activity Manager một lần, không tạo trùng.
try:
    legacy_projects = cursor.execute("""
        SELECT id, viec_tiep_theo, ngay_theo_doi, ghi_chu_cong_viec, uu_tien
        FROM cong_trinh_new
        WHERE TRIM(COALESCE(viec_tiep_theo, '')) <> ''
    """).fetchall()
    for p in legacy_projects:
        existing_count = cursor.execute(
            "SELECT COUNT(*) FROM cong_trinh_hoat_dong WHERE cong_trinh_id=?",
            (int(p[0]),)
        ).fetchone()[0]
        if existing_count == 0:
            cursor.execute("""
                INSERT INTO cong_trinh_hoat_dong
                (cong_trinh_id, loai, noi_dung, ngay_hen, trang_thai, uu_tien, ghi_chu, ngay_tao)
                VALUES (?, 'Follow-up', ?, ?, 'Đang làm', ?, ?, ?)
            """, (
                int(p[0]),
                str(p[1] or "").strip(),
                str(p[2] or "").strip(),
                str(p[4] or "Medium"),
                str(p[3] or "").strip(),
                datetime.now().strftime("%d/%m/%Y %H:%M")
            ))
    conn.commit()
except Exception:
    pass


# ============================================================
# 4. HÀM ĐỌC DATABASE
# ============================================================

def load_data():

    df_kh = pd.read_sql_query("""
        SELECT
            id,
            ten,
            thoai,
            ten_cong_ty,
            dia_chi_cong_ty,
            phan_loai_kh,
            ngay_tao
        FROM khach_hang_goc
        ORDER BY id DESC
    """, conn)

    df_ct = pd.read_sql_query("""
        SELECT
            id,
            ten_du_an,
            thoai_khach,
            dia_chi_cong_trinh,
            uu_tien,
            giai_doan,
            ngay_khoi_tao,
            viec_tiep_theo,
            ngay_theo_doi,
            ghi_chu_cong_viec,
            gia_tri_du_kien,
            note
        FROM cong_trinh_new
        ORDER BY id DESC
    """, conn)

    df_sp = pd.read_sql_query("""
        SELECT
            id,
            ma_code,
            ten_sp,
            danh_muc,
            hang,
            gia_ban,
            dvt,
            mo_ta,
            hinh_anh,
            ghi_chu
        FROM san_pham
        ORDER BY id DESC
    """, conn)

    df_cty = pd.read_sql_query("""
        SELECT
            ten_cty,
            tru_so,
            vpdd,
            sdt,
            email,
            website,
            facebook,
            logo_path
        FROM cau_hinh_doanh_nghiep
        WHERE id = 1
    """, conn)

    return df_kh, df_ct, df_sp, df_cty


def parse_activity_date(value):
    """Đọc ngày dd/mm/yyyy cho Activity Manager."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def sync_project_next_action(project_id, commit=True):
    """
    Đồng bộ Activity đang mở gần nhất về 3 cột legacy để Dashboard cũ
    tiếp tục hoạt động và luôn hiển thị việc gần nhất.
    """
    rows = cursor.execute("""
        SELECT id, noi_dung, ngay_hen, ghi_chu
        FROM cong_trinh_hoat_dong
        WHERE cong_trinh_id=? AND trang_thai='Đang làm'
        ORDER BY id ASC
    """, (int(project_id),)).fetchall()

    if not rows:
        cursor.execute("""
            UPDATE cong_trinh_new
            SET viec_tiep_theo='', ngay_theo_doi='', ghi_chu_cong_viec=''
            WHERE id=?
        """, (int(project_id),))
        if commit:
            conn.commit()
        return

    today = datetime.now().date()
    def sort_key(r):
        d = parse_activity_date(r[2])
        return (0 if d else 1, d or today, int(r[0]))

    selected = sorted(rows, key=sort_key)[0]
    cursor.execute("""
        UPDATE cong_trinh_new
        SET viec_tiep_theo=?, ngay_theo_doi=?, ghi_chu_cong_viec=?
        WHERE id=?
    """, (
        str(selected[1] or "").strip(),
        str(selected[2] or "").strip(),
        str(selected[3] or "").strip(),
        int(project_id)
    ))
    if commit:
        conn.commit()



def generate_quote_number():
    """Sinh số báo giá dễ đọc và không trùng."""
    today_code = datetime.now().strftime("%y%m%d")
    row = cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM bao_gia").fetchone()
    next_id = int(row[0] or 1)
    return f"QT-{today_code}-{next_id:04d}"


def money_vnd(value):
    try:
        return f"{float(value):,.0f} đ"
    except Exception:
        return "0 đ"


def get_project_customer(project_id):
    """Lấy snapshot công trình + khách hàng để lưu độc lập vào báo giá."""
    row = cursor.execute("""
        SELECT
            c.id,
            c.ten_du_an,
            COALESCE(c.thoai_khach, ''),
            COALESCE(k.ten, ''),
            COALESCE(k.ten_cong_ty, '')
        FROM cong_trinh_new c
        LEFT JOIN khach_hang_goc k ON k.thoai = c.thoai_khach
        WHERE c.id=?
    """, (int(project_id),)).fetchone()
    return row


def deal_health_label(stage, next_action, follow_date_text):
    if str(stage or "").strip() == "Hoàn thành":
        return "✓ Closed"
    action = str(next_action or "").strip()
    due = parse_activity_date(follow_date_text)
    today = datetime.now().date()
    if due and due < today:
        return "🔴 At Risk"
    if not action or due is None:
        return "🟡 Attention"
    return "🟢 Healthy"


# ============================================================
# B1 - BẢO VỆ, SAO LƯU VÀ KHÔI PHỤC DỮ LIỆU
# ============================================================

def database_health():
    """Kiểm tra database và trả về thông tin đủ gọn để hiển thị trên CRM."""
    integrity_row = cursor.execute("PRAGMA integrity_check").fetchone()
    integrity = str(integrity_row[0] if integrity_row else "unknown")
    table_counts = {}
    for table_name in (
        "khach_hang_goc", "cong_trinh_new", "cong_trinh_hoat_dong",
        "cong_viec_lich_su", "san_pham", "bao_gia", "bao_gia_chi_tiet",
        "agent_action_queue", "agent_action_log"
    ):
        table_counts[table_name] = int(
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0
        )
    return {
        "ok": integrity.lower() == "ok",
        "integrity": integrity,
        "database_size": os.path.getsize(DB_FILE) if os.path.exists(DB_FILE) else 0,
        "table_counts": table_counts,
    }


def _zip_directory(zip_file, source_dir, archive_root):
    if not os.path.isdir(source_dir):
        return
    for root, _, files in os.walk(source_dir):
        for filename in files:
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, source_dir)
            zip_file.write(abs_path, os.path.join(archive_root, rel_path))


def create_full_backup(trigger="manual", note=""):
    """Tạo ZIP chứa snapshot SQLite, ảnh, logo, app và manifest kiểm tra."""
    safe_trigger = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(trigger))[:30] or "manual"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_name = f"lightingsales_{timestamp}_{safe_trigger}.zip"
    backup_path = os.path.join(DATA_BACKUP_DIR, backup_name)

    health = database_health()
    if not health["ok"]:
        raise RuntimeError(f"Database không đạt kiểm tra integrity: {health['integrity']}")

    with tempfile.TemporaryDirectory(prefix="lightingsales_backup_") as temp_dir:
        snapshot_path = os.path.join(temp_dir, "lightingsales.db")
        snapshot_conn = sqlite3.connect(snapshot_path)
        try:
            conn.backup(snapshot_conn)
        finally:
            snapshot_conn.close()

        snapshot_check = sqlite3.connect(snapshot_path)
        try:
            check = snapshot_check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            snapshot_check.close()
        if str(check).lower() != "ok":
            raise RuntimeError("Snapshot database không đạt kiểm tra integrity.")

        with open(snapshot_path, "rb") as snapshot_file:
            database_hash = hashlib.sha256(snapshot_file.read()).hexdigest()

        manifest = {
            "format": "LightingSales CRM Backup",
            "format_version": 1,
            "app_version": APP_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "trigger": safe_trigger,
            "note": str(note or "").strip(),
            "database_sha256": database_hash,
            "table_counts": health["table_counts"],
        }

        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot_path, "database/lightingsales.db")
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            current_app = os.path.abspath(__file__)
            if os.path.isfile(current_app):
                zf.write(current_app, "application/app.py")
            if os.path.isfile(UPDATE_CONFIG_FILE):
                zf.write(UPDATE_CONFIG_FILE, "application/update_config.json")
            _zip_directory(zf, PRODUCT_IMAGE_DIR, "product_images")
            _zip_directory(zf, COMPANY_ASSET_DIR, "company_assets")
    return backup_path


def list_data_backups():
    backups = []
    if not os.path.isdir(DATA_BACKUP_DIR):
        return backups
    for filename in os.listdir(DATA_BACKUP_DIR):
        path = os.path.join(DATA_BACKUP_DIR, filename)
        if filename.lower().endswith(".zip") and os.path.isfile(path):
            backups.append({
                "name": filename,
                "path": path,
                "size": os.path.getsize(path),
                "modified": datetime.fromtimestamp(os.path.getmtime(path)),
            })
    return sorted(backups, key=lambda x: x["modified"], reverse=True)


def prune_automatic_backups(keep=14):
    automatic = [x for x in list_data_backups() if x["name"].endswith("_daily.zip")]
    for item in automatic[max(int(keep), 1):]:
        try:
            os.remove(item["path"])
        except OSError:
            pass


def ensure_daily_backup():
    today_code = datetime.now().strftime("%Y%m%d")
    exists = any(
        x["name"].startswith(f"lightingsales_{today_code}_")
        and x["name"].endswith("_daily.zip")
        for x in list_data_backups()
    )
    if not exists:
        path = create_full_backup("daily", "Bản sao lưu tự động đầu ngày")
        prune_automatic_backups(14)
        return path
    return ""


def _validate_backup_archive(zip_path):
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if sum(info.file_size for info in zf.infolist()) > 2 * 1024 * 1024 * 1024:
            raise ValueError("Dung lượng giải nén của backup vượt quá giới hạn 2 GB.")
        for name in names:
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError("File backup chứa đường dẫn không an toàn.")
        if "database/lightingsales.db" not in names or "manifest.json" not in names:
            raise ValueError("Đây không phải file backup đầy đủ của LightingSales CRM.")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        if manifest.get("format") != "LightingSales CRM Backup":
            raise ValueError("Định dạng backup không đúng.")
        database_bytes = zf.read("database/lightingsales.db")
        expected_hash = str(manifest.get("database_sha256", "")).strip().lower()
        actual_hash = hashlib.sha256(database_bytes).hexdigest().lower()
        if not expected_hash or expected_hash != actual_hash:
            raise ValueError("Checksum database trong backup không khớp.")
    return manifest


def restore_full_backup(uploaded_bytes):
    """Khôi phục database và tài sản; luôn tạo backup hiện trạng trước khi phục hồi."""
    if not uploaded_bytes:
        raise ValueError("File backup trống.")
    if len(uploaded_bytes) > 500 * 1024 * 1024:
        raise ValueError("File backup vượt quá giới hạn 500 MB.")

    with tempfile.TemporaryDirectory(prefix="lightingsales_restore_") as temp_dir:
        archive_path = os.path.join(temp_dir, "restore.zip")
        with open(archive_path, "wb") as f:
            f.write(uploaded_bytes)
        manifest = _validate_backup_archive(archive_path)
        pre_restore_path = create_full_backup("pre_restore", "Tự động tạo trước khi khôi phục")

        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(temp_dir)

        restored_db_path = os.path.join(temp_dir, "database", "lightingsales.db")
        restored_conn = sqlite3.connect(restored_db_path)
        try:
            check = restored_conn.execute("PRAGMA integrity_check").fetchone()[0]
            if str(check).lower() != "ok":
                raise ValueError("Database trong backup bị lỗi.")
            restored_conn.backup(conn)
        finally:
            restored_conn.close()

        for folder_name, destination in (
            ("product_images", PRODUCT_IMAGE_DIR),
            ("company_assets", COMPANY_ASSET_DIR),
        ):
            source = os.path.join(temp_dir, folder_name)
            if os.path.isdir(source):
                shutil.copytree(source, destination, dirs_exist_ok=True)
        conn.commit()
    return manifest, pre_restore_path


# ============================================================
# B2 - LỚP CÔNG CỤ CRM AN TOÀN CHO AI AGENT
# ============================================================

AGENT_ALLOWED_ACTIONS = {
    "CREATE_ACTIVITY",
    "RESCHEDULE_ACTIVITY",
    "COMPLETE_ACTIVITY",
    "UPDATE_PROJECT_STAGE",
    "CREATE_QUOTE_DRAFT",
}
AGENT_PROJECT_STAGES = {
    "Tiếp cận", "Khảo sát", "Báo giá", "Thương lượng",
    "Chốt đơn", "Triển khai", "Hoàn thành", "Tạm dừng"
}
AGENT_PRIORITIES = {"High", "Medium", "Low"}


class AgentActionError(ValueError):
    pass


def _clean_text(value, field_name, required=False, max_length=2000):
    text_value = str(value or "").strip()
    if required and not text_value:
        raise AgentActionError(f"Thiếu {field_name}.")
    if len(text_value) > max_length:
        raise AgentActionError(f"{field_name} vượt quá {max_length} ký tự.")
    return text_value


def _require_positive_id(value, field_name):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise AgentActionError(f"{field_name} không hợp lệ.")
    if result <= 0:
        raise AgentActionError(f"{field_name} không hợp lệ.")
    return result


def _require_date(value, field_name="ngày"):
    parsed = parse_activity_date(value)
    if parsed is None:
        raise AgentActionError(f"{field_name} phải theo định dạng dd/mm/yyyy.")
    return parsed.strftime("%d/%m/%Y")


def agent_search_customers(query, limit=20):
    q = _clean_text(query, "từ khóa", required=True, max_length=200)
    return pd.read_sql_query("""
        SELECT id, ten, thoai, ten_cong_ty, dia_chi_cong_ty, phan_loai_kh
        FROM khach_hang_goc
        WHERE ten LIKE ? OR thoai LIKE ? OR ten_cong_ty LIKE ?
        ORDER BY id DESC LIMIT ?
    """, conn, params=(f"%{q}%", f"%{q}%", f"%{q}%", min(max(int(limit), 1), 100)))


def agent_search_projects(query, limit=20):
    q = _clean_text(query, "từ khóa", required=True, max_length=200)
    return pd.read_sql_query("""
        SELECT id, ten_du_an, thoai_khach, dia_chi_cong_trinh,
               uu_tien, giai_doan, viec_tiep_theo, ngay_theo_doi
        FROM cong_trinh_new
        WHERE ten_du_an LIKE ? OR dia_chi_cong_trinh LIKE ? OR thoai_khach LIKE ?
        ORDER BY id DESC LIMIT ?
    """, conn, params=(f"%{q}%", f"%{q}%", f"%{q}%", min(max(int(limit), 1), 100)))


def agent_find_product_exact(product_code):
    """Chỉ trả sản phẩm khi mã khớp tuyệt đối; không dùng mã gần giống."""
    code = _clean_text(product_code, "mã sản phẩm", required=True, max_length=200)
    rows = pd.read_sql_query("""
        SELECT id, ma_code, ten_sp, danh_muc, hang, gia_ban, dvt, mo_ta
        FROM san_pham
        WHERE ma_code = ? COLLATE BINARY
        LIMIT 2
    """, conn, params=(code,))
    return rows


def _agent_log(action_uuid, event_type, action_type="", summary="", payload=None, result=None):
    cursor.execute("""
        INSERT INTO agent_action_log
        (action_uuid, event_type, action_type, summary, payload_json, result_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        str(action_uuid), str(event_type), str(action_type), str(summary or ""),
        json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        json.dumps(result or {}, ensure_ascii=False, sort_keys=True),
        datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    ))


def queue_agent_action(action_type, payload, summary, requested_by="AI Agent"):
    action_type = _clean_text(action_type, "loại hành động", required=True, max_length=80).upper()
    if action_type not in AGENT_ALLOWED_ACTIONS:
        raise AgentActionError("Hành động này chưa được CRM cho phép.")
    if not isinstance(payload, dict):
        raise AgentActionError("Dữ liệu hành động phải là object.")
    summary = _clean_text(summary, "mô tả hành động", required=True, max_length=500)
    action_uuid = str(uuid.uuid4())
    now_text = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    cursor.execute("""
        INSERT INTO agent_action_queue
        (action_uuid, action_type, payload_json, summary, status, requested_by, created_at)
        VALUES (?, ?, ?, ?, 'Chờ duyệt', ?, ?)
    """, (action_uuid, action_type, payload_json, summary, requested_by, now_text))
    _agent_log(action_uuid, "QUEUED", action_type, summary, payload=payload)
    conn.commit()
    return action_uuid


def reject_agent_action(action_uuid, review_note=""):
    action_uuid = _clean_text(action_uuid, "mã hành động", required=True, max_length=80)
    row = cursor.execute("""
        SELECT action_type, summary, payload_json, status
        FROM agent_action_queue WHERE action_uuid=?
    """, (action_uuid,)).fetchone()
    if not row:
        raise AgentActionError("Không tìm thấy hành động.")
    if row[3] != "Chờ duyệt":
        raise AgentActionError("Chỉ có thể từ chối hành động đang chờ duyệt.")
    now_text = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    cursor.execute("""
        UPDATE agent_action_queue
        SET status='Đã từ chối', reviewed_at=?, review_note=?
        WHERE action_uuid=? AND status='Chờ duyệt'
    """, (now_text, _clean_text(review_note, "ghi chú", max_length=1000), action_uuid))
    _agent_log(action_uuid, "REJECTED", row[0], row[1], json.loads(row[2]), {"review_note": review_note})
    conn.commit()


def _execute_agent_tool(action_type, payload):
    now_text = datetime.now().strftime("%d/%m/%Y %H:%M")

    if action_type == "CREATE_ACTIVITY":
        project_id = _require_positive_id(payload.get("project_id"), "project_id")
        project = cursor.execute(
            "SELECT ten_du_an FROM cong_trinh_new WHERE id=?", (project_id,)
        ).fetchone()
        if not project:
            raise AgentActionError("Không tìm thấy công trình chính xác.")
        content = _clean_text(payload.get("content"), "nội dung công việc", required=True)
        due_date = _require_date(payload.get("due_date"), "ngày hẹn")
        priority = str(payload.get("priority") or "Medium")
        if priority not in AGENT_PRIORITIES:
            raise AgentActionError("Mức ưu tiên không hợp lệ.")
        activity_type = _clean_text(payload.get("activity_type") or "Follow-up", "loại Activity", max_length=100)
        note = _clean_text(payload.get("note"), "ghi chú")
        cursor.execute("""
            INSERT INTO cong_trinh_hoat_dong
            (cong_trinh_id, loai, noi_dung, ngay_hen, trang_thai, uu_tien, ghi_chu, ngay_tao)
            VALUES (?, ?, ?, ?, 'Đang làm', ?, ?, ?)
        """, (project_id, activity_type, content, due_date, priority, note, now_text))
        activity_id = int(cursor.lastrowid)
        sync_project_next_action(project_id, commit=False)
        return {"activity_id": activity_id, "project_id": project_id}

    if action_type == "RESCHEDULE_ACTIVITY":
        activity_id = _require_positive_id(payload.get("activity_id"), "activity_id")
        due_date = _require_date(payload.get("due_date"), "ngày hẹn mới")
        row = cursor.execute("""
            SELECT cong_trinh_id FROM cong_trinh_hoat_dong
            WHERE id=? AND trang_thai='Đang làm'
        """, (activity_id,)).fetchone()
        if not row:
            raise AgentActionError("Không tìm thấy Activity đang làm.")
        note = payload.get("note")
        if note is None:
            cursor.execute(
                "UPDATE cong_trinh_hoat_dong SET ngay_hen=? WHERE id=?",
                (due_date, activity_id)
            )
        else:
            cursor.execute(
                "UPDATE cong_trinh_hoat_dong SET ngay_hen=?, ghi_chu=? WHERE id=?",
                (due_date, _clean_text(note, "ghi chú"), activity_id)
            )
        sync_project_next_action(int(row[0]), commit=False)
        return {"activity_id": activity_id, "due_date": due_date}

    if action_type == "COMPLETE_ACTIVITY":
        activity_id = _require_positive_id(payload.get("activity_id"), "activity_id")
        row = cursor.execute("""
            SELECT a.cong_trinh_id, c.ten_du_an, a.noi_dung, a.ngay_hen, a.ghi_chu
            FROM cong_trinh_hoat_dong a
            JOIN cong_trinh_new c ON c.id=a.cong_trinh_id
            WHERE a.id=? AND a.trang_thai='Đang làm'
        """, (activity_id,)).fetchone()
        if not row:
            raise AgentActionError("Không tìm thấy Activity đang làm.")
        completion_note = _clean_text(payload.get("note") or row[4], "ghi chú")
        cursor.execute("""
            UPDATE cong_trinh_hoat_dong
            SET trang_thai='Đã hoàn thành', ghi_chu=?, ngay_hoan_thanh=?
            WHERE id=? AND trang_thai='Đang làm'
        """, (completion_note, now_text, activity_id))
        cursor.execute("""
            INSERT INTO cong_viec_lich_su
            (cong_trinh_id, ten_du_an, noi_dung, ngay_hen, ghi_chu, ngay_hoan_thanh)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (int(row[0]), row[1], row[2], row[3], completion_note, now_text))
        sync_project_next_action(int(row[0]), commit=False)
        return {"activity_id": activity_id, "completed_at": now_text}

    if action_type == "UPDATE_PROJECT_STAGE":
        project_id = _require_positive_id(payload.get("project_id"), "project_id")
        stage = _clean_text(payload.get("stage"), "giai đoạn", required=True, max_length=100)
        if stage not in AGENT_PROJECT_STAGES:
            raise AgentActionError("Giai đoạn công trình không hợp lệ.")
        cursor.execute("UPDATE cong_trinh_new SET giai_doan=? WHERE id=?", (stage, project_id))
        if cursor.rowcount != 1:
            raise AgentActionError("Không tìm thấy công trình chính xác.")
        return {"project_id": project_id, "stage": stage}

    if action_type == "CREATE_QUOTE_DRAFT":
        project_id = _require_positive_id(payload.get("project_id"), "project_id")
        project = get_project_customer(project_id)
        if not project:
            raise AgentActionError("Không tìm thấy công trình chính xác.")
        quote_date = _require_date(payload.get("quote_date"), "ngày báo giá")
        if "vat_percent" not in payload:
            raise AgentActionError("Thiếu VAT; Agent không được tự đoán VAT.")
        try:
            vat_percent = float(payload.get("vat_percent"))
        except (TypeError, ValueError):
            raise AgentActionError("VAT không hợp lệ.")
        if not 0 <= vat_percent <= 20:
            raise AgentActionError("VAT phải nằm trong khoảng 0–20%.")
        lines = payload.get("lines")
        if not isinstance(lines, list) or not lines:
            raise AgentActionError("Báo giá phải có ít nhất một dòng sản phẩm.")

        verified_lines = []
        for index, line in enumerate(lines, start=1):
            if not isinstance(line, dict):
                raise AgentActionError(f"Dòng {index} không hợp lệ.")
            product_id = _require_positive_id(line.get("product_id"), f"product_id dòng {index}")
            product_code = _clean_text(line.get("product_code"), f"mã sản phẩm dòng {index}", required=True, max_length=200)
            product = cursor.execute("""
                SELECT id, ma_code, ten_sp, hang, mo_ta, dvt
                FROM san_pham WHERE id=? AND ma_code=? COLLATE BINARY
            """, (product_id, product_code)).fetchone()
            if not product:
                raise AgentActionError(f"Dòng {index}: mã sản phẩm không khớp chính xác.")
            sku = _clean_text(line.get("sku"), f"SKU dòng {index}", required=True, max_length=200)
            if "unit_price" not in line:
                raise AgentActionError(f"Dòng {index}: thiếu đơn giá; Agent không được tự điền giá.")
            try:
                quantity = float(line.get("quantity"))
                unit_price = float(line.get("unit_price"))
                discount = float(line.get("discount_percent", 0))
            except (TypeError, ValueError):
                raise AgentActionError(f"Dòng {index}: số lượng, đơn giá hoặc chiết khấu không hợp lệ.")
            if quantity <= 0 or unit_price < 0 or not 0 <= discount <= 100:
                raise AgentActionError(f"Dòng {index}: giá trị số nằm ngoài phạm vi cho phép.")
            amount = quantity * unit_price * (1 - discount / 100)
            verified_lines.append({
                "product": product, "sku": sku, "quantity": quantity,
                "unit_price": unit_price, "discount": discount, "amount": amount,
                "description": _clean_text(line.get("description") or product[4], "mô tả"),
                "note": _clean_text(line.get("note"), "ghi chú dòng"),
            })

        subtotal = sum(x["amount"] for x in verified_lines)
        vat_amount = subtotal * vat_percent / 100
        grand_total = subtotal + vat_amount
        quote_no = generate_quote_number()
        cursor.execute("""
            INSERT INTO bao_gia
            (so_bao_gia, cong_trinh_id, ten_du_an_snapshot, thoai_khach,
             ten_khach_snapshot, ngay_bao_gia, trang_thai, ghi_chu, vat_percent,
             tong_truoc_thue, tien_vat, tong_thanh_toan, ngay_tao, ngay_cap_nhat)
            VALUES (?, ?, ?, ?, ?, ?, 'Nháp', ?, ?, ?, ?, ?, ?, ?)
        """, (
            quote_no, project_id, str(project[1] or ""), str(project[2] or ""),
            str(project[3] or project[4] or ""), quote_date,
            _clean_text(payload.get("note"), "ghi chú báo giá"), vat_percent,
            subtotal, vat_amount, grand_total, now_text, now_text
        ))
        quote_id = int(cursor.lastrowid)
        for item in verified_lines:
            product = item["product"]
            cursor.execute("""
                INSERT INTO bao_gia_chi_tiet
                (bao_gia_id, san_pham_id, sku, ma_code_snapshot, ten_sp_snapshot,
                 mo_ta_snapshot, hang_snapshot, dvt_snapshot, so_luong, don_gia,
                 chiet_khau_percent, thanh_tien, ghi_chu)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                quote_id, int(product[0]), item["sku"], product[1], product[2],
                item["description"], product[3], product[5], item["quantity"],
                item["unit_price"], item["discount"], item["amount"], item["note"]
            ))
        cursor.execute(
            "UPDATE cong_trinh_new SET gia_tri_du_kien=? WHERE id=?",
            (grand_total, project_id)
        )
        return {"quote_id": quote_id, "quote_number": quote_no, "total": grand_total}

    raise AgentActionError("Hành động chưa có công cụ thực thi.")


def approve_and_execute_agent_action(action_uuid, review_note=""):
    """Duyệt và thực thi đúng một lần; thất bại sẽ rollback toàn bộ nghiệp vụ."""
    action_uuid = _clean_text(action_uuid, "mã hành động", required=True, max_length=80)
    row = cursor.execute("""
        SELECT action_type, payload_json, summary, status
        FROM agent_action_queue WHERE action_uuid=?
    """, (action_uuid,)).fetchone()
    if not row:
        raise AgentActionError("Không tìm thấy hành động.")
    action_type, payload_json, summary, status = row
    if status != "Chờ duyệt":
        raise AgentActionError("Hành động này đã được xử lý trước đó.")
    payload = json.loads(payload_json)
    now_text = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor.execute("""
            UPDATE agent_action_queue
            SET status='Đang thực hiện', reviewed_at=?, review_note=?
            WHERE action_uuid=? AND status='Chờ duyệt'
        """, (now_text, _clean_text(review_note, "ghi chú duyệt", max_length=1000), action_uuid))
        if cursor.rowcount != 1:
            raise AgentActionError("Hành động đã được phiên khác xử lý.")
        result = _execute_agent_tool(action_type, payload)
        cursor.execute("""
            UPDATE agent_action_queue SET status='Đã thực hiện', error_message=''
            WHERE action_uuid=?
        """, (action_uuid,))
        _agent_log(action_uuid, "EXECUTED", action_type, summary, payload, result)
        conn.commit()
        return result
    except Exception as exc:
        conn.rollback()
        cursor.execute("""
            UPDATE agent_action_queue
            SET status='Thực hiện lỗi', reviewed_at=?, error_message=?
            WHERE action_uuid=? AND status='Chờ duyệt'
        """, (now_text, str(exc)[:1000], action_uuid))
        _agent_log(action_uuid, "FAILED", action_type, summary, payload, {"error": str(exc)})
        conn.commit()
        raise


def get_agent_queue(status="Chờ duyệt", limit=100):
    sql = """
        SELECT id, action_uuid, action_type, summary, status,
               requested_by, created_at, reviewed_at, review_note, error_message
        FROM agent_action_queue
    """
    params = []
    if status:
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(min(max(int(limit), 1), 500))
    return pd.read_sql_query(sql, conn, params=tuple(params))


# Tạo tối đa một backup tự động mỗi ngày. Nếu thất bại, CRM vẫn mở và báo tại Cài đặt.
DAILY_BACKUP_ERROR = ""
try:
    ensure_daily_backup()
except Exception as backup_error:
    DAILY_BACKUP_ERROR = str(backup_error)


# ============================================================
# 5. GIAO DIỆN - MODERN BUSINESS CRM
# ============================================================

st.set_page_config(
    page_title="LightingSales CRM",
    page_icon="💡",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #F6F8FC; }
[data-testid="stHeader"] { background: rgba(246,248,252,.88); }
.block-container { padding-top: 1.5rem; padding-bottom: 2.2rem; max-width: 1500px; }
h1, h2, h3 { letter-spacing: -0.02em; }
[data-testid="stSidebar"] {
    background: #F5F7FA;
    border-right: 1px solid #E2E8F0;
    min-width: 278px !important;
    max-width: 278px !important;
}
[data-testid="stSidebar"] > div:first-child { width: 278px !important; }
[data-testid="stSidebar"] * { color: #1F2937; }
[data-testid="stSidebar"] .stRadio label { font-size: 0.94rem; font-weight: 600; }
[data-testid="stSidebar"] div[role="radiogroup"] label {
    padding: 0.60rem 0.72rem;
    border-radius: 10px;
    margin: 0.10rem 0;
    transition: background .15s ease, box-shadow .15s ease;
}
[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
    background: #EAF0F8;
}
[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
    background: #FFFFFF;
    box-shadow: 0 1px 3px rgba(15,23,42,.08);
    border-left: 3px solid #2563EB;
}
[data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child {
    display: none;
}
[data-testid="stSidebar"] .stButton button {
    border-radius: 10px;
    border: 1px solid #DCE3EC;
    background: #FFFFFF;
    color: #1F2937;
}
[data-testid="stSidebar"] .stButton button:hover {
    border-color: #B8C5D6;
    background: #F8FAFC;
}
[data-testid="stSidebar"] .stExpander {
    border: 1px solid #E2E8F0;
    background: #FFFFFF;
    border-radius: 12px;
}
[data-testid="stSidebar"] input {
    background: #FFFFFF !important;
    color: #1F2937 !important;
}
.sidebar-brand {
    padding: 0.35rem 0 1.05rem 0;
    border-bottom: 1px solid #E2E8F0;
    margin-bottom: .85rem;
}
.sidebar-brand .brand-name {
    font-size: 1.08rem;
    font-weight: 800;
    color: #0F172A;
}
.sidebar-brand .brand-sub {
    font-size: .76rem;
    color: #64748B;
    margin-top: .18rem;
}
.sidebar-section {
    color: #64748B;
    font-size: .70rem;
    font-weight: 800;
    letter-spacing: .08em;
    margin: .9rem 0 .32rem 0;
}
.pipeline-stage {
    background: #F8FAFC;
    border: 1px solid #E2E8F0;
    border-radius: 14px;
    padding: 12px;
    min-height: 160px;
    margin-bottom: 14px;
}
.pipeline-stage-title {
    font-size: .88rem;
    font-weight: 800;
    color: #0F172A;
    margin-bottom: 2px;
}
.pipeline-stage-count {
    font-size: .73rem;
    color: #64748B;
    margin-bottom: 10px;
}
.project-card {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 11px 12px;
    margin: 8px 0;
    box-shadow: 0 1px 2px rgba(15,23,42,.04);
}
.project-card.high { border-left: 4px solid #EF4444; }
.project-card.medium { border-left: 4px solid #F59E0B; }
.project-card.low { border-left: 4px solid #94A3B8; }
.project-name {
    font-size: .88rem;
    font-weight: 800;
    color: #111827;
    line-height: 1.3;
}
.project-client {
    font-size: .75rem;
    color: #64748B;
    margin-top: 3px;
}
.project-action {
    font-size: .78rem;
    color: #334155;
    margin-top: 8px;
    line-height: 1.35;
}
.project-follow {
    font-size: .72rem;
    color: #2563EB;
    margin-top: 7px;
    font-weight: 700;
}
.empty-stage {
    font-size: .75rem;
    color: #94A3B8;
    padding: 10px 2px 4px 2px;
}

.page-kicker { color: #64748B; font-size: .82rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }
.page-title { color: #0F172A; font-size: 2rem; line-height: 1.15; font-weight: 800; margin-top: .25rem; }
.page-subtitle { color: #64748B; font-size: .95rem; margin-top: .35rem; margin-bottom: 1.25rem; }
.kpi-card { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 16px; padding: 1.15rem 1.25rem; box-shadow: 0 1px 2px rgba(15,23,42,.04); min-height: 120px; }
.kpi-top { display:flex; justify-content:space-between; align-items:center; }
.kpi-icon { font-size:1.25rem; background:#F1F5F9; width:38px; height:38px; border-radius:10px; display:flex; align-items:center; justify-content:center; }
.kpi-label { color:#64748B; font-size:.78rem; font-weight:800; letter-spacing:.04em; text-transform:uppercase; }
.kpi-value { color:#0F172A; font-size:1.85rem; font-weight:800; margin-top:.45rem; }
.kpi-foot { color:#94A3B8; font-size:.78rem; margin-top:.25rem; }
.panel-title { color:#0F172A; font-size:1rem; font-weight:800; margin-bottom:.2rem; }
.panel-sub { color:#64748B; font-size:.82rem; margin-bottom:.8rem; }
.stTextInput input, .stTextArea textarea, .stNumberInput input, div[data-baseweb="select"] > div { border-radius: 10px !important; }
.stButton button { border-radius:10px; font-weight:700; }
[data-testid="stDataFrame"] { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:14px; overflow:hidden; }
div[data-testid="stMetric"] { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:14px; padding:1rem; }
hr { border-color:#E5E7EB !important; }
</style>
""", unsafe_allow_html=True)


def page_header(title, subtitle, kicker="LIGHTINGSALES CRM"):
    st.markdown(
        f'<div class="page-kicker">{kicker}</div><div class="page-title">{title}</div><div class="page-subtitle">{subtitle}</div>',
        unsafe_allow_html=True
    )


def kpi_card(icon, label, value, foot=""):
    st.markdown(
        f'<div class="kpi-card"><div class="kpi-top"><div class="kpi-label">{label}</div><div class="kpi-icon">{icon}</div></div><div class="kpi-value">{value}</div><div class="kpi-foot">{foot}</div></div>',
        unsafe_allow_html=True
    )


# ============================================================
# 5A. SIDEBAR / ĐIỀU HƯỚNG / CẬP NHẬT
# ============================================================
update_config = load_update_config()

with st.sidebar:
    st.markdown('<div class="sidebar-brand"><div class="brand-name">💡 LightingSales CRM</div><div class="brand-sub">Sales & Project Management</div></div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-section">MENU CHÍNH</div>', unsafe_allow_html=True)
    page = st.radio(
        "Điều hướng",
        ["⌂  Home", "◎  Deals", "✓  Activities", "🧾  Quotations", "♙  Customers", "▦  Products", "🛡  Agent Control", "⚙  Settings"],
        label_visibility="collapsed",
        key="main_navigation"
    )
    st.markdown('<div class="sidebar-section">HỆ THỐNG</div>', unsafe_allow_html=True)
    with st.expander("🔄 Cập nhật CRM", expanded=False):
        st.caption(f"Phiên bản hiện tại: v{APP_VERSION}")
        manifest_url_input = st.text_input(
            "URL version.json",
            value=update_config.get("manifest_url", ""),
            placeholder="https://raw.githubusercontent.com/.../version.json",
            key="manifest_url_settings"
        )
        if st.button("💾 Lưu cấu hình", use_container_width=True, key="save_update_source"):
            save_update_config(manifest_url_input)
            st.success("Đã lưu nguồn cập nhật.")
            st.rerun()
        if st.button("🔄 Kiểm tra cập nhật", use_container_width=True, key="check_update"):
            with st.spinner("Đang kiểm tra phiên bản mới..."):
                status, message = check_and_update(update_config.get("manifest_url", ""))
            if status == "updated":
                st.success(message)
                time.sleep(1)
                st.rerun()
            elif status == "latest":
                st.success(message)
            else:
                st.error(message)

    with st.expander("🛡️ Bảo vệ dữ liệu", expanded=False):
        try:
            health = database_health()
            if health["ok"]:
                st.success("Database: an toàn")
            else:
                st.error(f"Database có lỗi: {health['integrity']}")
            st.caption(
                f"{health['table_counts']['khach_hang_goc']} khách hàng • "
                f"{health['table_counts']['cong_trinh_new']} công trình • "
                f"{health['table_counts']['bao_gia']} báo giá"
            )
        except Exception as health_error:
            st.error(f"Không kiểm tra được database: {health_error}")

        if DAILY_BACKUP_ERROR:
            st.warning(f"Backup tự động chưa thành công: {DAILY_BACKUP_ERROR}")

        if st.button("📦 Tạo backup đầy đủ", use_container_width=True, key="create_full_backup_btn"):
            try:
                with st.spinner("Đang sao lưu database, ảnh và cấu hình..."):
                    created_backup = create_full_backup("manual", "Tạo thủ công từ CRM")
                st.session_state["latest_created_backup"] = created_backup
                st.success("Đã tạo bản sao lưu đầy đủ.")
            except Exception as backup_error:
                st.error(f"Không tạo được backup: {backup_error}")

        available_backups = list_data_backups()
        if available_backups:
            latest_backup = available_backups[0]
            with open(latest_backup["path"], "rb") as backup_file:
                backup_bytes = backup_file.read()
            st.download_button(
                "⬇️ Tải backup mới nhất",
                data=backup_bytes,
                file_name=latest_backup["name"],
                mime="application/zip",
                use_container_width=True,
                key="download_latest_backup"
            )
            st.caption(
                f"Mới nhất: {latest_backup['modified'].strftime('%d/%m/%Y %H:%M')} • "
                f"{latest_backup['size'] / 1024 / 1024:.1f} MB"
            )

        restore_upload = st.file_uploader(
            "Khôi phục từ backup ZIP",
            type=["zip"],
            key="restore_backup_upload"
        )
        restore_confirm = st.checkbox(
            "Tôi xác nhận khôi phục dữ liệu từ file đã chọn",
            key="restore_backup_confirm"
        )
        if st.button(
            "♻️ Khôi phục dữ liệu",
            disabled=restore_upload is None or not restore_confirm,
            use_container_width=True,
            key="restore_backup_btn"
        ):
            try:
                with st.spinner("Đang kiểm tra và khôi phục an toàn..."):
                    restored_manifest, pre_restore = restore_full_backup(restore_upload.getvalue())
                st.success(
                    f"Đã khôi phục backup ngày {restored_manifest.get('created_at', 'không xác định')}. "
                    "CRM cũng đã lưu một bản trước khi khôi phục."
                )
                st.rerun()
            except Exception as restore_error:
                st.error(f"Không thể khôi phục: {restore_error}")

        pending_actions = int(
            cursor.execute(
                "SELECT COUNT(*) FROM agent_action_queue WHERE status='Chờ duyệt'"
            ).fetchone()[0] or 0
        )
        st.caption(f"Agent Safety Engine: sẵn sàng • {pending_actions} hành động chờ duyệt")
    st.caption("SQLite local • Dữ liệu & ảnh không bị ghi đè")


# ============================================================
# 6. LOAD DATA
# ============================================================
df_kh, df_ct, df_sp, df_cty_saved = load_data()

# ============================================================
# V3.4.0 - DATA SAFETY + AGENT TOOL FOUNDATION
# ============================================================
crm_page = page
page_alias = {
    "⌂  Home": "📊  Tổng quan",
    "◎  Deals": "🏗️  Công trình",
    "🧾  Quotations": "🧾  Báo giá",
    "♙  Customers": "👥  Khách hàng",
    "▦  Products": "📦  Sản phẩm",
    "🛡  Agent Control": "🛡️  Agent Control",
    "⚙  Settings": "🏢  Thông tin công ty",
}
page = page_alias.get(page, page)

# ============================================================
# TAB 1 - DASHBOARD
# ============================================================

if page == "📊  Tổng quan":
    page_header(
        "Tổng quan kinh doanh",
        "Mở CRM là biết ngay dự án nào cần xử lý hôm nay, dự án nào quá hạn và việc gì sắp tới."
    )

    high_priority = 0
    active_projects = 0
    if not df_ct.empty:
        if "uu_tien" in df_ct.columns:
            high_priority = int(
                df_ct["uu_tien"].fillna("").astype(str).str.lower().eq("high").sum()
            )
        if "giai_doan" in df_ct.columns:
            active_projects = int(
                (~df_ct["giai_doan"].fillna("").isin(["Hoàn thành", ""])).sum()
            )

    # --------------------------------------------------------
    # Phân loại follow-up theo ngày
    # --------------------------------------------------------
    today = datetime.now().date()

    def parse_follow_date(value):
        """Đọc ngày follow-up an toàn. Hỗ trợ dd/mm/yyyy và yyyy-mm-dd."""
        if value is None or pd.isna(value):
            return None
        raw = str(value).strip()
        if not raw:
            return None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                pass
        return None

    # Dashboard V3.2.2: chỉ đọc Activity đang mở.
    # Không còn lấy danh sách công việc từ các cột legacy trong cong_trinh_new,
    # vì dữ liệu đó có thể còn cache/đồng bộ chậm sau khi một Activity hoàn thành.
    follow_rows = []
    active_tasks_df = pd.read_sql_query("""
        SELECT
            a.id AS activity_id,
            a.cong_trinh_id AS project_id,
            a.loai,
            a.noi_dung,
            a.ngay_hen,
            a.uu_tien AS activity_priority,
            a.ghi_chu,
            c.ten_du_an,
            c.giai_doan,
            c.uu_tien AS project_priority
        FROM cong_trinh_hoat_dong a
        JOIN cong_trinh_new c ON c.id = a.cong_trinh_id
        WHERE a.trang_thai='Đang làm'
          AND COALESCE(c.giai_doan, '') <> 'Hoàn thành'
        ORDER BY a.id DESC
    """, conn)

    for _, r in active_tasks_df.iterrows():
        action = str(r.get("noi_dung", "") or "").strip()
        follow_date = parse_follow_date(r.get("ngay_hen", ""))
        if not action or follow_date is None:
            continue
        follow_rows.append({
            "activity_id": int(r.get("activity_id")),
            "project_id": int(r.get("project_id")),
            "ten_du_an": str(r.get("ten_du_an", "") or ""),
            "giai_doan": str(r.get("giai_doan", "") or "").strip(),
            "uu_tien": str(r.get("activity_priority", "") or r.get("project_priority", "") or ""),
            "loai": str(r.get("loai", "") or ""),
            "viec_tiep_theo": action,
            "ngay_theo_doi": follow_date,
            "ghi_chu_cong_viec": str(r.get("ghi_chu", "") or "").strip(),
        })

    overdue_items = sorted(
        [x for x in follow_rows if x["ngay_theo_doi"] < today],
        key=lambda x: x["ngay_theo_doi"]
    )
    today_items = [
        x for x in follow_rows if x["ngay_theo_doi"] == today
    ]
    upcoming_items = sorted(
        [
            x for x in follow_rows
            if today < x["ngay_theo_doi"] <= today + timedelta(days=7)
        ],
        key=lambda x: x["ngay_theo_doi"]
    )

    # --------------------------------------------------------
    # KPI tổng quan
    # --------------------------------------------------------
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi_card("🏗️", "Công trình", f"{len(df_ct)}", f"{active_projects} dự án đang theo dõi")
    with c2:
        kpi_card("🔴", "Quá hạn", f"{len(overdue_items)}", "Việc follow-up đã trễ")
    with c3:
        kpi_card("📅", "Hôm nay", f"{len(today_items)}", "Việc cần xử lý hôm nay")
    with c4:
        kpi_card("⏳", "7 ngày tới", f"{len(upcoming_items)}", "Việc sắp phải theo dõi")

    st.markdown("")
    st.markdown('<div class="panel-title">✅ Trung tâm công việc</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="panel-sub">Chỉ hiển thị Activity đang làm; công việc đã hoàn thành sẽ tự động ẩn khỏi bảng.</div>',
        unsafe_allow_html=True
    )

    def render_task_group(title, icon, items, empty_text):
        st.markdown(f"#### {icon} {title}")
        if not items:
            st.caption(empty_text)
            return
        task_df = pd.DataFrame([
            {
                "Công trình": x["ten_du_an"],
                "Giai đoạn": x["giai_doan"],
                "Ưu tiên": x["uu_tien"],
                "Việc tiếp theo": x["viec_tiep_theo"],
                "Ngày": x["ngay_theo_doi"].strftime("%d/%m/%Y"),
            }
            for x in items[:8]
        ])
        st.dataframe(task_df, use_container_width=True, hide_index=True)

    task1, task2, task3 = st.columns(3, gap="large")
    with task1:
        render_task_group(
            "Quá hạn",
            "🔴",
            overdue_items,
            "Không có việc quá hạn."
        )
    with task2:
        render_task_group(
            "Hôm nay",
            "📅",
            today_items,
            "Không có việc cần xử lý hôm nay."
        )
    with task3:
        render_task_group(
            "Sắp tới",
            "⏳",
            upcoming_items,
            "Không có follow-up trong 7 ngày tới."
        )

    # --------------------------------------------------------
    # Xử lý trực tiếp công việc đến hạn
    # --------------------------------------------------------
    all_action_items = overdue_items + today_items + upcoming_items
    with st.expander("✏️ Xử lý công việc đến hạn", expanded=bool(overdue_items or today_items)):
        if not all_action_items:
            st.info("Hiện không có công việc quá hạn, hôm nay hoặc trong 7 ngày tới.")
        else:
            task_map = {
                int(x["activity_id"]): x for x in all_action_items if x.get("activity_id") is not None
            }
            selected_task_id = st.selectbox(
                "Chọn công việc cần xử lý",
                list(task_map.keys()),
                format_func=lambda x: f"#{x} - {task_map[x]['ten_du_an']} | {task_map[x]['viec_tiep_theo']} | {task_map[x]['ngay_theo_doi'].strftime('%d/%m/%Y')}",
                key="dashboard_task_select"
            )
            task = task_map[selected_task_id]
            selected_project_id = int(task["project_id"])

            c_task1, c_task2 = st.columns([1.5, 1])
            with c_task1:
                task_action_edit = st.text_area(
                    "Việc cần làm",
                    value=task["viec_tiep_theo"],
                    key=f"dashboard_task_action_{selected_task_id}"
                )
                task_note_edit = st.text_area(
                    "Ghi chú xử lý",
                    value=task.get("ghi_chu_cong_viec", ""),
                    placeholder="Ví dụ: Đã gọi khách, chờ xác nhận mẫu; cần gọi lại sau 2 ngày...",
                    key=f"dashboard_task_note_{selected_task_id}"
                )
            with c_task2:
                task_new_date = st.date_input(
                    "Ngày theo dõi / thời hạn mới",
                    value=task["ngay_theo_doi"],
                    key=f"dashboard_task_date_{selected_task_id}"
                )
                st.caption(f"Giai đoạn: {task['giai_doan']} · Ưu tiên: {task['uu_tien']}")

            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("💾 Lưu ghi chú", use_container_width=True, key=f"save_task_note_{selected_task_id}"):
                    cursor.execute("""
                        UPDATE cong_trinh_hoat_dong
                        SET noi_dung=?, ghi_chu=?
                        WHERE id=? AND trang_thai='Đang làm'
                    """, (task_action_edit.strip(), task_note_edit.strip(), int(selected_task_id)))
                    conn.commit()
                    sync_project_next_action(selected_project_id)
                    st.success("Đã lưu nội dung và ghi chú công việc.")
                    st.rerun()
            with b2:
                if st.button("📅 Dời thời hạn", use_container_width=True, key=f"reschedule_task_{selected_task_id}"):
                    new_date_text = task_new_date.strftime("%d/%m/%Y")
                    cursor.execute("""
                        UPDATE cong_trinh_hoat_dong
                        SET noi_dung=?, ngay_hen=?, ghi_chu=?
                        WHERE id=? AND trang_thai='Đang làm'
                    """, (
                        task_action_edit.strip(), new_date_text,
                        task_note_edit.strip(), int(selected_task_id)
                    ))
                    conn.commit()
                    sync_project_next_action(selected_project_id)
                    st.success(f"Đã dời thời hạn sang {new_date_text}.")
                    st.rerun()
            with b3:
                if st.button("✅ Đã thực hiện", type="primary", use_container_width=True, key=f"complete_task_{selected_task_id}"):
                    completed_at = datetime.now().strftime("%d/%m/%Y %H:%M")
                    cursor.execute("""
                        UPDATE cong_trinh_hoat_dong
                        SET noi_dung=?, ghi_chu=?, trang_thai='Đã hoàn thành',
                            ngay_hoan_thanh=?
                        WHERE id=? AND trang_thai='Đang làm'
                    """, (
                        task_action_edit.strip(), task_note_edit.strip(),
                        completed_at, int(selected_task_id)
                    ))
                    if cursor.rowcount > 0:
                        cursor.execute("""
                            INSERT INTO cong_viec_lich_su
                            (cong_trinh_id, ten_du_an, noi_dung, ngay_hen, ghi_chu, ngay_hoan_thanh)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            selected_project_id,
                            task["ten_du_an"],
                            task_action_edit.strip(),
                            task["ngay_theo_doi"].strftime("%d/%m/%Y"),
                            task_note_edit.strip(),
                            completed_at
                        ))
                    conn.commit()
                    sync_project_next_action(selected_project_id)
                    st.success("Đã chuyển công việc sang mục Đã thực hiện. Nếu còn Activity khác, CRM sẽ tự đưa việc gần nhất lên Dashboard.")
                    st.rerun()

    with st.expander("✅ Công việc đã thực hiện", expanded=False):
        done_df = pd.read_sql_query("""
            SELECT ten_du_an, noi_dung, ngay_hen, ghi_chu, ngay_hoan_thanh
            FROM cong_viec_lich_su
            ORDER BY id DESC
            LIMIT 50
        """, conn)
        if done_df.empty:
            st.caption("Chưa có công việc nào được đánh dấu hoàn thành.")
        else:
            done_df = done_df.rename(columns={
                "ten_du_an": "Công trình",
                "noi_dung": "Công việc",
                "ngay_hen": "Ngày hẹn",
                "ghi_chu": "Ghi chú",
                "ngay_hoan_thanh": "Hoàn thành lúc"
            })
            st.dataframe(done_df, use_container_width=True, hide_index=True)

    st.markdown("---")

    left, right = st.columns([1.6, 1], gap="large")
    with left:
        st.markdown(
            '<div class="panel-title">🏗️ Công trình đang theo dõi</div>',
            unsafe_allow_html=True
        )
        st.markdown(
            '<div class="panel-sub">Giai đoạn, việc tiếp theo và ngày follow-up gần nhất.</div>',
            unsafe_allow_html=True
        )

        if df_ct.empty:
            st.info("Chưa có công trình. Vào menu Công trình để thêm dự án đầu tiên.")
        else:
            active_df = df_ct[
                ~df_ct["giai_doan"].fillna("").eq("Hoàn thành")
            ].copy()

            if active_df.empty:
                st.success("Tất cả công trình hiện tại đã hoàn thành.")
            else:
                # Sắp xếp: High trước, sau đó theo ngày follow-up hợp lệ.
                active_df["_priority_order"] = (
                    active_df["uu_tien"]
                    .map({"High": 1, "Medium": 2, "Low": 3})
                    .fillna(4)
                )
                active_df["_follow_sort"] = active_df["ngay_theo_doi"].apply(
                    lambda x: parse_follow_date(x) or datetime.max.date()
                )
                active_df = active_df.sort_values(
                    ["_priority_order", "_follow_sort", "id"],
                    ascending=[True, True, False]
                )

                project_view = active_df[
                    ["ten_du_an", "uu_tien", "giai_doan", "viec_tiep_theo", "ngay_theo_doi"]
                ].head(10).copy()
                project_view.columns = [
                    "Công trình", "Ưu tiên", "Giai đoạn", "Việc tiếp theo", "Theo dõi"
                ]
                st.dataframe(project_view, use_container_width=True, hide_index=True)

    with right:
        st.markdown(
            '<div class="panel-title">👥 Khách hàng mới</div>',
            unsafe_allow_html=True
        )
        st.markdown(
            '<div class="panel-sub">Danh sách khách hàng được thêm gần đây.</div>',
            unsafe_allow_html=True
        )
        if df_kh.empty:
            st.info("Chưa có khách hàng. Vào menu Khách hàng để tạo hồ sơ đầu tiên.")
        else:
            recent_kh = df_kh[
                ["ten", "ten_cong_ty", "phan_loai_kh", "ngay_tao"]
            ].head(7).copy()
            recent_kh.columns = ["Khách hàng", "Công ty", "Phân loại", "Ngày tạo"]
            st.dataframe(recent_kh, use_container_width=True, hide_index=True)

    st.markdown("")
    st.info(
        "💡 Mẹo: mở menu Project Workspace để tạo và xử lý nhiều Activity cho từng công trình. "
        "và 'Ngày cần theo dõi'. Dashboard sẽ tự đưa công việc vào Quá hạn / Hôm nay / 7 ngày tới."
    )


# ============================================================
# TAB 2 - KHÁCH HÀNG
# ============================================================

if page == "👥  Khách hàng":
    page_header("Khách hàng", "Quản lý khách hàng, công ty, địa chỉ và phân loại đối tác.")
    st.markdown("## 👥 Quản lý khách hàng")

    with st.expander("➕ Thêm khách hàng mới", expanded=df_kh.empty):
        ten = st.text_input("Họ và tên *", key="kh_ten")
        thoai = st.text_input("Số điện thoại *", key="kh_thoai")
        ten_cong_ty = st.text_input("Tên công ty", placeholder="Ví dụ: Công ty TNHH ABC", key="kh_ten_cong_ty")
        dia_chi_cong_ty = st.text_input("Địa chỉ công ty", placeholder="Nhập địa chỉ công ty của khách hàng", key="kh_dia_chi_cong_ty")
        phan_loai_kh = st.selectbox("Phân loại khách hàng", ["Chủ đầu tư", "Nhà thầu", "Kiến trúc", "Nội thất", "Khách lẻ"], key="kh_loai")
        if st.button("➕ Thêm hồ sơ khách hàng", type="primary", key="add_customer_btn"):
            if not ten.strip() or not thoai.strip():
                st.warning("⚠️ Vui lòng nhập đầy đủ tên và số điện thoại.")
            else:
                try:
                    cursor.execute("""INSERT INTO khach_hang_goc (ten, thoai, ten_cong_ty, dia_chi_cong_ty, phan_loai_kh, ngay_tao) VALUES (?, ?, ?, ?, ?, ?)""", (ten.strip(), thoai.strip(), ten_cong_ty.strip(), dia_chi_cong_ty.strip(), phan_loai_kh, datetime.now().strftime("%Y-%m-%d")))
                    conn.commit(); st.success("🎉 Đã thêm khách hàng thành công!"); st.rerun()
                except sqlite3.IntegrityError:
                    st.error("❌ Số điện thoại này đã tồn tại!")

    st.markdown("### 📋 Danh sách khách hàng")
    df_kh_display = df_kh.rename(columns={"ten":"Họ và tên","thoai":"Số điện thoại","ten_cong_ty":"Công ty","dia_chi_cong_ty":"Địa chỉ công ty","phan_loai_kh":"Phân loại","ngay_tao":"Ngày tạo"})
    st.dataframe(df_kh_display, width="stretch", hide_index=True)

    if not df_kh.empty:
        with st.expander("✏️ Chỉnh sửa / cập nhật khách hàng"):
            kh_edit_id = st.selectbox("Chọn khách hàng cần chỉnh sửa", df_kh["id"].tolist(), format_func=lambda x: f"ID {x} - {df_kh.loc[df_kh['id']==x,'ten'].values[0]} ({df_kh.loc[df_kh['id']==x,'thoai'].values[0]})", key="kh_edit_id")
            kh_row = df_kh.loc[df_kh["id"] == kh_edit_id].iloc[0]
            kh_types = ["Chủ đầu tư", "Nhà thầu", "Kiến trúc", "Nội thất", "Khách lẻ"]
            current_type = str(kh_row.get("phan_loai_kh", "") or "")
            type_index = kh_types.index(current_type) if current_type in kh_types else 0
            e1,e2=st.columns(2)
            with e1:
                edit_ten=st.text_input("Họ và tên *", value=str(kh_row["ten"] or ""), key=f"edit_kh_ten_{kh_edit_id}")
                edit_thoai=st.text_input("Số điện thoại *", value=str(kh_row["thoai"] or ""), key=f"edit_kh_phone_{kh_edit_id}")
                edit_company=st.text_input("Tên công ty", value=str(kh_row.get("ten_cong_ty","") or ""), key=f"edit_kh_company_{kh_edit_id}")
            with e2:
                edit_address=st.text_input("Địa chỉ công ty", value=str(kh_row.get("dia_chi_cong_ty","") or ""), key=f"edit_kh_address_{kh_edit_id}")
                edit_type=st.selectbox("Phân loại khách hàng", kh_types, index=type_index, key=f"edit_kh_type_{kh_edit_id}")
                st.text_input("Ngày tạo", value=str(kh_row.get("ngay_tao","") or ""), disabled=True, key=f"edit_kh_created_{kh_edit_id}")
            if st.button("💾 Cập nhật thông tin khách hàng", type="primary", key="update_customer_btn"):
                if not edit_ten.strip() or not edit_thoai.strip():
                    st.warning("⚠️ Họ tên và số điện thoại không được để trống.")
                else:
                    try:
                        old_phone=str(kh_row["thoai"] or "").strip(); new_phone=edit_thoai.strip()
                        cursor.execute("""UPDATE khach_hang_goc SET ten=?, thoai=?, ten_cong_ty=?, dia_chi_cong_ty=?, phan_loai_kh=? WHERE id=?""", (edit_ten.strip(),new_phone,edit_company.strip(),edit_address.strip(),edit_type,int(kh_edit_id)))
                        if old_phone and new_phone != old_phone:
                            cursor.execute("UPDATE cong_trinh_new SET thoai_khach=? WHERE thoai_khach=?", (new_phone,old_phone))
                        conn.commit(); st.success("✅ Đã cập nhật thông tin khách hàng."); st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("❌ Số điện thoại này đang được dùng cho khách hàng khác.")
        with st.expander("🗑️ Xóa khách hàng"):
            kh_del_id=st.selectbox("Chọn khách hàng", df_kh["id"].tolist(), format_func=lambda x: f"ID {x} - {df_kh.loc[df_kh['id']==x,'ten'].values[0]} ({df_kh.loc[df_kh['id']==x,'thoai'].values[0]})", key="kh_delete_id")
            if st.button("❌ Xác nhận xóa", type="primary", key="delete_customer_btn"):
                cursor.execute("DELETE FROM khach_hang_goc WHERE id=?",(kh_del_id,)); conn.commit(); st.success("🎉 Đã xóa khách hàng!"); st.rerun()


# ============================================================
# TAB 3 - CÔNG TRÌNH
# ============================================================



# ============================================================
# V3.3.1 - ACTIVITIES CENTER
# ============================================================
if crm_page == "✓  Activities":
    page_header("Activities", "My Work — quản lý tập trung các cuộc gọi, meeting, khảo sát, follow-up và deadline.")

    act_df = pd.read_sql_query("""
        SELECT a.id, a.cong_trinh_id, a.loai, a.noi_dung, a.ngay_hen,
               a.uu_tien, a.ghi_chu, c.ten_du_an, c.giai_doan, c.gia_tri_du_kien
        FROM cong_trinh_hoat_dong a
        JOIN cong_trinh_new c ON c.id=a.cong_trinh_id
        WHERE a.trang_thai='Đang làm'
          AND COALESCE(c.giai_doan,'') <> 'Hoàn thành'
        ORDER BY a.id DESC
    """, conn)

    today_act = datetime.now().date()
    act_rows = []
    for _, ar in act_df.iterrows():
        due = parse_activity_date(ar["ngay_hen"])
        delta = (due - today_act).days if due else 9999
        if due and delta < 0: bucket = "Quá hạn"
        elif due and delta == 0: bucket = "Hôm nay"
        elif due and delta <= 7: bucket = "7 ngày tới"
        else: bucket = "Sắp tới"
        item = ar.to_dict()
        item.update({"_due": due, "_delta": delta, "_bucket": bucket})
        act_rows.append(item)

    a1, a2, a3, a4 = st.columns(4)
    with a1: kpi_card("🔴", "QUÁ HẠN", sum(x["_bucket"]=="Quá hạn" for x in act_rows), "Cần xử lý ngay")
    with a2: kpi_card("●", "HÔM NAY", sum(x["_bucket"]=="Hôm nay" for x in act_rows), "Đến hạn hôm nay")
    with a3: kpi_card("◷", "7 NGÀY TỚI", sum(x["_bucket"]=="7 ngày tới" for x in act_rows), "Lịch follow-up")
    with a4: kpi_card("✓", "ĐANG MỞ", len(act_rows), "Tổng Activity")

    activity_filter = st.selectbox(
        "Bộ lọc",
        ["Tất cả", "Quá hạn", "Hôm nay", "7 ngày tới", "Sắp tới"],
        key="activity_center_filter_v331"
    )
    filtered = act_rows if activity_filter == "Tất cả" else [x for x in act_rows if x["_bucket"] == activity_filter]
    filtered = sorted(filtered, key=lambda x: (x["_delta"], int(x["id"])))

    if not filtered:
        st.success("Không có Activity trong nhóm này.")
    else:
        act_view = pd.DataFrame([{
            "ID": int(x["id"]), "Hạn": x["ngay_hen"], "Loại": x["loai"],
            "Công việc": x["noi_dung"], "Deal / Công trình": x["ten_du_an"],
            "Giai đoạn": x["giai_doan"], "Ưu tiên": x["uu_tien"],
            "Giá trị": money_vnd(x["gia_tri_du_kien"] or 0)
        } for x in filtered])
        st.dataframe(act_view, width="stretch", hide_index=True)

        with st.expander("✓ Xử lý Activity", expanded=False):
            ids = [int(x["id"]) for x in filtered]
            aid = st.selectbox(
                "Chọn Activity", ids,
                format_func=lambda x: next(f"#{x} · {a['ten_du_an']} · {a['noi_dung']}" for a in filtered if int(a["id"])==x),
                key="activity_center_select_v331"
            )
            item = next(a for a in filtered if int(a["id"]) == aid)
            c1, c2 = st.columns([1.5,1])
            with c1:
                atext = st.text_area("Nội dung", value=str(item["noi_dung"] or ""), key=f"acenter_text_{aid}")
                anote = st.text_area("Ghi chú", value=str(item["ghi_chu"] or ""), key=f"acenter_note_{aid}")
            with c2:
                adate = st.date_input("Deadline", value=item["_due"] or today_act, key=f"acenter_date_{aid}")
                apriority = st.selectbox(
                    "Ưu tiên", ["High","Medium","Low"],
                    index=["High","Medium","Low"].index(item["uu_tien"]) if item["uu_tien"] in ["High","Medium","Low"] else 1,
                    key=f"acenter_priority_{aid}"
                )
            b1,b2 = st.columns(2)
            with b1:
                if st.button("💾 Lưu Activity", use_container_width=True, key=f"acenter_save_{aid}"):
                    cursor.execute("""
                        UPDATE cong_trinh_hoat_dong SET noi_dung=?, ngay_hen=?, uu_tien=?, ghi_chu=?
                        WHERE id=? AND trang_thai='Đang làm'
                    """,(atext.strip(),adate.strftime("%d/%m/%Y"),apriority,anote.strip(),aid))
                    conn.commit()
                    sync_project_next_action(int(item["cong_trinh_id"]))
                    st.rerun()
            with b2:
                if st.button("✓ Hoàn thành", type="primary", use_container_width=True, key=f"acenter_done_{aid}"):
                    completed_at = datetime.now().strftime("%d/%m/%Y %H:%M")
                    cursor.execute("""
                        UPDATE cong_trinh_hoat_dong
                        SET noi_dung=?, ghi_chu=?, trang_thai='Đã hoàn thành', ngay_hoan_thanh=?
                        WHERE id=? AND trang_thai='Đang làm'
                    """,(atext.strip(),anote.strip(),completed_at,aid))
                    if cursor.rowcount > 0:
                        cursor.execute("""
                            INSERT INTO cong_viec_lich_su
                            (cong_trinh_id,ten_du_an,noi_dung,ngay_hen,ghi_chu,ngay_hoan_thanh)
                            VALUES (?,?,?,?,?,?)
                        """,(int(item["cong_trinh_id"]),item["ten_du_an"],atext.strip(),
                             adate.strftime("%d/%m/%Y"),anote.strip(),completed_at))
                    conn.commit()
                    sync_project_next_action(int(item["cong_trinh_id"]))
                    st.rerun()

# ============================================================
# PROJECT WORKSPACE - TRANG RIÊNG
# ============================================================
if page == "🧭  Project Workspace" or (page == "🏗️  Công trình" and locals().get("deal_view_mode") == "Deal Workspace"):
    page_header("Project Workspace", "Trung tâm điều hành từng công trình: thông tin, Activity, deadline và lịch sử làm việc.")

    # ========================================================
    # PROJECT WORKSPACE - trung tâm làm việc của từng công trình
    # ========================================================
    st.markdown("### 🧭 Project Workspace")
    st.caption("Chọn một công trình để xem thông tin, quản lý Activity và lịch sử làm việc tại một nơi.")

    if not df_ct_joined.empty:
        workspace_project_id = st.selectbox(
            "Công trình đang làm việc",
            df_ct_joined["id"].tolist(),
            format_func=lambda x: f"#{x} - {df_ct_joined.loc[df_ct_joined['id']==x, 'ten_du_an'].iloc[0]}",
            key="workspace_project_id"
        )
        wp = df_ct_joined[df_ct_joined["id"] == workspace_project_id].iloc[0]

        wt1, wt2, wt3 = st.tabs(["📌 Tổng quan", "✅ Công việc / Activity", "🕘 Lịch sử"])

        with wt1:
            w1, w2, w3, w4 = st.columns(4)
            with w1:
                kpi_card("🎯", "GIAI ĐOẠN", str(wp["giai_doan"] or "—"), "Pipeline hiện tại")
            with w2:
                kpi_card("⚡", "ƯU TIÊN", str(wp["uu_tien"] or "—"), "Mức độ cần chú ý")
            with w3:
                expected_value = float(wp["gia_tri_du_kien"] or 0)
                kpi_card("💰", "GIÁ TRỊ DỰ KIẾN", f"{expected_value:,.0f} đ", "Giá trị cơ hội")
            with w4:
                open_count = cursor.execute(
                    "SELECT COUNT(*) FROM cong_trinh_hoat_dong WHERE cong_trinh_id=? AND trang_thai='Đang làm'",
                    (int(workspace_project_id),)
                ).fetchone()[0]
                kpi_card("📋", "VIỆC ĐANG MỞ", int(open_count), "Activity chưa hoàn thành")

            info1, info2 = st.columns(2)
            with info1:
                st.markdown("#### 👤 Khách hàng / Chủ đầu tư")
                st.write(f"**Tên:** {wp['Chu_Dau_Tu'] if pd.notna(wp['Chu_Dau_Tu']) else '—'}")
                st.write(f"**Công ty:** {wp['Cong_Ty'] if pd.notna(wp['Cong_Ty']) else '—'}")
                st.write(f"**Điện thoại:** {wp['SDT_Khach'] if pd.notna(wp['SDT_Khach']) else '—'}")
            with info2:
                st.markdown("#### 🏗️ Thông tin công trình")
                st.write(f"**Địa chỉ:** {wp['Dia_Chi'] if pd.notna(wp['Dia_Chi']) else '—'}")
                st.write(f"**Khởi tạo:** {wp['ngay_khoi_tao'] if pd.notna(wp['ngay_khoi_tao']) else '—'}")
                st.write(f"**Việc tiếp theo:** {wp['viec_tiep_theo'] if pd.notna(wp['viec_tiep_theo']) and str(wp['viec_tiep_theo']).strip() else 'Chưa có'}")

            with st.expander("✏️ Cập nhật nhanh công trình"):
                wc1, wc2, wc3 = st.columns(3)
                workspace_stages = ["Tiếp cận", "Khảo sát", "Báo giá", "Thương lượng", "Chốt đơn", "Triển khai", "Hoàn thành", "Tạm dừng"]
                workspace_priorities = ["High", "Medium", "Low"]
                with wc1:
                    wp_stage = st.selectbox(
                        "Giai đoạn",
                        workspace_stages,
                        index=workspace_stages.index(wp["giai_doan"]) if wp["giai_doan"] in workspace_stages else 0,
                        key=f"wp_stage_{workspace_project_id}"
                    )
                with wc2:
                    wp_priority = st.selectbox(
                        "Ưu tiên",
                        workspace_priorities,
                        index=workspace_priorities.index(wp["uu_tien"]) if wp["uu_tien"] in workspace_priorities else 1,
                        key=f"wp_priority_{workspace_project_id}"
                    )
                with wc3:
                    wp_value = st.number_input(
                        "Giá trị dự kiến (VNĐ)",
                        min_value=0.0,
                        value=float(wp["gia_tri_du_kien"] or 0),
                        step=1000000.0,
                        format="%.0f",
                        key=f"wp_value_{workspace_project_id}"
                    )
                wp_address = st.text_input(
                    "Địa chỉ công trình",
                    value="" if pd.isna(wp["Dia_Chi"]) else str(wp["Dia_Chi"]),
                    key=f"wp_address_{workspace_project_id}"
                )
                wp_note = st.text_area(
                    "Ghi chú công trình",
                    value="" if pd.isna(wp["note"]) else str(wp["note"]),
                    key=f"wp_note_{workspace_project_id}"
                )
                if st.button("💾 Lưu Project Workspace", type="primary", key=f"save_wp_{workspace_project_id}"):
                    cursor.execute("""
                        UPDATE cong_trinh_new
                        SET giai_doan=?, uu_tien=?, gia_tri_du_kien=?, dia_chi_cong_trinh=?, note=?
                        WHERE id=?
                    """, (
                        wp_stage, wp_priority, float(wp_value), wp_address.strip(),
                        wp_note.strip(), int(workspace_project_id)
                    ))
                    conn.commit()
                    st.success("Đã cập nhật thông tin công trình.")
                    st.rerun()

        with wt2:
            activity_df = pd.read_sql_query("""
                SELECT id, loai, noi_dung, ngay_hen, trang_thai, uu_tien,
                       ghi_chu, ngay_tao, ngay_hoan_thanh
                FROM cong_trinh_hoat_dong
                WHERE cong_trinh_id=?
                ORDER BY
                    CASE trang_thai WHEN 'Đang làm' THEN 1 ELSE 2 END,
                    id DESC
            """, conn, params=(int(workspace_project_id),))

            st.markdown("#### 📋 Công việc đang mở")
            open_df = activity_df[activity_df["trang_thai"] == "Đang làm"].copy()
            if open_df.empty:
                st.info("Công trình này hiện chưa có Activity đang mở.")
            else:
                open_view = open_df[["id", "loai", "noi_dung", "ngay_hen", "uu_tien", "ghi_chu"]].copy()
                open_view.columns = ["ID", "Loại", "Công việc", "Hạn", "Ưu tiên", "Ghi chú"]
                st.dataframe(open_view, width="stretch", hide_index=True)

            with st.expander("➕ Tạo Activity mới", expanded=open_df.empty):
                ac1, ac2, ac3 = st.columns([1.2, 2, 1])
                with ac1:
                    new_activity_type = st.selectbox(
                        "Loại công việc",
                        ["Call", "Meeting", "Khảo sát", "Gửi báo giá", "Follow-up", "Gửi mẫu", "Deadline", "Khác"],
                        key=f"new_activity_type_{workspace_project_id}"
                    )
                with ac2:
                    new_activity_text = st.text_input(
                        "Nội dung công việc *",
                        placeholder="Ví dụ: Gọi khách xác nhận báo giá Rev.1",
                        key=f"new_activity_text_{workspace_project_id}"
                    )
                with ac3:
                    new_activity_priority = st.selectbox(
                        "Ưu tiên",
                        ["High", "Medium", "Low"],
                        index=1,
                        key=f"new_activity_priority_{workspace_project_id}"
                    )
                new_activity_date = st.date_input(
                    "Ngày đến hạn",
                    value=datetime.now().date(),
                    key=f"new_activity_date_{workspace_project_id}"
                )
                new_activity_note = st.text_area(
                    "Ghi chú",
                    placeholder="Nội dung trao đổi, người cần liên hệ, thông tin cần chuẩn bị...",
                    key=f"new_activity_note_{workspace_project_id}"
                )
                if st.button("➕ Thêm Activity", type="primary", key=f"add_activity_{workspace_project_id}"):
                    if not new_activity_text.strip():
                        st.warning("Vui lòng nhập nội dung công việc.")
                    else:
                        cursor.execute("""
                            INSERT INTO cong_trinh_hoat_dong
                            (cong_trinh_id, loai, noi_dung, ngay_hen, trang_thai,
                             uu_tien, ghi_chu, ngay_tao)
                            VALUES (?, ?, ?, ?, 'Đang làm', ?, ?, ?)
                        """, (
                            int(workspace_project_id),
                            new_activity_type,
                            new_activity_text.strip(),
                            new_activity_date.strftime("%d/%m/%Y"),
                            new_activity_priority,
                            new_activity_note.strip(),
                            datetime.now().strftime("%d/%m/%Y %H:%M")
                        ))
                        conn.commit()
                        sync_project_next_action(workspace_project_id)
                        st.success("Đã thêm Activity mới.")
                        st.rerun()

            if not open_df.empty:
                with st.expander("✏️ Xử lý / cập nhật Activity", expanded=False):
                    open_ids = open_df["id"].astype(int).tolist()
                    act_id = st.selectbox(
                        "Chọn Activity",
                        open_ids,
                        format_func=lambda x: f"#{x} - {open_df.loc[open_df['id']==x, 'noi_dung'].iloc[0]}",
                        key=f"manage_activity_{workspace_project_id}"
                    )
                    act = open_df[open_df["id"] == act_id].iloc[0]
                    ma1, ma2 = st.columns([1.6, 1])
                    with ma1:
                        act_text = st.text_area(
                            "Nội dung",
                            value=str(act["noi_dung"] or ""),
                            key=f"manage_act_text_{act_id}"
                        )
                        act_note = st.text_area(
                            "Ghi chú xử lý",
                            value=str(act["ghi_chu"] or ""),
                            key=f"manage_act_note_{act_id}"
                        )
                    with ma2:
                        current_act_date = parse_activity_date(act["ngay_hen"]) or datetime.now().date()
                        act_date = st.date_input(
                            "Ngày đến hạn",
                            value=current_act_date,
                            key=f"manage_act_date_{act_id}"
                        )
                        act_priority = st.selectbox(
                            "Ưu tiên",
                            ["High", "Medium", "Low"],
                            index=["High", "Medium", "Low"].index(act["uu_tien"]) if act["uu_tien"] in ["High", "Medium", "Low"] else 1,
                            key=f"manage_act_priority_{act_id}"
                        )

                    ab1, ab2, ab3 = st.columns(3)
                    with ab1:
                        if st.button("💾 Lưu thay đổi", use_container_width=True, key=f"save_activity_{act_id}"):
                            cursor.execute("""
                                UPDATE cong_trinh_hoat_dong
                                SET noi_dung=?, ngay_hen=?, uu_tien=?, ghi_chu=?
                                WHERE id=?
                            """, (
                                act_text.strip(), act_date.strftime("%d/%m/%Y"),
                                act_priority, act_note.strip(), int(act_id)
                            ))
                            conn.commit()
                            sync_project_next_action(workspace_project_id)
                            st.success("Đã lưu Activity.")
                            st.rerun()
                    with ab2:
                        if st.button("📅 Dời +3 ngày", use_container_width=True, key=f"plus3_activity_{act_id}"):
                            new_date = act_date + timedelta(days=3)
                            cursor.execute("""
                                UPDATE cong_trinh_hoat_dong
                                SET ngay_hen=?, ghi_chu=?
                                WHERE id=?
                            """, (new_date.strftime("%d/%m/%Y"), act_note.strip(), int(act_id)))
                            conn.commit()
                            sync_project_next_action(workspace_project_id)
                            st.success(f"Đã dời sang {new_date.strftime('%d/%m/%Y')}.")
                            st.rerun()
                    with ab3:
                        if st.button("✅ Hoàn thành", type="primary", use_container_width=True, key=f"done_activity_{act_id}"):
                            cursor.execute("""
                                UPDATE cong_trinh_hoat_dong
                                SET noi_dung=?, ghi_chu=?, trang_thai='Đã hoàn thành',
                                    ngay_hoan_thanh=?
                                WHERE id=?
                            """, (
                                act_text.strip(), act_note.strip(),
                                datetime.now().strftime("%d/%m/%Y %H:%M"), int(act_id)
                            ))
                            cursor.execute("""
                                INSERT INTO cong_viec_lich_su
                                (cong_trinh_id, ten_du_an, noi_dung, ngay_hen, ghi_chu, ngay_hoan_thanh)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                int(workspace_project_id),
                                str(wp["ten_du_an"]),
                                act_text.strip(),
                                act_date.strftime("%d/%m/%Y"),
                                act_note.strip(),
                                datetime.now().strftime("%d/%m/%Y %H:%M")
                            ))
                            conn.commit()
                            sync_project_next_action(workspace_project_id)
                            st.success("Đã hoàn thành Activity và chuyển sang lịch sử.")
                            st.rerun()

        with wt3:
            history_df = pd.read_sql_query("""
                SELECT loai, noi_dung, ngay_hen, uu_tien, ghi_chu,
                       ngay_tao, ngay_hoan_thanh
                FROM cong_trinh_hoat_dong
                WHERE cong_trinh_id=? AND trang_thai='Đã hoàn thành'
                ORDER BY id DESC
            """, conn, params=(int(workspace_project_id),))
            if history_df.empty:
                st.info("Chưa có Activity đã hoàn thành.")
            else:
                history_view = history_df.copy()
                history_view.columns = [
                    "Loại", "Công việc", "Ngày hẹn", "Ưu tiên",
                    "Ghi chú", "Ngày tạo", "Hoàn thành"
                ]
                st.dataframe(history_view, width="stretch", hide_index=True)
    else:
        st.info("Chưa có công trình để mở Project Workspace.")

    st.markdown("---")


if page == "🏗️  Công trình":
    deal_view_mode = st.radio(
        "Chế độ Deals",
        ["Pipeline & Danh sách", "Deal Workspace"],
        horizontal=True,
        key="deal_view_mode_v331"
    )
    page_header("Công trình", "Trung tâm theo dõi dự án: đang ở giai đoạn nào và việc cần làm tiếp theo.")

    # --- Project control KPIs ---
    total_ct = len(df_ct)
    active_ct = int((~df_ct["giai_doan"].fillna("").eq("Hoàn thành")).sum()) if total_ct else 0
    high_ct = int(df_ct["uu_tien"].fillna("").str.lower().eq("high").sum()) if total_ct else 0
    follow_ct = int(df_ct["viec_tiep_theo"].fillna("").str.strip().ne("").sum()) if total_ct else 0

    c1, c2, c3, c4 = st.columns(4)
    with c1: kpi_card("🏗️", "TỔNG DỰ ÁN", total_ct, "Toàn bộ công trình")
    with c2: kpi_card("▶️", "ĐANG THEO DÕI", active_ct, "Chưa hoàn thành")
    with c3: kpi_card("⚡", "ƯU TIÊN CAO", high_ct, "Cần chú ý")
    with c4: kpi_card("📌", "CÓ VIỆC TIẾP THEO", follow_ct, "Đã có action")

    st.markdown("### 🎯 Bảng điều hành dự án")
    st.caption("Nhìn nhanh dự án đang ở đâu trong pipeline, việc tiếp theo là gì và khi nào cần follow-up.")

    df_ct_joined = pd.read_sql_query("""
        SELECT
            c.id,
            c.ten_du_an,
            c.thoai_khach AS SDT_Khach,
            k.ten AS Chu_Dau_Tu,
            k.ten_cong_ty AS Cong_Ty,
            k.dia_chi_cong_ty AS Dia_Chi_Cong_Ty,
            c.dia_chi_cong_trinh AS Dia_Chi,
            c.uu_tien,
            c.giai_doan,
            c.viec_tiep_theo,
            c.ngay_theo_doi,
            c.ngay_khoi_tao,
            c.gia_tri_du_kien,
            c.note
        FROM cong_trinh_new c
        LEFT JOIN khach_hang_goc k ON c.thoai_khach = k.thoai
        ORDER BY
            CASE c.uu_tien WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
            c.id DESC
    """, conn)

    st.info("🧭 Project Workspace đã được tách thành menu riêng trên sidebar để làm việc theo từng công trình nhanh hơn.")

    mode_col, search_col, priority_col = st.columns([1, 1.7, 1])
    with mode_col:
        view_mode = st.radio(
            "Chế độ xem",
            ["🧩 Pipeline", "📋 Danh sách"],
            horizontal=True,
            key="project_view_mode"
        )
    with search_col:
        search_ct = st.text_input(
            "🔎 Tìm công trình",
            placeholder="Tên dự án, chủ đầu tư, địa chỉ...",
            key="project_search"
        )
    with priority_col:
        priority_filter = st.selectbox(
            "Ưu tiên",
            ["Tất cả", "High", "Medium", "Low"],
            key="project_priority_filter"
        )

    base_view = df_ct_joined.copy()
    if search_ct.strip():
        q = search_ct.strip().lower()
        mask = base_view.astype(str).apply(
            lambda col: col.str.lower().str.contains(q, na=False)
        ).any(axis=1)
        base_view = base_view[mask]
    if priority_filter != "Tất cả":
        base_view = base_view[base_view["uu_tien"] == priority_filter]

    stages = [
        "Tiếp cận", "Khảo sát", "Báo giá", "Thương lượng",
        "Chốt đơn", "Triển khai", "Hoàn thành", "Tạm dừng"
    ]

    if view_mode == "🧩 Pipeline":
        # 8 giai đoạn chia thành 2 hàng x 4 cột để vẫn đọc được trên màn hình desktop.
        for row_start in (0, 4):
            stage_cols = st.columns(4)
            for idx, stage in enumerate(stages[row_start:row_start + 4]):
                stage_df = base_view[
                    base_view["giai_doan"].fillna("").eq(stage)
                ]
                with stage_cols[idx]:
                    st.markdown(
                        f'<div class="pipeline-stage-title">{html.escape(stage)}</div>'
                        f'<div class="pipeline-stage-count">{len(stage_df)} dự án</div>',
                        unsafe_allow_html=True
                    )

                    if stage_df.empty:
                        st.markdown(
                            '<div class="pipeline-stage"><div class="empty-stage">Chưa có dự án</div></div>',
                            unsafe_allow_html=True
                        )
                    else:
                        cards = ['<div class="pipeline-stage">']
                        for _, r in stage_df.iterrows():
                            priority = str(r["uu_tien"] or "Medium")
                            priority_class = priority.lower() if priority.lower() in ("high", "medium", "low") else "medium"
                            name = html.escape(str(r["ten_du_an"] or ""))
                            client = html.escape(str(r["Chu_Dau_Tu"] or "Chưa gắn khách hàng"))
                            action = html.escape(str(r["viec_tiep_theo"] or "").strip())
                            follow = html.escape(str(r["ngay_theo_doi"] or "").strip())

                            action_html = (
                                f'<div class="project-action">📌 {action}</div>'
                                if action else
                                '<div class="project-action" style="color:#94A3B8;">📌 Chưa có việc tiếp theo</div>'
                            )
                            follow_html = (
                                f'<div class="project-follow">🗓 {follow}</div>'
                                if follow else
                                '<div class="project-follow" style="color:#94A3B8;">🗓 Chưa đặt ngày follow-up</div>'
                            )
                            cards.append(
                                f'<div class="project-card {priority_class}">'
                                f'<div class="project-name">{name}</div>'
                                f'<div class="project-client">👤 {client} · {html.escape(priority)}</div>'
                                f'{action_html}{follow_html}'
                                f'</div>'
                            )
                        cards.append('</div>')
                        st.markdown("".join(cards), unsafe_allow_html=True)

    else:
        list_filter_col, _ = st.columns([1, 2])
        with list_filter_col:
            stage_filter = st.selectbox(
                "Lọc theo giai đoạn",
                ["Tất cả"] + stages,
                key="project_stage_filter"
            )
        view = base_view.copy()
        if stage_filter != "Tất cả":
            view = view[view["giai_doan"] == stage_filter]

        display_cols = [
            "id", "ten_du_an", "Chu_Dau_Tu", "uu_tien",
            "giai_doan", "gia_tri_du_kien", "viec_tiep_theo", "ngay_theo_doi"
        ]
        display = view[display_cols].copy()
        display["Deal Health"] = view.apply(
            lambda r: deal_health_label(r["giai_doan"], r["viec_tiep_theo"], r["ngay_theo_doi"]), axis=1
        )
        display["gia_tri_du_kien"] = display["gia_tri_du_kien"].apply(money_vnd)
        display.columns = [
            "ID", "Deal / Công trình", "Khách hàng / CĐT", "Ưu tiên",
            "Giai đoạn", "Giá trị", "Việc tiếp theo", "Ngày theo dõi", "Deal Health"
        ]
        st.dataframe(display, width="stretch", hide_index=True)

    st.markdown("---")
    action_col, create_col = st.columns(2)

    with action_col:
        with st.expander("✏️ Cập nhật tiến độ / việc tiếp theo", expanded=not df_ct.empty):
            if df_ct_joined.empty:
                st.info("Chưa có công trình để cập nhật.")
            else:
                project_ids = df_ct_joined["id"].tolist()
                edit_id = st.selectbox(
                    "Chọn công trình",
                    project_ids,
                    format_func=lambda x: f"#{x} - {df_ct_joined.loc[df_ct_joined['id']==x, 'ten_du_an'].iloc[0]}",
                    key="edit_project_id"
                )
                row = df_ct_joined[df_ct_joined["id"] == edit_id].iloc[0]
                stages = ["Tiếp cận", "Khảo sát", "Báo giá", "Thương lượng", "Chốt đơn", "Triển khai", "Hoàn thành", "Tạm dừng"]
                current_stage = row["giai_doan"] if row["giai_doan"] in stages else "Báo giá"
                new_stage = st.selectbox("Giai đoạn hiện tại", stages, index=stages.index(current_stage), key="edit_project_stage")
                priorities = ["High", "Medium", "Low"]
                current_pr = row["uu_tien"] if row["uu_tien"] in priorities else "Medium"
                new_priority = st.selectbox("Mức ưu tiên", priorities, index=priorities.index(current_pr), key="edit_project_priority")
                project_expected_value = st.number_input(
                    "Giá trị dự kiến (VNĐ)",
                    min_value=0.0,
                    value=float(row.get("gia_tri_du_kien", 0) or 0),
                    step=1000000.0,
                    format="%.0f",
                    key=f"project_expected_value_{edit_id}"
                )
                next_action = st.text_area(
                    "Việc cần làm tiếp theo",
                    value="" if pd.isna(row["viec_tiep_theo"]) else str(row["viec_tiep_theo"]),
                    placeholder="Ví dụ: Gửi lại báo giá revision 02; gọi khách xác nhận mẫu đèn...",
                    key=f"next_action_{edit_id}"
                )
                follow_date_text = st.text_input(
                    "Ngày cần theo dõi",
                    value="" if pd.isna(row["ngay_theo_doi"]) else str(row["ngay_theo_doi"]),
                    placeholder="dd/mm/yyyy",
                    key=f"follow_date_{edit_id}"
                )
                task_note_project = st.text_area(
                    "Ghi chú công việc",
                    value="" if pd.isna(row.get("ghi_chu_cong_viec", "")) else str(row.get("ghi_chu_cong_viec", "")),
                    key=f"project_task_note_{edit_id}"
                )
                if st.button("💾 Lưu cập nhật dự án", type="primary", key="save_project_progress"):
                    cursor.execute("""
                        UPDATE cong_trinh_new
                        SET giai_doan=?, uu_tien=?, gia_tri_du_kien=?, viec_tiep_theo=?, ngay_theo_doi=?, ghi_chu_cong_viec=?
                        WHERE id=?
                    """, (new_stage, new_priority, float(project_expected_value), next_action.strip(), follow_date_text.strip(), task_note_project.strip(), int(edit_id)))
                    conn.commit()
                    if next_action.strip():
                        existing_open = cursor.execute(
                            "SELECT COUNT(*) FROM cong_trinh_hoat_dong WHERE cong_trinh_id=? AND trang_thai='Đang làm'",
                            (int(edit_id),)
                        ).fetchone()[0]
                        if existing_open == 0:
                            cursor.execute("""
                                INSERT INTO cong_trinh_hoat_dong
                                (cong_trinh_id, loai, noi_dung, ngay_hen, trang_thai, uu_tien, ghi_chu, ngay_tao)
                                VALUES (?, 'Follow-up', ?, ?, 'Đang làm', ?, ?, ?)
                            """, (
                                int(edit_id), next_action.strip(), follow_date_text.strip(),
                                new_priority, task_note_project.strip(),
                                datetime.now().strftime("%d/%m/%Y %H:%M")
                            ))
                            conn.commit()
                    sync_project_next_action(edit_id)
                    st.success("Đã cập nhật tiến độ dự án.")
                    st.rerun()

    with create_col:
        with st.expander("➕ Tạo công trình mới"):
            ten_du_an = st.text_input("Tên dự án / công trình *", placeholder="Ví dụ: Penthouse Landmark 81", key="new_project_name")
            dia_chi_cong_trinh = st.text_input("Địa chỉ công trình", key="new_project_address")
            if not df_kh.empty:
                options_kh = {f"{r['ten']} - {r['thoai']}": r["thoai"] for _, r in df_kh.iterrows()}
                kh_label = st.selectbox("Khách hàng / chủ đầu tư *", list(options_kh.keys()), key="new_project_customer")
                thoai_khach_save = options_kh[kh_label]
            else:
                st.warning("Chưa có khách hàng. Hãy tạo khách hàng trước.")
                thoai_khach_save = ""
            uu_tien = st.selectbox("Mức ưu tiên", ["High", "Medium", "Low"], index=1, key="new_project_priority")
            gia_tri_du_kien_new = st.number_input(
                "Giá trị dự kiến (VNĐ)",
                min_value=0.0,
                value=0.0,
                step=1000000.0,
                format="%.0f",
                key="new_project_expected_value"
            )
            giai_doan = st.selectbox("Giai đoạn dự án", ["Tiếp cận","Khảo sát","Báo giá","Thương lượng","Chốt đơn","Triển khai","Hoàn thành","Tạm dừng"], key="new_project_stage")
            ngay_khoi_tao = st.date_input("Ngày khởi tạo", value=datetime.now().date(), key="new_project_date")
            next_action_new = st.text_area("Việc cần làm tiếp theo", key="new_project_next_action")
            follow_new = st.text_input("Ngày cần theo dõi", placeholder="dd/mm/yyyy", key="new_project_follow_date")
            note = st.text_area("Ghi chú", key="new_project_note")
            if st.button("💾 Lưu công trình", type="primary", key="save_new_project"):
                if not ten_du_an.strip():
                    st.warning("Vui lòng nhập tên dự án.")
                elif not thoai_khach_save:
                    st.warning("Vui lòng chọn khách hàng.")
                else:
                    cursor.execute("""
                        INSERT INTO cong_trinh_new
                        (ten_du_an, thoai_khach, dia_chi_cong_trinh, uu_tien, giai_doan,
                         ngay_khoi_tao, gia_tri_du_kien, viec_tiep_theo, ngay_theo_doi, note)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ten_du_an.strip(), thoai_khach_save, dia_chi_cong_trinh.strip(),
                        uu_tien, giai_doan, ngay_khoi_tao.strftime("%d/%m/%Y"),
                        float(gia_tri_du_kien_new), next_action_new.strip(), follow_new.strip(), note.strip()
                    ))
                    conn.commit()
                    new_project_id = cursor.lastrowid
                    if next_action_new.strip():
                        cursor.execute("""
                            INSERT INTO cong_trinh_hoat_dong
                            (cong_trinh_id, loai, noi_dung, ngay_hen, trang_thai, uu_tien, ghi_chu, ngay_tao)
                            VALUES (?, 'Follow-up', ?, ?, 'Đang làm', ?, '', ?)
                        """, (
                            int(new_project_id), next_action_new.strip(), follow_new.strip(),
                            uu_tien, datetime.now().strftime("%d/%m/%Y %H:%M")
                        ))
                        conn.commit()
                        sync_project_next_action(new_project_id)
                    st.success("Đã tạo công trình!")
                    st.rerun()

    if not df_ct.empty:
        with st.expander("🗑️ Xóa công trình"):
            ct_del_id = st.selectbox(
                "Chọn công trình cần xóa",
                df_ct["id"].tolist(),
                format_func=lambda x: f"ID {x} - {df_ct.loc[df_ct['id']==x, 'ten_du_an'].iloc[0]}",
                key="delete_project_id"
            )
            if st.button("🗑️ Xóa công trình", key="delete_project_btn"):
                cursor.execute("DELETE FROM cong_trinh_new WHERE id = ?", (int(ct_del_id),))
                conn.commit()
                st.success("Đã xóa công trình!")
                st.rerun()


# TAB 4 - BÁO GIÁ
# ============================================================

if page == "🧾  Báo giá":
    page_header(
        "Quotations",
        "Tạo báo giá nhanh từ kho sản phẩm, nhập SKU, số lượng, chiết khấu và lưu theo công trình."
    )

    # --------------------------------------------------------
    # QUOTATION KPIs
    # --------------------------------------------------------
    quote_stats = cursor.execute("""
        SELECT
            COUNT(*) AS total_quotes,
            SUM(CASE WHEN trang_thai='Nháp' THEN 1 ELSE 0 END) AS draft_quotes,
            COALESCE(SUM(tong_thanh_toan), 0) AS total_value
        FROM bao_gia
    """).fetchone()

    qk1, qk2, qk3, qk4 = st.columns(4)
    with qk1:
        kpi_card("🧾", "TỔNG BÁO GIÁ", int(quote_stats[0] or 0), "Đã lưu trong CRM")
    with qk2:
        kpi_card("📝", "BẢN NHÁP", int(quote_stats[1] or 0), "Đang chỉnh sửa")
    with qk3:
        kpi_card("💰", "TỔNG GIÁ TRỊ", money_vnd(quote_stats[2] or 0), "Giá trị báo giá")
    with qk4:
        product_count = int(cursor.execute("SELECT COUNT(*) FROM san_pham").fetchone()[0] or 0)
        kpi_card("📦", "SẢN PHẨM KHO", product_count, "Sẵn sàng chọn")

    # --------------------------------------------------------
    # SESSION CART
    # --------------------------------------------------------
    if "quote_lines_v330" not in st.session_state:
        st.session_state["quote_lines_v330"] = []

    quote_lines = st.session_state["quote_lines_v330"]

    # --------------------------------------------------------
    # NEW QUICK QUOTE
    # --------------------------------------------------------
    with st.expander("➕ Tạo báo giá nhanh", expanded=True):
        if df_ct.empty:
            st.warning("Chưa có công trình. Hãy tạo Công trình trước khi lập báo giá.")
        elif df_sp.empty:
            st.warning("Kho sản phẩm đang trống. Hãy thêm sản phẩm trước khi lập báo giá.")
        else:
            project_ids = df_ct["id"].astype(int).tolist()
            quote_project_id = st.selectbox(
                "Công trình / Deal *",
                project_ids,
                format_func=lambda x: f"#{x} - {df_ct.loc[df_ct['id']==x, 'ten_du_an'].iloc[0]}",
                key="quote_project_id_v330"
            )

            customer_row = get_project_customer(quote_project_id)
            project_name = str(customer_row[1] or "") if customer_row else ""
            customer_phone = str(customer_row[2] or "") if customer_row else ""
            customer_name = str(customer_row[3] or "") if customer_row else ""
            customer_company = str(customer_row[4] or "") if customer_row else ""

            h1, h2, h3 = st.columns([1.2, 1.5, 1.2])
            with h1:
                quote_number_preview = generate_quote_number()
                st.text_input(
                    "Số báo giá",
                    value=quote_number_preview,
                    disabled=True,
                    key="quote_number_preview_v330"
                )
            with h2:
                quote_customer_display = customer_name
                if customer_company:
                    quote_customer_display += f" / {customer_company}"
                st.text_input(
                    "Khách hàng",
                    value=quote_customer_display or "Chưa có tên khách hàng",
                    disabled=True,
                    key="quote_customer_display_v330"
                )
            with h3:
                quote_date = st.date_input(
                    "Ngày báo giá",
                    value=datetime.now().date(),
                    key="quote_date_v330"
                )

            st.caption(f"📞 {customer_phone or 'Chưa có SĐT'}  •  🏗️ {project_name}")

            st.markdown("#### 📦 Thêm sản phẩm từ Kho")

            add1, add2 = st.columns([2.4, 1])
            with add1:
                product_ids = df_sp["id"].astype(int).tolist()
                selected_product_id = st.selectbox(
                    "Tìm / chọn sản phẩm",
                    product_ids,
                    format_func=lambda x: (
                        f"{df_sp.loc[df_sp['id']==x, 'ma_code'].iloc[0]} | "
                        f"{df_sp.loc[df_sp['id']==x, 'ten_sp'].iloc[0]} | "
                        f"{df_sp.loc[df_sp['id']==x, 'hang'].iloc[0]}"
                    ),
                    key="quote_product_select_v330"
                )
            selected_product = df_sp[df_sp["id"] == selected_product_id].iloc[0]

            with add2:
                sku_input = st.text_input(
                    "SKU",
                    value="",
                    placeholder="Nhập SKU cho dòng báo giá",
                    key=f"quote_sku_{selected_product_id}"
                )

            p1, p2, p3, p4 = st.columns([1, 1.2, 1, 1.2])
            with p1:
                qty_input = st.number_input(
                    "Số lượng",
                    min_value=0.01,
                    value=1.0,
                    step=1.0,
                    key=f"quote_qty_{selected_product_id}"
                )
            with p2:
                price_input = st.number_input(
                    "Đơn giá",
                    min_value=0.0,
                    value=float(selected_product["gia_ban"] or 0),
                    step=10000.0,
                    format="%.0f",
                    key=f"quote_price_{selected_product_id}"
                )
            with p3:
                discount_input = st.number_input(
                    "CK %",
                    min_value=0.0,
                    max_value=100.0,
                    value=0.0,
                    step=1.0,
                    key=f"quote_discount_{selected_product_id}"
                )
            with p4:
                line_total_preview = float(qty_input) * float(price_input) * (1 - float(discount_input) / 100)
                st.metric("Thành tiền", money_vnd(line_total_preview))

            desc_input = st.text_area(
                "Mô tả / Thông số",
                value=str(selected_product["mo_ta"] or ""),
                height=80,
                key=f"quote_desc_{selected_product_id}"
            )
            line_note = st.text_input(
                "Ghi chú dòng sản phẩm",
                value="",
                placeholder="Ví dụ: 3000K / CRI90 / Beam 24° / Finish Black",
                key=f"quote_line_note_{selected_product_id}"
            )

            if st.button("➕ Thêm vào báo giá", type="primary", key="quote_add_line_v330"):
                st.session_state["quote_lines_v330"].append({
                    "line_key": f"{datetime.now().timestamp()}_{selected_product_id}",
                    "san_pham_id": int(selected_product_id),
                    "SKU": sku_input.strip(),
                    "Mã SP": str(selected_product["ma_code"] or ""),
                    "Tên sản phẩm": str(selected_product["ten_sp"] or ""),
                    "Hãng": str(selected_product["hang"] or ""),
                    "Mô tả / Thông số": desc_input.strip(),
                    "ĐVT": str(selected_product["dvt"] or "cái"),
                    "SL": float(qty_input),
                    "Đơn giá": float(price_input),
                    "CK %": float(discount_input),
                    "Ghi chú": line_note.strip(),
                })
                st.success("Đã thêm sản phẩm vào báo giá.")
                st.rerun()

            st.markdown("---")
            st.markdown("#### 🧾 Chi tiết báo giá")

            if not quote_lines:
                st.info("Chưa có sản phẩm. Chọn sản phẩm từ Kho ở phía trên và bấm “Thêm vào báo giá”.")
            else:
                # Convert current session lines to editable table.
                editor_rows = []
                for idx, line in enumerate(quote_lines, start=1):
                    amount = float(line["SL"]) * float(line["Đơn giá"]) * (1 - float(line["CK %"]) / 100)
                    editor_rows.append({
                        "Xóa": False,
                        "STT": idx,
                        "SKU": line["SKU"],
                        "Mã SP": line["Mã SP"],
                        "Tên sản phẩm": line["Tên sản phẩm"],
                        "Hãng": line["Hãng"],
                        "Mô tả / Thông số": line["Mô tả / Thông số"],
                        "ĐVT": line["ĐVT"],
                        "SL": float(line["SL"]),
                        "Đơn giá": float(line["Đơn giá"]),
                        "CK %": float(line["CK %"]),
                        "Thành tiền": amount,
                        "Ghi chú": line["Ghi chú"],
                    })

                quote_editor_df = pd.DataFrame(editor_rows)

                edited_quote_df = st.data_editor(
                    quote_editor_df,
                    width="stretch",
                    hide_index=True,
                    disabled=["STT", "Mã SP", "Tên sản phẩm", "Hãng", "ĐVT", "Thành tiền"],
                    column_config={
                        "Xóa": st.column_config.CheckboxColumn("Xóa"),
                        "STT": st.column_config.NumberColumn("STT", width="small"),
                        "SKU": st.column_config.TextColumn("SKU", width="medium"),
                        "Mã SP": st.column_config.TextColumn("Mã SP", width="medium"),
                        "Tên sản phẩm": st.column_config.TextColumn("Tên sản phẩm", width="large"),
                        "Mô tả / Thông số": st.column_config.TextColumn("Mô tả / Thông số", width="large"),
                        "SL": st.column_config.NumberColumn("SL", min_value=0.01, step=1.0),
                        "Đơn giá": st.column_config.NumberColumn("Đơn giá", min_value=0, step=10000, format="%.0f"),
                        "CK %": st.column_config.NumberColumn("CK %", min_value=0, max_value=100, step=1),
                        "Thành tiền": st.column_config.NumberColumn("Thành tiền", format="%.0f"),
                    },
                    key="quote_lines_editor_v330"
                )

                # Save edits back into session state before calculations.
                updated_lines = []
                delete_indexes = []
                for i, row in edited_quote_df.iterrows():
                    if bool(row.get("Xóa", False)):
                        delete_indexes.append(i)
                        continue
                    original = quote_lines[i]
                    updated_lines.append({
                        "line_key": original["line_key"],
                        "san_pham_id": original["san_pham_id"],
                        "SKU": str(row.get("SKU", "") or "").strip(),
                        "Mã SP": original["Mã SP"],
                        "Tên sản phẩm": original["Tên sản phẩm"],
                        "Hãng": original["Hãng"],
                        "Mô tả / Thông số": str(row.get("Mô tả / Thông số", "") or "").strip(),
                        "ĐVT": original["ĐVT"],
                        "SL": max(float(row.get("SL", 0) or 0), 0.01),
                        "Đơn giá": max(float(row.get("Đơn giá", 0) or 0), 0.0),
                        "CK %": min(max(float(row.get("CK %", 0) or 0), 0.0), 100.0),
                        "Ghi chú": str(row.get("Ghi chú", "") or "").strip(),
                    })

                if delete_indexes:
                    if st.button("🗑️ Xóa các dòng đã chọn", key="quote_delete_rows_v330"):
                        st.session_state["quote_lines_v330"] = updated_lines
                        st.success("Đã xóa dòng sản phẩm.")
                        st.rerun()
                else:
                    st.session_state["quote_lines_v330"] = updated_lines

                # Recalculate using edited values.
                subtotal = sum(
                    float(x["SL"]) * float(x["Đơn giá"]) * (1 - float(x["CK %"]) / 100)
                    for x in updated_lines
                )

                total1, total2 = st.columns([2, 1])
                with total1:
                    quote_note = st.text_area(
                        "Ghi chú báo giá",
                        placeholder="Điều kiện giao hàng, thời gian hiệu lực, ghi chú cho khách hàng...",
                        key="quote_note_v330"
                    )
                with total2:
                    vat_percent = st.number_input(
                        "VAT %",
                        min_value=0.0,
                        max_value=20.0,
                        value=8.0,
                        step=1.0,
                        key="quote_vat_v330"
                    )
                    vat_amount = subtotal * float(vat_percent) / 100
                    grand_total = subtotal + vat_amount
                    st.write(f"**Tạm tính:** {money_vnd(subtotal)}")
                    st.write(f"**VAT ({vat_percent:g}%):** {money_vnd(vat_amount)}")
                    st.markdown(f"### Tổng cộng: {money_vnd(grand_total)}")

                save1, save2 = st.columns([1, 1])
                with save1:
                    if st.button(
                        "💾 Lưu báo giá",
                        type="primary",
                        use_container_width=True,
                        key="quote_save_v330"
                    ):
                        final_lines = st.session_state.get("quote_lines_v330", [])
                        if not final_lines:
                            st.warning("Báo giá phải có ít nhất 1 sản phẩm.")
                        else:
                            quote_no = generate_quote_number()
                            now_text = datetime.now().strftime("%d/%m/%Y %H:%M")
                            cursor.execute("""
                                INSERT INTO bao_gia
                                (
                                    so_bao_gia, cong_trinh_id, ten_du_an_snapshot,
                                    thoai_khach, ten_khach_snapshot, ngay_bao_gia,
                                    trang_thai, ghi_chu, vat_percent,
                                    tong_truoc_thue, tien_vat, tong_thanh_toan,
                                    ngay_tao, ngay_cap_nhat
                                )
                                VALUES (?, ?, ?, ?, ?, ?, 'Nháp', ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                quote_no,
                                int(quote_project_id),
                                project_name,
                                customer_phone,
                                customer_name or customer_company,
                                quote_date.strftime("%d/%m/%Y"),
                                quote_note.strip(),
                                float(vat_percent),
                                float(subtotal),
                                float(vat_amount),
                                float(grand_total),
                                now_text,
                                now_text
                            ))
                            quote_id = int(cursor.lastrowid)

                            for line in final_lines:
                                line_amount = (
                                    float(line["SL"])
                                    * float(line["Đơn giá"])
                                    * (1 - float(line["CK %"]) / 100)
                                )
                                cursor.execute("""
                                    INSERT INTO bao_gia_chi_tiet
                                    (
                                        bao_gia_id, san_pham_id, sku,
                                        ma_code_snapshot, ten_sp_snapshot,
                                        mo_ta_snapshot, hang_snapshot, dvt_snapshot,
                                        so_luong, don_gia, chiet_khau_percent,
                                        thanh_tien, ghi_chu
                                    )
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    quote_id,
                                    int(line["san_pham_id"]),
                                    line["SKU"],
                                    line["Mã SP"],
                                    line["Tên sản phẩm"],
                                    line["Mô tả / Thông số"],
                                    line["Hãng"],
                                    line["ĐVT"],
                                    float(line["SL"]),
                                    float(line["Đơn giá"]),
                                    float(line["CK %"]),
                                    float(line_amount),
                                    line["Ghi chú"]
                                ))

                            # Project value follows latest quotation value.
                            cursor.execute("""
                                UPDATE cong_trinh_new
                                SET gia_tri_du_kien=?
                                WHERE id=?
                            """, (float(grand_total), int(quote_project_id)))

                            conn.commit()
                            st.session_state["quote_lines_v330"] = []
                            st.success(f"Đã lưu báo giá {quote_no} — {money_vnd(grand_total)}")
                            st.rerun()

                with save2:
                    if st.button(
                        "🧹 Xóa toàn bộ sản phẩm đang chọn",
                        use_container_width=True,
                        key="quote_clear_all_v330"
                    ):
                        st.session_state["quote_lines_v330"] = []
                        st.rerun()

    # --------------------------------------------------------
    # SAVED QUOTATIONS
    # --------------------------------------------------------
    st.markdown("### 📚 Báo giá đã lưu")
    saved_quotes = pd.read_sql_query("""
        SELECT
            id,
            so_bao_gia,
            ten_du_an_snapshot,
            ten_khach_snapshot,
            ngay_bao_gia,
            trang_thai,
            vat_percent,
            tong_thanh_toan,
            ngay_tao
        FROM bao_gia
        ORDER BY id DESC
    """, conn)

    if saved_quotes.empty:
        st.info("Chưa có báo giá nào được lưu.")
    else:
        sq1, sq2 = st.columns([2, 1])
        with sq1:
            quote_search = st.text_input(
                "🔎 Tìm báo giá",
                placeholder="Số báo giá / Công trình / Khách hàng",
                key="quote_search_v330"
            )
        with sq2:
            quote_status_filter = st.selectbox(
                "Trạng thái",
                ["Tất cả", "Nháp", "Đã gửi", "Đã chốt", "Hủy"],
                key="quote_status_filter_v330"
            )

        quote_view = saved_quotes.copy()
        if quote_search.strip():
            mask = (
                quote_view["so_bao_gia"].fillna("").str.contains(quote_search, case=False, na=False)
                | quote_view["ten_du_an_snapshot"].fillna("").str.contains(quote_search, case=False, na=False)
                | quote_view["ten_khach_snapshot"].fillna("").str.contains(quote_search, case=False, na=False)
            )
            quote_view = quote_view[mask]

        if quote_status_filter != "Tất cả":
            quote_view = quote_view[quote_view["trang_thai"] == quote_status_filter]

        display_quotes = quote_view[
            [
                "id", "so_bao_gia", "ten_du_an_snapshot",
                "ten_khach_snapshot", "ngay_bao_gia",
                "trang_thai", "tong_thanh_toan"
            ]
        ].copy()
        display_quotes["tong_thanh_toan"] = display_quotes["tong_thanh_toan"].apply(money_vnd)
        display_quotes.columns = [
            "ID", "Số báo giá", "Công trình",
            "Khách hàng", "Ngày", "Trạng thái", "Tổng cộng"
        ]
        st.dataframe(display_quotes, width="stretch", hide_index=True)

        with st.expander("🔍 Xem chi tiết / cập nhật trạng thái báo giá"):
            quote_ids = quote_view["id"].astype(int).tolist()
            if not quote_ids:
                st.info("Không có báo giá phù hợp bộ lọc.")
            else:
                selected_quote_id = st.selectbox(
                    "Chọn báo giá",
                    quote_ids,
                    format_func=lambda x: (
                        f"{quote_view.loc[quote_view['id']==x, 'so_bao_gia'].iloc[0]} | "
                        f"{quote_view.loc[quote_view['id']==x, 'ten_du_an_snapshot'].iloc[0]}"
                    ),
                    key="saved_quote_select_v330"
                )

                quote_header = cursor.execute("""
                    SELECT
                        so_bao_gia, ten_du_an_snapshot, ten_khach_snapshot,
                        ngay_bao_gia, trang_thai, ghi_chu,
                        vat_percent, tong_truoc_thue, tien_vat, tong_thanh_toan
                    FROM bao_gia
                    WHERE id=?
                """, (int(selected_quote_id),)).fetchone()

                detail_df = pd.read_sql_query("""
                    SELECT
                        sku,
                        ma_code_snapshot,
                        ten_sp_snapshot,
                        hang_snapshot,
                        mo_ta_snapshot,
                        dvt_snapshot,
                        so_luong,
                        don_gia,
                        chiet_khau_percent,
                        thanh_tien,
                        ghi_chu
                    FROM bao_gia_chi_tiet
                    WHERE bao_gia_id=?
                    ORDER BY id ASC
                """, conn, params=(int(selected_quote_id),))

                st.markdown(
                    f"#### {quote_header[0]} — {quote_header[1]}"
                )
                st.caption(
                    f"Khách hàng: {quote_header[2] or '—'} • "
                    f"Ngày: {quote_header[3]} • "
                    f"Trạng thái: {quote_header[4]}"
                )

                if not detail_df.empty:
                    detail_show = detail_df.copy()
                    detail_show.columns = [
                        "SKU", "Mã SP", "Tên sản phẩm", "Hãng",
                        "Mô tả / Thông số", "ĐVT", "SL",
                        "Đơn giá", "CK %", "Thành tiền", "Ghi chú"
                    ]
                    detail_show["Đơn giá"] = detail_show["Đơn giá"].apply(money_vnd)
                    detail_show["Thành tiền"] = detail_show["Thành tiền"].apply(money_vnd)
                    st.dataframe(detail_show, width="stretch", hide_index=True)

                tt1, tt2, tt3 = st.columns(3)
                with tt1:
                    st.metric("Tạm tính", money_vnd(quote_header[7]))
                with tt2:
                    st.metric(f"VAT {float(quote_header[6] or 0):g}%", money_vnd(quote_header[8]))
                with tt3:
                    st.metric("Tổng cộng", money_vnd(quote_header[9]))

                new_quote_status = st.selectbox(
                    "Cập nhật trạng thái",
                    ["Nháp", "Đã gửi", "Đã chốt", "Hủy"],
                    index=["Nháp", "Đã gửi", "Đã chốt", "Hủy"].index(quote_header[4])
                    if quote_header[4] in ["Nháp", "Đã gửi", "Đã chốt", "Hủy"] else 0,
                    key=f"quote_status_update_{selected_quote_id}"
                )

                if st.button(
                    "💾 Lưu trạng thái báo giá",
                    key=f"save_quote_status_{selected_quote_id}"
                ):
                    cursor.execute("""
                        UPDATE bao_gia
                        SET trang_thai=?, ngay_cap_nhat=?
                        WHERE id=?
                    """, (
                        new_quote_status,
                        datetime.now().strftime("%d/%m/%Y %H:%M"),
                        int(selected_quote_id)
                    ))
                    conn.commit()
                    st.success("Đã cập nhật trạng thái báo giá.")
                    st.rerun()

                st.caption(
                    "V3.3 lưu snapshot sản phẩm, SKU, giá và chiết khấu tại thời điểm tạo báo giá. "
                    "V3.4 sẽ bổ sung Revision và xuất PDF chuyên nghiệp."
                )


# ============================================================
# TAB 5 - KHO
# ============================================================

if page == "📦  Sản phẩm":
    page_header("Sản phẩm", "Quản lý danh mục sản phẩm, giá bán, mô tả và hình ảnh.")
    st.markdown("## 📦 Kho sản phẩm chiếu sáng")
    arr_danh_muc_den=["Downlight","Led dây","Thanh profile","Bộ nguồn","Đèn nam châm","Đèn trang trí","Đèn Steplight","Đèn gắn tường","Đèn Outdoor","Khác"]
    arr_hang_den=["Raynice","1962","Wullian","Khác"]
    arr_dvt=["cái","bộ","mét"]
    with st.expander("📋 Tra cứu / Danh sách sản phẩm", expanded=False):
        txt_tim_sp=st.text_input("🔎 Tìm sản phẩm", placeholder="Nhập tên hoặc mã code", key="product_search")
        f1,f2=st.columns(2)
        with f1: cbo_danh_muc=st.selectbox("Danh mục",["Tất cả"]+arr_danh_muc_den,key="product_filter_category")
        with f2: cbo_hang=st.selectbox("Hãng sản xuất",["Tất cả"]+arr_hang_den,key="product_filter_brand")
        df_sp_filtered=df_sp.copy()
        if txt_tim_sp.strip():
            q=txt_tim_sp.strip().lower(); mask=(df_sp_filtered["ma_code"].fillna("").astype(str).str.lower().str.contains(q,na=False)|df_sp_filtered["ten_sp"].fillna("").astype(str).str.lower().str.contains(q,na=False)|df_sp_filtered["mo_ta"].fillna("").astype(str).str.lower().str.contains(q,na=False)); df_sp_filtered=df_sp_filtered[mask]
        if cbo_danh_muc!="Tất cả": df_sp_filtered=df_sp_filtered[df_sp_filtered["danh_muc"]==cbo_danh_muc]
        if cbo_hang!="Tất cả": df_sp_filtered=df_sp_filtered[df_sp_filtered["hang"]==cbo_hang]
        st.markdown("### 📋 Danh sách sản phẩm")
        if df_sp_filtered.empty: st.info("Chưa có sản phẩm phù hợp bộ lọc.")
        else:
            show_sp=df_sp_filtered[["id","ma_code","ten_sp","danh_muc","hang","gia_ban","dvt","mo_ta","ghi_chu"]].copy(); show_sp["gia_ban"]=show_sp["gia_ban"].fillna(0).apply(lambda x:f"{float(x):,.0f} đ"); show_sp.columns=["ID","Mã code","Tên sản phẩm","Danh mục","Hãng","Giá bán","ĐVT","Mô tả","Ghi chú"]; st.dataframe(show_sp,use_container_width=True,hide_index=True)
            products_with_images=df_sp_filtered[df_sp_filtered["hinh_anh"].fillna("").astype(str).str.strip()!=""]
            if not products_with_images.empty:
                st.markdown("### 🖼️ Hình ảnh sản phẩm")
                for _,product in products_with_images.iterrows():
                    image_abs_path=os.path.join(BASE_DIR,str(product["hinh_anh"]))
                    if os.path.isfile(image_abs_path):
                        col_img,col_info=st.columns([1,3])
                        with col_img: st.image(image_abs_path,width=200)
                        with col_info:
                            st.markdown(f"**[{product['ma_code']}] {product['ten_sp']}**")
                            if str(product.get("mo_ta","") or "").strip(): st.write(str(product["mo_ta"]))
                            st.caption(f"{product['danh_muc']} • {product['hang']} • {float(product['gia_ban'] or 0):,.0f} đ/{product['dvt']}")
                        st.markdown("---")

    with st.expander("➕ Thêm sản phẩm mới",expanded=df_sp.empty):
        new_ma_code=st.text_input("Mã code sản phẩm *",key="new_product_code"); new_ten_sp=st.text_input("Tên sản phẩm *",key="new_product_name")
        c1,c2=st.columns(2)
        with c1: new_danh_muc=st.selectbox("Danh mục",arr_danh_muc_den,key="new_product_category")
        with c2: new_hang=st.selectbox("Hãng",arr_hang_den,key="new_product_brand")
        c1,c2=st.columns(2)
        with c1: new_dvt=st.selectbox("Đơn vị tính",arr_dvt,key="new_product_unit")
        with c2: new_gia_ban=st.number_input("Giá bán (VNĐ)",min_value=0.0,step=1000.0,key="new_product_price")
        new_mo_ta=st.text_area("Mô tả sản phẩm",placeholder="Nhập thông số, đặc điểm, công suất, kích thước, màu sắc...",key="new_product_desc")
        st.markdown("**Hình ảnh sản phẩm**")
        st.caption("Bạn có thể chọn file, kéo thả file ảnh vào khung bên dưới, hoặc copy ảnh rồi bấm nút Dán ảnh từ Clipboard.")
        img_col1,img_col2=st.columns([1.5,1])
        with img_col1:
            new_hinh_anh=st.file_uploader("Chọn / kéo thả ảnh",type=["png","jpg","jpeg","webp"],key="new_product_image")
        with img_col2:
            new_paste_result=paste_image_button("📋 Dán ảnh từ Clipboard",key="new_product_paste")
            if new_paste_result.image_data is not None:
                st.session_state["new_product_pasted_image"] = new_paste_result.image_data.copy()
        new_pasted_image=st.session_state.get("new_product_pasted_image")
        if new_hinh_anh is not None:
            st.image(new_hinh_anh,caption="Ảnh sản phẩm đã chọn",width=220)
        elif new_pasted_image is not None:
            st.image(new_pasted_image,caption="Ảnh đã dán từ Clipboard",width=220)
            if st.button("🧹 Bỏ ảnh đã dán",key="clear_new_pasted_image"):
                st.session_state.pop("new_product_pasted_image",None); st.rerun()
        new_ghi_chu=st.text_area("Ghi chú",key="product_note")
        if st.button("📦 Nhập sản phẩm vào kho",type="primary",key="add_product_btn"):
            if not new_ma_code.strip() or not new_ten_sp.strip(): st.warning("⚠️ Vui lòng nhập mã và tên sản phẩm.")
            else:
                try:
                    if new_hinh_anh is not None:
                        image_path=save_product_image(new_hinh_anh,new_ma_code.strip())
                    elif new_pasted_image is not None:
                        image_path=save_pasted_product_image(new_pasted_image,new_ma_code.strip())
                    else:
                        image_path=""
                    cursor.execute("""INSERT INTO san_pham (ma_code,ten_sp,danh_muc,hang,gia_ban,dvt,mo_ta,hinh_anh,ghi_chu) VALUES (?,?,?,?,?,?,?,?,?)""",(new_ma_code.strip().upper(),new_ten_sp.strip(),new_danh_muc,new_hang,float(new_gia_ban),new_dvt,new_mo_ta.strip(),image_path,new_ghi_chu.strip())); conn.commit(); st.session_state.pop("new_product_pasted_image",None); st.success("🎉 Đã thêm sản phẩm vào kho!"); st.rerun()
                except sqlite3.IntegrityError: st.error("❌ Mã sản phẩm đã tồn tại.")
    if not df_sp.empty:
        with st.expander("✏️ Chỉnh sửa / cập nhật sản phẩm"):
            sp_edit_id=st.selectbox("Chọn sản phẩm cần chỉnh sửa",df_sp["id"].tolist(),format_func=lambda x:f"{df_sp.loc[df_sp['id']==x,'ma_code'].values[0]} - {df_sp.loc[df_sp['id']==x,'ten_sp'].values[0]}",key="sp_edit_id"); sp_row=df_sp.loc[df_sp["id"]==sp_edit_id].iloc[0]
            cur_cat=str(sp_row.get("danh_muc","") or ""); cur_brand=str(sp_row.get("hang","") or ""); cur_unit=str(sp_row.get("dvt","") or ""); cat_idx=arr_danh_muc_den.index(cur_cat) if cur_cat in arr_danh_muc_den else len(arr_danh_muc_den)-1; brand_idx=arr_hang_den.index(cur_brand) if cur_brand in arr_hang_den else len(arr_hang_den)-1; unit_idx=arr_dvt.index(cur_unit) if cur_unit in arr_dvt else 0
            e1,e2=st.columns(2)
            with e1:
                edit_code=st.text_input("Mã code sản phẩm *",value=str(sp_row["ma_code"] or ""),key=f"edit_sp_code_{sp_edit_id}"); edit_name=st.text_input("Tên sản phẩm *",value=str(sp_row["ten_sp"] or ""),key=f"edit_sp_name_{sp_edit_id}"); edit_category=st.selectbox("Danh mục",arr_danh_muc_den,index=cat_idx,key=f"edit_sp_cat_{sp_edit_id}"); edit_brand=st.selectbox("Hãng",arr_hang_den,index=brand_idx,key=f"edit_sp_brand_{sp_edit_id}")
            with e2:
                edit_unit=st.selectbox("Đơn vị tính",arr_dvt,index=unit_idx,key=f"edit_sp_unit_{sp_edit_id}"); edit_price=st.number_input("Giá bán (VNĐ)",min_value=0.0,step=1000.0,value=float(sp_row.get("gia_ban",0) or 0),key=f"edit_sp_price_{sp_edit_id}"); current_img=str(sp_row.get("hinh_anh","") or "")
                if current_img:
                    current_abs=os.path.join(BASE_DIR,current_img)
                    if os.path.exists(current_abs): st.image(current_abs,caption="Ảnh hiện tại",width=180)
            edit_desc=st.text_area("Mô tả sản phẩm",value=str(sp_row.get("mo_ta","") or ""),key=f"edit_sp_desc_{sp_edit_id}")
            st.markdown("**Thay hình ảnh sản phẩm**")
            st.caption("Không chọn/dán ảnh mới nếu muốn giữ ảnh hiện tại.")
            ei1,ei2=st.columns([1.5,1])
            with ei1:
                replacement_image=st.file_uploader("Chọn / kéo thả ảnh mới",type=["png","jpg","jpeg","webp"],key=f"edit_sp_image_{sp_edit_id}")
            with ei2:
                edit_paste_result=paste_image_button("📋 Dán ảnh từ Clipboard",key=f"edit_sp_paste_{sp_edit_id}")
                if edit_paste_result.image_data is not None:
                    st.session_state[f"edit_sp_pasted_image_{sp_edit_id}"] = edit_paste_result.image_data.copy()
            edit_pasted_image=st.session_state.get(f"edit_sp_pasted_image_{sp_edit_id}")
            if replacement_image is not None:
                st.image(replacement_image,caption="Ảnh mới đã chọn",width=180)
            elif edit_pasted_image is not None:
                st.image(edit_pasted_image,caption="Ảnh mới dán từ Clipboard",width=180)
                if st.button("🧹 Bỏ ảnh đã dán",key=f"clear_edit_pasted_{sp_edit_id}"):
                    st.session_state.pop(f"edit_sp_pasted_image_{sp_edit_id}",None); st.rerun()
            edit_note=st.text_area("Ghi chú",value=str(sp_row.get("ghi_chu","") or ""),key=f"edit_sp_note_{sp_edit_id}")
            if st.button("💾 Cập nhật thông tin sản phẩm",type="primary",key="update_product_btn"):
                if not edit_code.strip() or not edit_name.strip(): st.warning("⚠️ Mã code và tên sản phẩm không được để trống.")
                else:
                    try:
                        final_image=current_img
                        if replacement_image is not None:
                            final_image=save_product_image(replacement_image,edit_code.strip())
                        elif edit_pasted_image is not None:
                            final_image=save_pasted_product_image(edit_pasted_image,edit_code.strip())
                        cursor.execute("""UPDATE san_pham SET ma_code=?,ten_sp=?,danh_muc=?,hang=?,gia_ban=?,dvt=?,mo_ta=?,hinh_anh=?,ghi_chu=? WHERE id=?""",(edit_code.strip().upper(),edit_name.strip(),edit_category,edit_brand,float(edit_price),edit_unit,edit_desc.strip(),final_image,edit_note.strip(),int(sp_edit_id))); conn.commit(); st.session_state.pop(f"edit_sp_pasted_image_{sp_edit_id}",None); st.success("✅ Đã cập nhật thông tin sản phẩm."); st.rerun()
                    except sqlite3.IntegrityError: st.error("❌ Mã code này đang được dùng cho sản phẩm khác.")
        with st.expander("🗑️ Xóa sản phẩm"):
            sp_del_id=st.selectbox("Chọn sản phẩm cần xóa",df_sp["id"].tolist(),format_func=lambda x:f"{df_sp.loc[df_sp['id']==x,'ma_code'].values[0]} - {df_sp.loc[df_sp['id']==x,'ten_sp'].values[0]}",key="sp_delete_id")
            if st.button("❌ Xác nhận xóa sản phẩm",type="primary",key="delete_product_btn"):
                cursor.execute("DELETE FROM san_pham WHERE id=?",(sp_del_id,)); conn.commit(); st.success("🎉 Đã xóa sản phẩm!"); st.rerun()


# ============================================================
# B2 - AGENT CONTROL CENTER
# ============================================================

if page == "🛡️  Agent Control":
    page_header(
        "Agent Control Center",
        "Kiểm soát mọi hành động do AI đề xuất trước khi dữ liệu CRM được thay đổi.",
        "AI AGENT SAFETY"
    )

    agent_counts = {
        row[0]: int(row[1] or 0)
        for row in cursor.execute("""
            SELECT status, COUNT(*)
            FROM agent_action_queue
            GROUP BY status
        """).fetchall()
    }
    ac1, ac2, ac3, ac4 = st.columns(4)
    with ac1:
        kpi_card("⏳", "CHỜ DUYỆT", agent_counts.get("Chờ duyệt", 0), "Chưa thay đổi CRM")
    with ac2:
        kpi_card("✓", "ĐÃ THỰC HIỆN", agent_counts.get("Đã thực hiện", 0), "Đã được phê duyệt")
    with ac3:
        kpi_card("⊘", "ĐÃ TỪ CHỐI", agent_counts.get("Đã từ chối", 0), "Không tác động dữ liệu")
    with ac4:
        kpi_card("!", "THỰC HIỆN LỖI", agent_counts.get("Thực hiện lỗi", 0), "Đã rollback an toàn")

    control_tab, tools_tab, log_tab = st.tabs([
        "⏳ Hàng chờ phê duyệt",
        "🧰 Chạy thử công cụ",
        "🕘 Nhật ký Agent"
    ])

    with control_tab:
        st.markdown("### Hành động đang chờ bạn quyết định")
        st.caption("Agent chỉ đề xuất. CRM chỉ thay đổi sau khi bạn bấm Duyệt và thực hiện.")
        pending_df = get_agent_queue("Chờ duyệt", 200)
        if pending_df.empty:
            st.success("Không có hành động nào đang chờ duyệt.")
        else:
            pending_show = pending_df[
                ["id", "action_type", "summary", "requested_by", "created_at"]
            ].copy()
            pending_show.columns = ["ID", "Loại hành động", "Nội dung", "Nguồn", "Thời điểm"]
            st.dataframe(pending_show, width="stretch", hide_index=True)

            action_uuid_options = pending_df["action_uuid"].tolist()
            selected_action_uuid = st.selectbox(
                "Chọn hành động để kiểm tra",
                action_uuid_options,
                format_func=lambda x: (
                    f"#{int(pending_df.loc[pending_df['action_uuid']==x, 'id'].iloc[0])} — "
                    f"{pending_df.loc[pending_df['action_uuid']==x, 'summary'].iloc[0]}"
                ),
                key="agent_pending_select"
            )
            selected_action = cursor.execute("""
                SELECT action_type, payload_json, summary, requested_by, created_at
                FROM agent_action_queue
                WHERE action_uuid=? AND status='Chờ duyệt'
            """, (selected_action_uuid,)).fetchone()

            if selected_action:
                action_type, payload_json, summary, requested_by, created_at = selected_action
                st.markdown(f"#### {summary}")
                st.caption(f"{action_type} • {requested_by} • {created_at}")
                try:
                    payload_preview = json.loads(payload_json)
                    st.json(payload_preview)
                except Exception:
                    st.code(payload_json)

                review_note = st.text_area(
                    "Ghi chú phê duyệt / từ chối",
                    placeholder="Ví dụ: Đã kiểm tra đúng công trình và ngày hẹn.",
                    key=f"agent_review_note_{selected_action_uuid}"
                )
                approve_col, reject_col = st.columns(2)
                with approve_col:
                    if st.button(
                        "✅ Duyệt và thực hiện",
                        type="primary",
                        use_container_width=True,
                        key=f"agent_approve_{selected_action_uuid}"
                    ):
                        try:
                            result = approve_and_execute_agent_action(
                                selected_action_uuid, review_note
                            )
                            st.success(f"Đã thực hiện an toàn: {result}")
                            st.rerun()
                        except Exception as action_error:
                            st.error(f"Hành động không được thực hiện: {action_error}")
                with reject_col:
                    if st.button(
                        "❌ Từ chối",
                        use_container_width=True,
                        key=f"agent_reject_{selected_action_uuid}"
                    ):
                        try:
                            reject_agent_action(selected_action_uuid, review_note)
                            st.success("Đã từ chối. CRM không bị thay đổi.")
                            st.rerun()
                        except Exception as reject_error:
                            st.error(f"Không thể từ chối: {reject_error}")

    with tools_tab:
        st.markdown("### Chạy thử quy trình đề xuất → phê duyệt")
        st.info(
            "Các form dưới đây chỉ đưa hành động vào hàng chờ. "
            "Bạn phải sang tab Hàng chờ phê duyệt để kiểm tra và thực hiện."
        )

        tool_activity, tool_existing, tool_stage = st.tabs([
            "➕ Tạo Activity",
            "📅 Xử lý Activity",
            "🏗️ Đổi giai đoạn"
        ])

        with tool_activity:
            if df_ct.empty:
                st.warning("Chưa có công trình để tạo Activity.")
            else:
                with st.form("agent_test_create_activity_form"):
                    project_ids = df_ct["id"].astype(int).tolist()
                    test_project_id = st.selectbox(
                        "Công trình *",
                        project_ids,
                        format_func=lambda x: f"#{x} - {df_ct.loc[df_ct['id']==x, 'ten_du_an'].iloc[0]}"
                    )
                    ta1, ta2, ta3 = st.columns(3)
                    with ta1:
                        test_activity_type = st.selectbox(
                            "Loại Activity", ["Follow-up", "Call", "Meeting", "Site Survey", "Deadline"]
                        )
                    with ta2:
                        test_activity_date = st.date_input(
                            "Ngày hẹn *", value=datetime.now().date() + timedelta(days=1)
                        )
                    with ta3:
                        test_activity_priority = st.selectbox(
                            "Ưu tiên", ["High", "Medium", "Low"], index=1
                        )
                    test_activity_content = st.text_area(
                        "Nội dung công việc *",
                        placeholder="Ví dụ: Gọi khách xác nhận mẫu đèn và lịch khảo sát."
                    )
                    test_activity_note = st.text_area("Ghi chú")
                    queue_activity_submit = st.form_submit_button(
                        "Đưa vào hàng chờ", type="primary", use_container_width=True
                    )
                    if queue_activity_submit:
                        try:
                            action_uuid = queue_agent_action(
                                "CREATE_ACTIVITY",
                                {
                                    "project_id": int(test_project_id),
                                    "activity_type": test_activity_type,
                                    "content": test_activity_content,
                                    "due_date": test_activity_date.strftime("%d/%m/%Y"),
                                    "priority": test_activity_priority,
                                    "note": test_activity_note,
                                },
                                f"Tạo {test_activity_type} cho {df_ct.loc[df_ct['id']==test_project_id, 'ten_du_an'].iloc[0]}",
                                "Kiểm thử Bước 2"
                            )
                            st.success(f"Đã đưa vào hàng chờ: {action_uuid[:8]}")
                        except Exception as queue_error:
                            st.error(f"Không thể tạo đề xuất: {queue_error}")

        with tool_existing:
            open_activity_df = pd.read_sql_query("""
                SELECT a.id, a.cong_trinh_id, c.ten_du_an, a.loai,
                       a.noi_dung, a.ngay_hen, a.uu_tien
                FROM cong_trinh_hoat_dong a
                JOIN cong_trinh_new c ON c.id=a.cong_trinh_id
                WHERE a.trang_thai='Đang làm'
                ORDER BY a.id DESC
            """, conn)
            if open_activity_df.empty:
                st.info("Không có Activity đang làm.")
            else:
                with st.form("agent_test_existing_activity_form"):
                    open_activity_ids = open_activity_df["id"].astype(int).tolist()
                    test_activity_id = st.selectbox(
                        "Activity *",
                        open_activity_ids,
                        format_func=lambda x: (
                            f"#{x} - {open_activity_df.loc[open_activity_df['id']==x, 'ten_du_an'].iloc[0]} | "
                            f"{open_activity_df.loc[open_activity_df['id']==x, 'noi_dung'].iloc[0]}"
                        )
                    )
                    activity_operation = st.radio(
                        "Hành động", ["Dời thời hạn", "Đánh dấu hoàn thành"], horizontal=True
                    )
                    new_activity_date = st.date_input(
                        "Ngày hẹn mới",
                        value=datetime.now().date() + timedelta(days=3),
                        disabled=activity_operation != "Dời thời hạn"
                    )
                    activity_operation_note = st.text_area("Ghi chú xử lý")
                    queue_existing_submit = st.form_submit_button(
                        "Đưa vào hàng chờ", type="primary", use_container_width=True
                    )
                    if queue_existing_submit:
                        try:
                            selected_activity_name = str(
                                open_activity_df.loc[
                                    open_activity_df["id"] == test_activity_id, "noi_dung"
                                ].iloc[0]
                            )
                            if activity_operation == "Dời thời hạn":
                                queued_type = "RESCHEDULE_ACTIVITY"
                                queued_payload = {
                                    "activity_id": int(test_activity_id),
                                    "due_date": new_activity_date.strftime("%d/%m/%Y"),
                                    "note": activity_operation_note,
                                }
                                queued_summary = f"Dời thời hạn: {selected_activity_name}"
                            else:
                                queued_type = "COMPLETE_ACTIVITY"
                                queued_payload = {
                                    "activity_id": int(test_activity_id),
                                    "note": activity_operation_note,
                                }
                                queued_summary = f"Hoàn thành: {selected_activity_name}"
                            action_uuid = queue_agent_action(
                                queued_type, queued_payload, queued_summary, "Kiểm thử Bước 2"
                            )
                            st.success(f"Đã đưa vào hàng chờ: {action_uuid[:8]}")
                        except Exception as queue_error:
                            st.error(f"Không thể tạo đề xuất: {queue_error}")

        with tool_stage:
            if df_ct.empty:
                st.warning("Chưa có công trình để cập nhật giai đoạn.")
            else:
                with st.form("agent_test_project_stage_form"):
                    project_ids = df_ct["id"].astype(int).tolist()
                    stage_project_id = st.selectbox(
                        "Công trình *",
                        project_ids,
                        format_func=lambda x: f"#{x} - {df_ct.loc[df_ct['id']==x, 'ten_du_an'].iloc[0]}"
                    )
                    stage_value = st.selectbox(
                        "Giai đoạn mới *",
                        ["Tiếp cận", "Khảo sát", "Báo giá", "Thương lượng", "Chốt đơn", "Triển khai", "Hoàn thành", "Tạm dừng"]
                    )
                    queue_stage_submit = st.form_submit_button(
                        "Đưa vào hàng chờ", type="primary", use_container_width=True
                    )
                    if queue_stage_submit:
                        try:
                            project_name = str(
                                df_ct.loc[df_ct["id"] == stage_project_id, "ten_du_an"].iloc[0]
                            )
                            action_uuid = queue_agent_action(
                                "UPDATE_PROJECT_STAGE",
                                {"project_id": int(stage_project_id), "stage": stage_value},
                                f"Chuyển {project_name} sang giai đoạn {stage_value}",
                                "Kiểm thử Bước 2"
                            )
                            st.success(f"Đã đưa vào hàng chờ: {action_uuid[:8]}")
                        except Exception as queue_error:
                            st.error(f"Không thể tạo đề xuất: {queue_error}")

        st.markdown("### Bộ công cụ đã khóa quyền")
        tool_status_df = pd.DataFrame([
            ["CREATE_ACTIVITY", "Tạo Activity", "Bắt buộc duyệt"],
            ["RESCHEDULE_ACTIVITY", "Dời thời hạn", "Bắt buộc duyệt"],
            ["COMPLETE_ACTIVITY", "Hoàn thành Activity", "Bắt buộc duyệt"],
            ["UPDATE_PROJECT_STAGE", "Đổi giai đoạn", "Bắt buộc duyệt"],
            ["CREATE_QUOTE_DRAFT", "Tạo báo giá nháp", "Bắt buộc duyệt + exact match"],
        ], columns=["Mã công cụ", "Chức năng", "Kiểm soát"])
        st.dataframe(tool_status_df, width="stretch", hide_index=True)

    with log_tab:
        st.markdown("### Nhật ký kiểm toán")
        st.caption("Lịch sử chỉ đọc: đề xuất, phê duyệt, từ chối, thành công và lỗi.")
        agent_log_df = pd.read_sql_query("""
            SELECT id, action_uuid, event_type, action_type, summary, created_at
            FROM agent_action_log
            ORDER BY id DESC
            LIMIT 200
        """, conn)
        if agent_log_df.empty:
            st.info("Chưa có hoạt động Agent nào được ghi nhận.")
        else:
            log_show = agent_log_df.copy()
            log_show["action_uuid"] = log_show["action_uuid"].astype(str).str[:8]
            log_show.columns = ["ID", "Mã", "Sự kiện", "Loại", "Nội dung", "Thời điểm"]
            st.dataframe(log_show, width="stretch", hide_index=True)

            with st.expander("🔍 Xem dữ liệu chi tiết của một sự kiện"):
                log_ids = agent_log_df["id"].astype(int).tolist()
                selected_log_id = st.selectbox("Chọn ID nhật ký", log_ids, key="agent_log_select")
                log_detail = cursor.execute("""
                    SELECT event_type, action_type, summary, payload_json, result_json, created_at
                    FROM agent_action_log WHERE id=?
                """, (int(selected_log_id),)).fetchone()
                if log_detail:
                    st.caption(
                        f"{log_detail[0]} • {log_detail[1]} • {log_detail[5]}"
                    )
                    st.write(log_detail[2])
                    detail_left, detail_right = st.columns(2)
                    with detail_left:
                        st.markdown("**Dữ liệu đề xuất**")
                        try:
                            st.json(json.loads(log_detail[3] or "{}"))
                        except Exception:
                            st.code(log_detail[3] or "{}")
                    with detail_right:
                        st.markdown("**Kết quả**")
                        try:
                            st.json(json.loads(log_detail[4] or "{}"))
                        except Exception:
                            st.code(log_detail[4] or "{}")


# ============================================================
# TAB 6 - THÔNG TIN CÔNG TY
# ============================================================

if page == "🏢  Thông tin công ty":
    page_header("Thông tin công ty", "Quản lý nhận diện doanh nghiệp để dùng cho báo giá, đơn hàng và hồ sơ dự án.")
    st.markdown("## 🏢 Thông tin doanh nghiệp")
    if not df_cty_saved.empty:
        company=df_cty_saved.iloc[0]; cur_ten=str(company.get("ten_cty","") or ""); cur_tru_so=str(company.get("tru_so","") or ""); cur_vpdd=str(company.get("vpdd","") or ""); cur_sdt=str(company.get("sdt","") or ""); cur_email=str(company.get("email","") or ""); cur_website=str(company.get("website","") or ""); cur_facebook=str(company.get("facebook","") or ""); cur_logo=str(company.get("logo_path","") or "")
    else:
        cur_ten=cur_tru_so=cur_vpdd=cur_sdt=""; cur_email=cur_website=cur_facebook=cur_logo=""
    brand_col,info_col=st.columns([0.8,2.2],gap="large")
    with brand_col:
        st.markdown("### 🖼️ Logo công ty")
        if cur_logo:
            logo_abs=os.path.join(BASE_DIR,cur_logo)
            if os.path.exists(logo_abs): st.image(logo_abs,caption="Logo hiện tại",width=240)
            else: st.caption("Logo đã lưu nhưng file hiện không còn ở thư mục CRM.")
        else: st.info("Chưa có logo công ty.")
        new_logo=st.file_uploader("Upload / thay Logo",type=["png","jpg","jpeg","webp"],help="Khuyến nghị PNG nền trong suốt để dùng đẹp trên báo giá.",key="company_logo_upload")
        if new_logo is not None: st.image(new_logo,caption="Logo mới đã chọn",width=240)
    with info_col:
        c1,c2=st.columns(2)
        with c1:
            ten_cty=st.text_input("Tên công ty / pháp nhân *",value=cur_ten,key="company_name"); tru_so=st.text_input("Địa chỉ trụ sở chính",value=cur_tru_so,key="company_hq"); vpdd=st.text_input("Văn phòng / Showroom",value=cur_vpdd,key="company_office"); sdt=st.text_input("Số điện thoại / Hotline",value=cur_sdt,key="company_phone")
        with c2:
            email=st.text_input("Email",value=cur_email,key="company_email"); website=st.text_input("Website",value=cur_website,key="company_website"); facebook=st.text_input("Facebook / Fanpage",value=cur_facebook,key="company_facebook")
        st.caption("Logo và thông tin này sẽ là nguồn dữ liệu chung cho báo giá/PDF và các module V3 sau này.")
        if st.button("💾 Lưu / cập nhật thông tin doanh nghiệp",type="primary",key="save_company_btn"):
            if not ten_cty.strip(): st.warning("⚠️ Vui lòng nhập tên công ty.")
            else:
                final_logo=cur_logo
                if new_logo is not None: final_logo=save_company_logo(new_logo)
                cursor.execute("""INSERT INTO cau_hinh_doanh_nghiep (id,ten_cty,tru_so,vpdd,sdt,email,website,facebook,logo_path) VALUES (1,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ten_cty=excluded.ten_cty,tru_so=excluded.tru_so,vpdd=excluded.vpdd,sdt=excluded.sdt,email=excluded.email,website=excluded.website,facebook=excluded.facebook,logo_path=excluded.logo_path""",(ten_cty.strip(),tru_so.strip(),vpdd.strip(),sdt.strip(),email.strip(),website.strip(),facebook.strip(),final_logo)); conn.commit(); st.success("🎉 Đã cập nhật thông tin doanh nghiệp và Logo!"); st.rerun()


# ============================================================
# 8. TỰ ĐÓNG DATABASE KHI APP KẾT THÚC
# ============================================================

# Không cần đóng thủ công trong Streamlit vì
# connection được quản lý bằng st.cache_resource.
