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
APP_VERSION = "3.0.2"
UPDATE_CONFIG_FILE = os.path.join(BASE_DIR, "update_config.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)


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

conn.commit()

# Nâng cấp database cũ: chỉ thêm cột, tuyệt đối không xóa dữ liệu hiện có.
ensure_column("khach_hang_goc", "ten_cong_ty", "TEXT DEFAULT ''")
ensure_column("khach_hang_goc", "dia_chi_cong_ty", "TEXT DEFAULT ''")
ensure_column("cong_trinh_new", "dia_chi_cong_trinh", "TEXT DEFAULT ''")
ensure_column("cong_trinh_new", "viec_tiep_theo", "TEXT DEFAULT ''")
ensure_column("cong_trinh_new", "ngay_theo_doi", "TEXT DEFAULT ''")
ensure_column("san_pham", "mo_ta", "TEXT DEFAULT ''")
ensure_column("san_pham", "hinh_anh", "TEXT DEFAULT ''")
ensure_column("cau_hinh_doanh_nghiep", "logo_path", "TEXT DEFAULT ''")


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
        ["📊  Tổng quan", "👥  Khách hàng", "🏗️  Công trình", "🧾  Báo giá", "📦  Sản phẩm", "🏢  Thông tin công ty"],
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
    st.caption("SQLite local • Dữ liệu & ảnh không bị ghi đè")


# ============================================================
# 6. LOAD DATA
# ============================================================
df_kh, df_ct, df_sp, df_cty_saved = load_data()

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

    follow_rows = []
    if not df_ct.empty:
        for _, r in df_ct.iterrows():
            stage = str(r.get("giai_doan", "") or "").strip()
            action = str(r.get("viec_tiep_theo", "") or "").strip()
            follow_raw = r.get("ngay_theo_doi", "")
            follow_date = parse_follow_date(follow_raw)

            # Chỉ đưa vào trung tâm công việc nếu còn dự án và có việc tiếp theo.
            if stage == "Hoàn thành" or not action or follow_date is None:
                continue

            follow_rows.append({
                "id": r.get("id"),
                "ten_du_an": str(r.get("ten_du_an", "") or ""),
                "giai_doan": stage,
                "uu_tien": str(r.get("uu_tien", "") or ""),
                "viec_tiep_theo": action,
                "ngay_theo_doi": follow_date,
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
        '<div class="panel-sub">Ưu tiên xử lý theo ngày follow-up đã đặt trong từng công trình.</div>',
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
        "💡 Mẹo: ở menu Công trình, hãy luôn điền 'Việc cần làm tiếp theo' "
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

if page == "🏗️  Công trình":
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
            k.ten AS Chu_Dau_Tu,
            c.dia_chi_cong_trinh AS Dia_Chi,
            c.uu_tien,
            c.giai_doan,
            c.viec_tiep_theo,
            c.ngay_theo_doi,
            c.ngay_khoi_tao,
            c.note
        FROM cong_trinh_new c
        LEFT JOIN khach_hang_goc k ON c.thoai_khach = k.thoai
        ORDER BY
            CASE c.uu_tien WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
            c.id DESC
    """, conn)

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
            "giai_doan", "viec_tiep_theo", "ngay_theo_doi"
        ]
        display = view[display_cols].copy()
        display.columns = [
            "ID", "Công trình", "Khách hàng / CĐT", "Ưu tiên",
            "Giai đoạn", "Việc tiếp theo", "Ngày theo dõi"
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
                if st.button("💾 Lưu cập nhật dự án", type="primary", key="save_project_progress"):
                    cursor.execute("""
                        UPDATE cong_trinh_new
                        SET giai_doan=?, uu_tien=?, viec_tiep_theo=?, ngay_theo_doi=?
                        WHERE id=?
                    """, (new_stage, new_priority, next_action.strip(), follow_date_text.strip(), int(edit_id)))
                    conn.commit()
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
                         ngay_khoi_tao, viec_tiep_theo, ngay_theo_doi, note)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ten_du_an.strip(), thoai_khach_save, dia_chi_cong_trinh.strip(),
                        uu_tien, giai_doan, ngay_khoi_tao.strftime("%d/%m/%Y"),
                        next_action_new.strip(), follow_new.strip(), note.strip()
                    ))
                    conn.commit()
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
    page_header("Báo giá", "Tạo và quản lý báo giá theo khách hàng, công trình và sản phẩm.")

    st.markdown("## 🧾 Báo giá")

    if (
        not df_cty_saved.empty
        and str(df_cty_saved.iloc[0]["ten_cty"]).strip()
    ):

        company = df_cty_saved.iloc[0]

        st.success(
            f"🏢 Đơn vị xuất báo giá: "
            f"{company['ten_cty']}"
        )

        st.write(
            f"📍 Địa chỉ: {company['tru_so']}"
        )

        st.write(
            f"📞 Hotline: {company['sdt']}"
        )

        st.write(
            f"📧 Email: {company['email']}"
        )

    else:

        st.warning(
            "⚠️ Chưa cấu hình thông tin doanh nghiệp."
        )

    st.markdown("---")

    st.info(
        "Module báo giá hiện đang là khung cơ bản. "
        "Có thể mở rộng thêm chọn khách hàng, "
        "chọn sản phẩm, số lượng, chiết khấu, VAT "
        "và xuất PDF."
    )


# ============================================================
# TAB 5 - KHO
# ============================================================

if page == "📦  Sản phẩm":
    page_header("Sản phẩm", "Quản lý danh mục sản phẩm, giá bán, mô tả và hình ảnh.")
    st.markdown("## 📦 Kho sản phẩm chiếu sáng")
    arr_danh_muc_den=["Downlight","Led dây","Thanh profile","Bộ nguồn","Đèn nam châm","Đèn trang trí","Khác"]
    arr_hang_den=["Raynice","1962","Wullian","Khác"]
    arr_dvt=["cái","bộ","mét"]
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
