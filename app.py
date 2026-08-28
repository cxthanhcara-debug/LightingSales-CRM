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
import urllib.request
import urllib.error
from datetime import datetime

# ============================================================
# 1. TỰ KIỂM TRA / CÀI STREAMLIT + PANDAS
# ============================================================

def install_package(package):
    try:
        __import__(package)
    except ImportError:
        print(f"Đang cài {package}...")
        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            package
        ])


install_package("streamlit")
install_package("pandas")


import pandas as pd
import streamlit as st


# ============================================================
# 2. CẤU HÌNH DATABASE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "lightingsales.db")
PRODUCT_IMAGE_DIR = os.path.join(BASE_DIR, "product_images")
os.makedirs(PRODUCT_IMAGE_DIR, exist_ok=True)

# Phiên bản hiện tại và cấu hình cập nhật tự động
APP_VERSION = "2.3.0"
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
ensure_column("san_pham", "mo_ta", "TEXT DEFAULT ''")
ensure_column("san_pham", "hinh_anh", "TEXT DEFAULT ''")


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
            facebook
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
[data-testid="stSidebar"] { background: #111827; border-right: 1px solid #1F2937; }
[data-testid="stSidebar"] * { color: #E5E7EB; }
[data-testid="stSidebar"] .stRadio label { font-size: 0.96rem; }
[data-testid="stSidebar"] div[role="radiogroup"] label { padding: 0.58rem 0.65rem; border-radius: 10px; margin: 0.12rem 0; }
[data-testid="stSidebar"] div[role="radiogroup"] label:hover { background: #1F2937; }
[data-testid="stSidebar"] .stButton button { border-radius: 10px; }
.sidebar-brand { padding: 0.45rem 0 1.15rem 0; border-bottom: 1px solid #273244; margin-bottom: 1rem; }
.sidebar-brand .brand-name { font-size: 1.12rem; font-weight: 800; color: #FFFFFF; }
.sidebar-brand .brand-sub { font-size: .78rem; color: #94A3B8; margin-top: .18rem; }
.sidebar-section { color: #94A3B8; font-size: .72rem; font-weight: 800; letter-spacing: .08em; margin: 1rem 0 .35rem 0; }
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
    page_header("Tổng quan kinh doanh", "Theo dõi nhanh khách hàng, công trình và danh mục sản phẩm của LightingSales.")

    high_priority = 0
    active_projects = 0
    if not df_ct.empty:
        if "uu_tien" in df_ct.columns:
            high_priority = int(df_ct["uu_tien"].fillna("").astype(str).str.lower().eq("high").sum())
        if "giai_doan" in df_ct.columns:
            active_projects = int((~df_ct["giai_doan"].fillna("").isin(["Hoàn thành", ""])).sum())

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi_card("👥", "Khách hàng", f"{len(df_kh)}", "Tổng hồ sơ khách hàng")
    with c2:
        kpi_card("🏗️", "Công trình", f"{len(df_ct)}", f"{active_projects} dự án đang theo dõi")
    with c3:
        kpi_card("📦", "Sản phẩm", f"{len(df_sp)}", "Mặt hàng trong danh mục")
    with c4:
        kpi_card("⚡", "Ưu tiên cao", f"{high_priority}", "Công trình cần chú ý")

    st.markdown("")
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        st.markdown('<div class="panel-title">🏗️ Công trình gần đây</div>', unsafe_allow_html=True)
        st.markdown('<div class="panel-sub">Các công trình mới nhất đang có trong hệ thống.</div>', unsafe_allow_html=True)
        if df_ct.empty:
            st.info("Chưa có công trình. Vào menu Công trình để thêm dự án đầu tiên.")
        else:
            recent_ct = df_ct[["ten_du_an", "dia_chi_cong_trinh", "uu_tien", "giai_doan", "ngay_khoi_tao"]].head(7).copy()
            recent_ct.columns = ["Công trình", "Địa chỉ", "Ưu tiên", "Giai đoạn", "Ngày khởi tạo"]
            st.dataframe(recent_ct, use_container_width=True, hide_index=True)
    with right:
        st.markdown('<div class="panel-title">👥 Khách hàng mới</div>', unsafe_allow_html=True)
        st.markdown('<div class="panel-sub">Danh sách khách hàng được thêm gần đây.</div>', unsafe_allow_html=True)
        if df_kh.empty:
            st.info("Chưa có khách hàng. Vào menu Khách hàng để tạo hồ sơ đầu tiên.")
        else:
            recent_kh = df_kh[["ten", "ten_cong_ty", "phan_loai_kh", "ngay_tao"]].head(7).copy()
            recent_kh.columns = ["Khách hàng", "Công ty", "Phân loại", "Ngày tạo"]
            st.dataframe(recent_kh, use_container_width=True, hide_index=True)

    st.markdown("")
    st.info("💡 Dữ liệu được lưu cục bộ trong lightingsales.db. Cập nhật code không ghi đè database hoặc ảnh sản phẩm.")


# ============================================================
# TAB 2 - KHÁCH HÀNG
# ============================================================

if page == "👥  Khách hàng":
    page_header("Khách hàng", "Quản lý khách hàng, công ty, địa chỉ và phân loại đối tác.")

    st.markdown("## 👥 Quản lý khách hàng")

    st.markdown("### ➕ Thêm khách hàng mới")

    ten = st.text_input(
        "Họ và tên *",
        key="kh_ten"
    )

    thoai = st.text_input(
        "Số điện thoại *",
        key="kh_thoai"
    )

    ten_cong_ty = st.text_input(
        "Tên công ty",
        placeholder="Ví dụ: Công ty TNHH ABC",
        key="kh_ten_cong_ty"
    )

    dia_chi_cong_ty = st.text_input(
        "Địa chỉ công ty",
        placeholder="Nhập địa chỉ công ty của khách hàng",
        key="kh_dia_chi_cong_ty"
    )

    phan_loai_kh = st.selectbox(
        "Phân loại khách hàng",
        [
            "Chủ đầu tư",
            "Nhà thầu",
            "Kiến trúc",
            "Nội thất",
            "Khách lẻ"
        ],
        key="kh_loai"
    )

    if st.button(
        "➕ Thêm hồ sơ khách hàng",
        type="primary"
    ):

        if not ten.strip() or not thoai.strip():

            st.warning(
                "⚠️ Vui lòng nhập đầy đủ tên và số điện thoại."
            )

        else:

            try:

                cursor.execute("""
                    INSERT INTO khach_hang_goc
                    (ten, thoai, ten_cong_ty, dia_chi_cong_ty, phan_loai_kh, ngay_tao)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    ten.strip(),
                    thoai.strip(),
                    ten_cong_ty.strip(),
                    dia_chi_cong_ty.strip(),
                    phan_loai_kh,
                    datetime.now().strftime("%Y-%m-%d")
                ))

                conn.commit()

                st.success(
                    "🎉 Đã thêm khách hàng thành công!"
                )

                st.rerun()

            except sqlite3.IntegrityError:

                st.error(
                    "❌ Số điện thoại này đã tồn tại!"
                )

    st.markdown("---")

    st.markdown("### 📋 Danh sách khách hàng")

    df_kh_display = df_kh.rename(columns={
        "ten": "Họ và tên",
        "thoai": "Số điện thoại",
        "ten_cong_ty": "Công ty",
        "dia_chi_cong_ty": "Địa chỉ công ty",
        "phan_loai_kh": "Phân loại",
        "ngay_tao": "Ngày tạo"
    })

    st.dataframe(
        df_kh_display,
        width="stretch",
        hide_index=True
    )

    if not df_kh.empty:

        with st.expander(
            "🗑️ Xóa khách hàng"
        ):

            kh_del_id = st.selectbox(
                "Chọn khách hàng",
                df_kh["id"].tolist(),
                format_func=lambda x:
                    f"ID {x} - "
                    f"{df_kh.loc[df_kh['id'] == x, 'ten'].values[0]} "
                    f"({df_kh.loc[df_kh['id'] == x, 'thoai'].values[0]})"
            )

            if st.button(
                "❌ Xác nhận xóa",
                type="primary"
            ):

                cursor.execute(
                    "DELETE FROM khach_hang_goc WHERE id = ?",
                    (kh_del_id,)
                )

                conn.commit()

                st.success(
                    "🎉 Đã xóa khách hàng!"
                )

                st.rerun()


# ============================================================
# TAB 3 - CÔNG TRÌNH
# ============================================================

if page == "🏗️  Công trình":
    page_header("Công trình", "Theo dõi công trình, địa chỉ, mức ưu tiên và giai đoạn bán hàng.")

    st.markdown("## 🏗️ Quản lý công trình / dự án")

    st.markdown("### ➕ Tạo công trình mới")

    ten_du_an = st.text_input(
        "Tên dự án / công trình *",
        placeholder="Ví dụ: Penthouse Landmark 81"
    )

    dia_chi_cong_trinh = st.text_input(
        "Địa chỉ công trình",
        placeholder="Nhập địa chỉ thi công / địa điểm dự án"
    )

    if not df_kh.empty:

        options_kh = {
            f"{r['ten']} - {r['thoai']}": r["thoai"]
            for _, r in df_kh.iterrows()
        }

        kh_label = st.selectbox(
            "Khách hàng / chủ đầu tư *",
            list(options_kh.keys())
        )

        thoai_khach_save = options_kh[kh_label]

    else:

        st.warning(
            "⚠️ Chưa có khách hàng. "
            "Hãy tạo khách hàng trước."
        )

        thoai_khach_save = ""

    uu_tien = st.selectbox(
        "Mức ưu tiên",
        ["High", "Medium", "Low"]
    )

    giai_doan = st.selectbox(
        "Giai đoạn dự án",
        [
            "Báo giá",
            "Thương lượng",
            "Triển khai",
            "Hoàn thành"
        ]
    )

    ngay_khoi_tao = st.date_input(
        "Ngày khởi tạo",
        value=datetime.now().date()
    )

    note = st.text_area(
        "Ghi chú",
        key="project_note"
    )

    if st.button(
        "💾 Lưu công trình",
        type="primary"
    ):

        if not ten_du_an.strip():

            st.warning(
                "⚠️ Vui lòng nhập tên dự án."
            )

        elif not thoai_khach_save:

            st.warning(
                "⚠️ Vui lòng chọn khách hàng."
            )

        else:

            cursor.execute("""
                INSERT INTO cong_trinh_new
                (
                    ten_du_an,
                    thoai_khach,
                    dia_chi_cong_trinh,
                    uu_tien,
                    giai_doan,
                    ngay_khoi_tao,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ten_du_an.strip(),
                thoai_khach_save,
                dia_chi_cong_trinh.strip(),
                uu_tien,
                giai_doan,
                ngay_khoi_tao.strftime("%d/%m/%Y"),
                note.strip()
            ))

            conn.commit()

            st.success(
                "🎉 Đã tạo công trình!"
            )

            st.rerun()

    st.markdown("---")

    st.markdown("### 📋 Danh sách công trình")

    df_ct_joined = pd.read_sql_query("""
        SELECT
            c.id,
            c.ten_du_an,
            c.dia_chi_cong_trinh AS Dia_Chi_Cong_Trinh,
            k.ten AS Chu_Dau_Tu,
            c.uu_tien,
            c.giai_doan,
            c.ngay_khoi_tao,
            c.note
        FROM cong_trinh_new c
        LEFT JOIN khach_hang_goc k
            ON c.thoai_khach = k.thoai
        ORDER BY c.id DESC
    """, conn)

    st.dataframe(
        df_ct_joined,
        width="stretch",
        hide_index=True
    )

    if not df_ct.empty:

        with st.expander(
            "🗑️ Xóa công trình"
        ):

            ct_del_id = st.selectbox(
                "Chọn công trình",
                df_ct["id"].tolist(),
                format_func=lambda x:
                    f"ID {x} - "
                    f"{df_ct.loc[df_ct['id'] == x, 'ten_du_an'].values[0]}"
            )

            if st.button(
                "❌ Xác nhận xóa công trình",
                type="primary"
            ):

                cursor.execute(
                    "DELETE FROM cong_trinh_new WHERE id = ?",
                    (ct_del_id,)
                )

                conn.commit()

                st.success(
                    "🎉 Đã xóa công trình!"
                )

                st.rerun()


# ============================================================
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

    arr_danh_muc_den = [
        "Downlight",
        "Led dây",
        "Thanh profile",
        "Bộ nguồn",
        "Đèn nam châm",
        "Đèn trang trí",
        "Khác"
    ]

    arr_hang_den = [
        "Raynice",
        "1962",
        "Wullian",
        "Khác"
    ]

    txt_tim_sp = st.text_input(
        "🔎 Tìm sản phẩm",
        placeholder="Nhập tên hoặc mã code"
    )

    col1, col2 = st.columns(2)

    with col1:

        cbo_danh_muc = st.selectbox(
            "Danh mục",
            ["Tất cả"] + arr_danh_muc_den
        )

    with col2:

        cbo_hang = st.selectbox(
            "Hãng sản xuất",
            ["Tất cả"] + arr_hang_den
        )

    st.markdown("---")

    st.markdown("### ➕ Thêm sản phẩm mới")
    st.caption("Điền thông tin sản phẩm, mô tả và tải ảnh sản phẩm lên hệ thống.")


    new_ma_code = st.text_input(
        "Mã code sản phẩm *"
    )

    new_ten_sp = st.text_input(
        "Tên sản phẩm *"
    )

    col1, col2 = st.columns(2)

    with col1:

        new_danh_muc = st.selectbox(
            "Danh mục",
            arr_danh_muc_den
        )

    with col2:

        new_hang = st.selectbox(
            "Hãng",
            arr_hang_den
        )

    col1, col2 = st.columns(2)

    with col1:

        new_dvt = st.selectbox(
            "Đơn vị tính",
            ["cái", "bộ", "mét"]
        )

    with col2:

        new_gia_ban = st.number_input(
            "Giá bán (VNĐ)",
            min_value=0.0,
            step=1000.0
        )

    new_mo_ta = st.text_area(
        "Mô tả sản phẩm",
        placeholder="Nhập thông số, đặc điểm, công suất, kích thước, màu sắc..."
    )

    new_hinh_anh = st.file_uploader(
        "Hình ảnh sản phẩm",
        type=["png", "jpg", "jpeg", "webp"],
        help="Chọn 1 ảnh PNG/JPG/JPEG/WEBP cho sản phẩm."
    )

    if new_hinh_anh is not None:
        st.image(new_hinh_anh, caption="Ảnh sản phẩm đã chọn", width=220)

    new_ghi_chu = st.text_area(
        "Ghi chú",
        key="product_note"
    )

    if st.button(
        "📦 Nhập sản phẩm vào kho",
        type="primary"
    ):

        if not new_ma_code.strip() or not new_ten_sp.strip():

            st.warning(
                "⚠️ Vui lòng nhập mã và tên sản phẩm."
            )

        else:

            try:

                image_path = save_product_image(
                    new_hinh_anh,
                    new_ma_code.strip()
                )

                cursor.execute("""
                    INSERT INTO san_pham
                    (
                        ma_code,
                        ten_sp,
                        danh_muc,
                        hang,
                        gia_ban,
                        dvt,
                        mo_ta,
                        hinh_anh,
                        ghi_chu
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    new_ma_code.strip().upper(),
                    new_ten_sp.strip(),
                    new_danh_muc,
                    new_hang,
                    new_gia_ban,
                    new_dvt,
                    new_mo_ta.strip(),
                    image_path,
                    new_ghi_chu.strip()
                ))

                conn.commit()

                st.success(
                    "🎉 Đã thêm sản phẩm!"
                )

                st.rerun()

            except sqlite3.IntegrityError:

                st.error(
                    "❌ Mã sản phẩm đã tồn tại!"
                )
    st.markdown("---")

    df_sp_display = df_sp.copy()

    if cbo_danh_muc != "Tất cả":

        df_sp_display = df_sp_display[
            df_sp_display["danh_muc"] == cbo_danh_muc
        ]

    if cbo_hang != "Tất cả":

        df_sp_display = df_sp_display[
            df_sp_display["hang"] == cbo_hang
        ]

    if txt_tim_sp.strip():

        search_val = txt_tim_sp.strip().lower()

        df_sp_display = df_sp_display[
            df_sp_display["ten_sp"]
            .fillna("")
            .str.lower()
            .str.contains(search_val)
            |
            df_sp_display["ma_code"]
            .fillna("")
            .str.lower()
            .str.contains(search_val)
            |
            df_sp_display["mo_ta"]
            .fillna("")
            .str.lower()
            .str.contains(search_val)
        ]

    if not df_sp_display.empty:

        df_display = df_sp_display.copy()

        df_display["GIÁ BÁN"] = (
            df_display["gia_ban"]
            .fillna(0)
            .apply(lambda x: f"{x:,.0f} đ")
        )

        df_display = df_display.rename(columns={
            "ma_code": "Mã code",
            "ten_sp": "Tên sản phẩm",
            "danh_muc": "Danh mục",
            "hang": "Hãng",
            "dvt": "DVT",
            "mo_ta": "Mô tả sản phẩm",
            "ghi_chu": "Ghi chú"
        })

        df_table = df_display.drop(columns=["hinh_anh"], errors="ignore")

        st.dataframe(
            df_table,
            width="stretch",
            hide_index=True
        )

        products_with_images = df_sp_display[
            df_sp_display["hinh_anh"].fillna("").astype(str).str.strip() != ""
        ]

        if not products_with_images.empty:
            st.markdown("### 🖼️ Hình ảnh sản phẩm")
            for _, product in products_with_images.iterrows():
                image_abs_path = os.path.join(BASE_DIR, str(product["hinh_anh"]))
                if os.path.isfile(image_abs_path):
                    col_img, col_info = st.columns([1, 3])
                    with col_img:
                        st.image(image_abs_path, width=200)
                    with col_info:
                        st.markdown(
                            f"**[{product['ma_code']}] {product['ten_sp']}**"
                        )
                        if str(product.get("mo_ta", "")).strip():
                            st.write(str(product["mo_ta"]))
                        st.caption(
                            f"{product['danh_muc']} • {product['hang']} • "
                            f"{float(product['gia_ban'] or 0):,.0f} đ/{product['dvt']}"
                        )
                    st.markdown("---")

    else:

        st.info(
            "Không tìm thấy sản phẩm."
        )

    if not df_sp.empty:

        with st.expander(
            "🗑️ Xóa sản phẩm"
        ):

            sp_del_id = st.selectbox(
                "Chọn sản phẩm",
                df_sp["id"].tolist(),
                format_func=lambda x:
                    f"[{df_sp.loc[df_sp['id'] == x, 'ma_code'].values[0]}] "
                    f"{df_sp.loc[df_sp['id'] == x, 'ten_sp'].values[0]}"
            )

            if st.button(
                "❌ Xóa sản phẩm",
                type="primary"
            ):

                cursor.execute(
                    "DELETE FROM san_pham WHERE id = ?",
                    (sp_del_id,)
                )

                conn.commit()

                st.success(
                    "🎉 Đã xóa sản phẩm!"
                )

                st.rerun()


# ============================================================
# TAB 6 - THÔNG TIN CÔNG TY
# ============================================================

if page == "🏢  Thông tin công ty":
    page_header("Thông tin công ty", "Thông tin doanh nghiệp sử dụng trên báo giá và hồ sơ CRM.")

    st.markdown("## 🏢 Thông tin doanh nghiệp")

    if not df_cty_saved.empty:

        company = df_cty_saved.iloc[0]

        cur_ten = company["ten_cty"]
        cur_tru_so = company["tru_so"]
        cur_vpdd = company["vpdd"]
        cur_sdt = company["sdt"]
        cur_email = company["email"]
        cur_website = company["website"]
        cur_facebook = company["facebook"]

    else:

        cur_ten = ""
        cur_tru_so = ""
        cur_vpdd = ""
        cur_sdt = ""
        cur_email = ""
        cur_website = ""
        cur_facebook = ""

    col1, col2 = st.columns(2)

    with col1:

        ten_cty = st.text_input(
            "Tên công ty / pháp nhân *",
            value=cur_ten
        )

        tru_so = st.text_input(
            "Địa chỉ trụ sở chính",
            value=cur_tru_so
        )

        vpdd = st.text_input(
            "Văn phòng / Showroom",
            value=cur_vpdd
        )

        sdt = st.text_input(
            "Số điện thoại / Hotline",
            value=cur_sdt
        )

    with col2:

        email = st.text_input(
            "Email",
            value=cur_email
        )

        website = st.text_input(
            "Website",
            value=cur_website
        )

        facebook = st.text_input(
            "Facebook / Fanpage",
            value=cur_facebook
        )

    if st.button(
        "💾 Lưu thông tin doanh nghiệp",
        type="primary"
    ):

        if not ten_cty.strip():

            st.warning(
                "⚠️ Vui lòng nhập tên công ty."
            )

        else:

            cursor.execute(
                "DELETE FROM cau_hinh_doanh_nghiep"
            )

            cursor.execute("""
                INSERT INTO cau_hinh_doanh_nghiep
                (
                    id,
                    ten_cty,
                    tru_so,
                    vpdd,
                    sdt,
                    email,
                    website,
                    facebook
                )
                VALUES
                (
                    1,
                    ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                ten_cty.strip(),
                tru_so.strip(),
                vpdd.strip(),
                sdt.strip(),
                email.strip(),
                website.strip(),
                facebook.strip()
            ))

            conn.commit()

            st.success(
                "🎉 Đã lưu thông tin doanh nghiệp!"
            )

            st.rerun()


# ============================================================
# 8. TỰ ĐÓNG DATABASE KHI APP KẾT THÚC
# ============================================================

# Không cần đóng thủ công trong Streamlit vì
# connection được quản lý bằng st.cache_resource.
