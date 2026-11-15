from __future__ import annotations

import csv
import io
import os
from datetime import datetime
from pathlib import Path

import streamlit as st
from pypdf import PdfReader

import db

st.set_page_config(
    page_title="Maritime Knowledge Base",
    page_icon="⚓",
    layout="wide",
    initial_sidebar_state="expanded",
)

db.init_db()

# Public web preview is deliberately read-only. Community Cloud does not
# guarantee persistence for files written to the app container.
DEPLOYMENT_MODE = os.getenv("MARITIME_DEPLOYMENT_MODE", "local").strip().lower()
PUBLIC_DEMO = DEPLOYMENT_MODE == "demo"
if PUBLIC_DEMO and db.count_rows("terms") == 0:
    sample_path = Path(__file__).with_name("sample_terms.csv")
    if sample_path.exists():
        db.import_terms_csv(sample_path.read_bytes())

st.markdown(
    """
    <style>
    html, body, [class*="css"], [data-testid="stAppViewContainer"] {
        font-family: Tahoma, 'Segoe UI', sans-serif;
    }
    .block-container {max-width: 1240px; padding-top: 1.3rem; padding-bottom: 3rem;}
    [data-testid="stSidebar"] {background: #102B43;}
    [data-testid="stSidebar"] * {color: #F4F8FB !important;}
    .hero {
        background: linear-gradient(115deg, #102B43 0%, #174B68 70%, #147CA6 100%);
        border-radius: 18px; padding: 28px 30px; color: white; margin-bottom: 18px;
    }
    .hero h1 {color:white; font-size: 2rem; margin: 0 0 7px 0;}
    .hero p {color:#D6E6EE; margin:0; font-size: 0.98rem;}
    .metric-card {background:white; border:1px solid #DCE5EB; border-radius:14px; padding:16px 18px; min-height:92px;}
    .metric-label {font-size:0.85rem; color:#667785; margin-bottom:7px;}
    .metric-value {font-size:1.7rem; font-weight:800; color:#102B43;}
    .term-card {background:white; border:1px solid #DCE5EB; border-right:4px solid #147CA6; border-radius:12px; padding:16px 18px; margin:8px 0 12px 0;}
    .term-en {font-size:1.25rem; font-weight:800; color:#12344B; direction:ltr; text-align:left;}
    .term-fa {font-size:1.12rem; font-weight:700; color:#176B8D; margin-top:4px;}
    .muted {color:#667785; font-size:0.84rem;}
    .small-note {background:#FFF8E8; border:1px solid #F3D79A; border-radius:10px; padding:12px 15px; color:#614B1A; font-size:0.9rem;}
    div[data-testid="stForm"] {background:#FFFFFF; border:1px solid #DCE5EB; padding:18px; border-radius:14px;}
    .stButton>button, .stDownloadButton>button {border-radius:9px; font-weight:600;}
    div[data-testid="stMetric"] {background:white; border:1px solid #DCE5EB; border-radius:12px; padding:12px;}
    </style>
    """,
    unsafe_allow_html=True,
)


def status_badge(status: str) -> str:
    label = db.STATUS_LABELS.get(status, status)
    color = db.STATUS_COLORS.get(status, "#667085")
    return f'<span style="display:inline-block;background:{color}18;color:{color};border:1px solid {color}55;border-radius:20px;padding:3px 10px;font-size:0.78rem;font-weight:700">{label}</span>'


def show_term(term):
    citations = db.get_term_citations(int(term["id"]))
    st.markdown('<div class="term-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="term-en">{_esc(term["english_term"])}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="term-fa">{_esc(term["persian_term"])}</div>', unsafe_allow_html=True)
    left, right = st.columns([4, 1])
    with left:
        meta = " · ".join(x for x in [term["discipline"], term["system_name"]] if x)
        if meta:
            st.caption(meta)
    with right:
        st.markdown(status_badge(term["status"]), unsafe_allow_html=True)
    if term["definition_fa"]:
        st.markdown("**تعریف تخصصی**")
        st.write(term["definition_fa"])
    if term["explanation_fa"]:
        st.markdown("**توضیح کاربردی**")
        st.write(term["explanation_fa"])
    if term["synonyms"]:
        st.markdown("**مترادف‌ها و صورت‌های جایگزین:** " + term["synonyms"])
    if term["pronunciation"] or term["ipa"]:
        st.markdown("**تلفظ:** " + " · ".join(x for x in [term["pronunciation"], term["ipa"]] if x))
    if term["examples_en"] or term["examples_fa"]:
        with st.expander("مثال کاربردی"):
            if term["examples_en"]:
                st.markdown(f"**English:** {_esc(term['examples_en'])}")
            if term["examples_fa"]:
                st.write(term["examples_fa"])
    if citations:
        with st.expander(f"منابع و ارجاعات ({len(citations)})"):
            for cite in citations:
                st.markdown(f"**{_esc(cite['title'])}** — صفحه {cite['page_number']}")
                source_meta = " · ".join(x for x in [cite["organization"], cite["edition"], cite["publication_date"]] if x)
                if source_meta:
                    st.caption(source_meta)
                if cite["note"]:
                    st.write(cite["note"])
                excerpt = (cite["content"] or "").strip()
                if excerpt:
                    st.text(excerpt[:1800] + ("…" if len(excerpt) > 1800 else ""))
    else:
        st.markdown('<div class="small-note">برای این مدخل هنوز ارجاع صفحه‌دار ثبت نشده است. تا زمان بررسی منبع، این اطلاعات را مرجع تأییدشده تلقی نکنید.</div>', unsafe_allow_html=True)
    if term["notes"]:
        with st.expander("یادداشت و محدودیت‌ها"):
            st.write(term["notes"])
    st.markdown('</div>', unsafe_allow_html=True)


def _esc(value) -> str:
    text = str(value or "")
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#x27;"))


def source_options():
    sources = db.get_sources()
    return sources, {f"{s['title']} (شناسه {s['id']})": int(s["id"]) for s in sources}


def page_options(source_id: int):
    pages = db.get_source_pages(source_id)
    return [int(p["page_number"]) for p in pages] or [1]


with st.sidebar:
    st.markdown("<div style='font-size:1.4rem;font-weight:800;margin:8px 0 2px'>⚓ Maritime KB</div>", unsafe_allow_html=True)
    st.caption("مرجع تخصصی واژگان و دانش دریایی")
    st.divider()
    menu_items = ["خانه", "جست‌وجوی واژگان", "درباره و پشتیبان‌گیری"] if PUBLIC_DEMO else [
        "خانه", "جست‌وجوی واژگان", "مدیریت واژگان", "بانک منابع", "درباره و پشتیبان‌گیری"
    ]
    page = st.radio("منوی اصلی", menu_items, label_visibility="collapsed")
    st.divider()
    st.caption("نسخه آزمایشی تحت وب · فقط مشاهده" if PUBLIC_DEMO else "نسخه توسعه · اجرای محلی")

if page == "خانه":
    st.markdown(
        "<div class='hero'><h1>Maritime Knowledge Base</h1><p>مرجع تخصصی واژگان، تجهیزات و دانش صنعت دریانوردی</p></div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("مدخل‌های واژگان", db.count_rows("terms"))
    with c2:
        st.metric("منابع ثبت‌شده", db.count_rows("sources"))
    with c3:
        st.metric("صفحات استخراج‌شده", db.count_rows("source_pages"))
    st.markdown("### جست‌وجوی سریع")
    q = st.text_input("اصطلاح انگلیسی یا فارسی را بنویسید", placeholder="مثلاً Draft یا آبخور کشتی", key="home_search")
    if q.strip():
        results = db.search_terms(q)
        if results:
            st.success(f"{len(results)} نتیجه پیدا شد")
            for row in results[:10]:
                show_term(row)
        else:
            st.info("نتیجه‌ای در بانک واژگان فعلی پیدا نشد. می‌توانید مدخل را از بخش مدیریت واژگان اضافه کنید.")
    st.markdown("### بخش‌های اصلی")
    a, b = st.columns(2)
    with a:
        st.info("**فرهنگ لغت تخصصی**\n\nثبت و جست‌وجوی انگلیسی به فارسی و فارسی به انگلیسی؛ همراه با وضعیت اعتبار.")
        st.info("**بانک منابع**\n\nثبت کتاب‌ها و اسناد، استخراج متن PDF و نگهداری شماره صفحات.")
    with b:
        st.info("**مدیریت اصطلاحات**\n\nافزودن، اصلاح، واردکردن CSV و ثبت ارجاعات صفحه‌دار.")
        st.warning("**هوش مصنوعی منبع‌محور**\n\nدر مرحله بعد اضافه می‌شود؛ فعلاً برنامه هیچ پاسخ ساختگی یا بدون منبع تولید نمی‌کند.")
    st.markdown("<div class='small-note'>این نسخه موتور جست‌وجو و بانک اطلاعاتی عملیاتی دارد. داده‌های نمونه یا مدخل‌های بدون ارجاع، تأییدشده محسوب نمی‌شوند.</div>", unsafe_allow_html=True)

elif page == "جست‌وجوی واژگان":
    st.title("فرهنگ لغت تخصصی دریایی")
    st.caption("جست‌وجو در انگلیسی، فارسی، مترادف‌ها و توضیحات ثبت‌شده")
    query = st.text_input("عبارت موردنظر", placeholder="مثلاً Propeller Shaft / محور پروانه")
    status_filter = st.multiselect("فیلتر وضعیت", options=list(db.STATUS_LABELS.keys()), format_func=lambda x: db.STATUS_LABELS[x])
    if query.strip():
        results = db.search_terms(query, limit=100)
        if status_filter:
            results = [r for r in results if r["status"] in status_filter]
        if results:
            st.success(f"{len(results)} نتیجه")
            for row in results:
                show_term(row)
        else:
            st.warning("نتیجه‌ای پیدا نشد. املای واژه را بررسی کنید یا مدخل جدیدی ثبت کنید.")
    else:
        st.info("برای شروع، یک اصطلاح فارسی یا انگلیسی وارد کنید.")

elif page == "مدیریت واژگان":
    st.title("مدیریت واژگان")
    st.caption("مدخل‌های جدید به‌صورت پیش‌نویس ثبت می‌شوند؛ ورود از CSV نیز هرگز وضعیت تأیید انسانی ایجاد نمی‌کند.")
    tab_add, tab_edit, tab_import = st.tabs(["افزودن مدخل", "ویرایش مدخل", "ورود و خروجی CSV"])

    sources, src_map = source_options()
    with tab_add:
        with st.form("add_term_form", clear_on_submit=True):
            st.markdown("#### اطلاعات اصطلاح")
            col1, col2 = st.columns(2)
            with col1:
                en = st.text_input("اصطلاح انگلیسی *")
                fa = st.text_input("معادل تخصصی فارسی *")
                synonyms = st.text_input("مترادف‌ها / صورت‌های جایگزین", help="با ویرگول جدا کنید")
                pronunciation = st.text_input("راهنمای تلفظ")
                ipa = st.text_input("IPA")
            with col2:
                discipline = st.text_input("حوزه تخصصی", placeholder="Marine Engineering")
                system_name = st.text_input("سیستم یا بخش کشتی", placeholder="Propulsion System")
                status_choice = st.selectbox("وضعیت مدخل", ["draft", "under_review", "ai_suggested", "human_verified"], format_func=lambda x: db.STATUS_LABELS[x])
                verified_ack = st.checkbox("تأیید می‌کنم ترجمه را شخصاً با منبع تخصصی بررسی کرده‌ام")
            definition = st.text_area("تعریف تخصصی فارسی", height=100)
            explanation = st.text_area("توضیح کاربردی و ساده", height=85)
            ex1, ex2 = st.columns(2)
            with ex1:
                example_en = st.text_area("مثال انگلیسی", height=75)
            with ex2:
                example_fa = st.text_area("ترجمه مثال", height=75)
            notes = st.text_area("یادداشت، محدوده کاربرد یا ابهام", height=70)
            st.markdown("#### ارجاع منبع (برای اعتبارسنجی ضروری)")
            source_label = st.selectbox("منبع", ["بدون منبع"] + list(src_map.keys()))
            source_id = src_map.get(source_label)
            page_num = None
            if source_id:
                page_num = st.selectbox("شماره صفحه", page_options(source_id))
            citation_note = st.text_input("یادداشت ارجاع", placeholder="مثلاً تعریف اصطلاح در بخش 2.3")
            submitted = st.form_submit_button("ثبت مدخل", type="primary", use_container_width=True)
        if submitted:
            if not en.strip() or not fa.strip():
                st.error("اصطلاح انگلیسی و معادل فارسی الزامی است.")
            elif status_choice == "human_verified" and (not verified_ack or not source_id or page_num is None):
                st.error("برای ثبت به‌عنوان تأیید انسانی، باید تأیید شخصی را علامت بزنید و منبع و شماره صفحه انتخاب کنید.")
            elif status_choice == "human_verified" and not db.source_page_has_text(source_id, int(page_num)):
                st.error("صفحه انتخاب‌شده متن قابل استخراج ندارد؛ برای تأیید انسانی یک صفحه دارای متن یا منبع متنی معتبر انتخاب کنید.")
            else:
                term_id = db.add_term({
                    "english_term": en, "persian_term": fa, "synonyms": synonyms,
                    "definition_fa": definition, "explanation_fa": explanation,
                    "discipline": discipline, "system_name": system_name,
                    "examples_en": example_en, "examples_fa": example_fa,
                    "pronunciation": pronunciation, "ipa": ipa, "notes": notes,
                    "status": status_choice,
                }, source_id, page_num, citation_note)
                st.success(f"مدخل با شناسه {term_id} ثبت شد.")
                st.rerun()

    with tab_edit:
        all_terms = db.list_terms()
        if not all_terms:
            st.info("هنوز مدخلی ثبت نشده است.")
        else:
            term_map = {f"{t['english_term']} — {t['persian_term']} (#{t['id']})": int(t["id"]) for t in all_terms}
            selected_label = st.selectbox("مدخل موردنظر برای ویرایش", list(term_map.keys()))
            term = db.get_term(term_map[selected_label])
            with st.form("edit_term_form"):
                col1, col2 = st.columns(2)
                with col1:
                    e_en = st.text_input("اصطلاح انگلیسی", value=term["english_term"])
                    e_fa = st.text_input("معادل فارسی", value=term["persian_term"])
                    e_syn = st.text_input("مترادف‌ها", value=term["synonyms"])
                    e_pron = st.text_input("راهنمای تلفظ", value=term["pronunciation"])
                    e_ipa = st.text_input("IPA", value=term["ipa"])
                with col2:
                    e_disc = st.text_input("حوزه تخصصی", value=term["discipline"])
                    e_system = st.text_input("سیستم", value=term["system_name"])
                    status_keys = list(db.STATUS_LABELS.keys())
                    e_status = st.selectbox("وضعیت", status_keys, index=status_keys.index(term["status"]), format_func=lambda x: db.STATUS_LABELS[x])
                    e_ack = st.checkbox("تأیید می‌کنم تغییرات و وضعیت اعتبار را شخصاً بررسی کرده‌ام")
                e_def = st.text_area("تعریف تخصصی", value=term["definition_fa"], height=95)
                e_exp = st.text_area("توضیح کاربردی", value=term["explanation_fa"], height=80)
                e_ex1, e_ex2 = st.columns(2)
                with e_ex1:
                    e_example_en = st.text_area("مثال انگلیسی", value=term["examples_en"], height=70)
                with e_ex2:
                    e_example_fa = st.text_area("ترجمه مثال", value=term["examples_fa"], height=70)
                e_notes = st.text_area("یادداشت", value=term["notes"], height=65)
                attach_source = st.checkbox("یک ارجاع منبع جدید اضافه کن")
                e_source_id = None
                e_page_num = None
                e_citation_note = ""
                if attach_source and src_map:
                    e_source_label = st.selectbox("منبع جدید", list(src_map.keys()))
                    e_source_id = src_map[e_source_label]
                    e_page_num = st.selectbox("صفحه منبع", page_options(e_source_id))
                    e_citation_note = st.text_input("یادداشت ارجاع جدید")
                e_submit = st.form_submit_button("ذخیره تغییرات", type="primary", use_container_width=True)
            if e_submit:
                if not e_en.strip() or not e_fa.strip():
                    st.error("اصطلاح انگلیسی و معادل فارسی الزامی است.")
                elif e_status == "human_verified" and not e_ack:
                    st.error("برای انتخاب تأیید انسانی، تأیید شخصی را علامت بزنید.")
                else:
                    # A verified record must have at least one page-level citation.
                    old_cites = db.get_term_citations(int(term["id"]))
                    has_usable_old_citation = any(
                        db.source_page_has_text(int(cite["source_id"]), int(cite["page_number"]))
                        for cite in old_cites
                    )
                    has_usable_new_citation = bool(
                        e_source_id and e_page_num and db.source_page_has_text(e_source_id, int(e_page_num))
                    )
                    if e_status == "human_verified" and not (has_usable_old_citation or has_usable_new_citation):
                        st.error("برای تأیید انسانی، حداقل یک ارجاع به صفحه‌ای با متن قابل استخراج لازم است.")
                    else:
                        db.update_term(int(term["id"]), {
                            "english_term": e_en, "persian_term": e_fa, "synonyms": e_syn,
                            "definition_fa": e_def, "explanation_fa": e_exp,
                            "discipline": e_disc, "system_name": e_system,
                            "examples_en": e_example_en, "examples_fa": e_example_fa,
                            "pronunciation": e_pron, "ipa": e_ipa, "notes": e_notes,
                            "status": e_status,
                        }, e_source_id, e_page_num, e_citation_note)
                        st.success("تغییرات ذخیره شد و نسخه قبلی در تاریخچه نگهداری شد.")
                        revisions = db.get_term_revisions(int(term["id"]))
                        if revisions:
                            with st.expander("تاریخچه ویرایش‌های قبلی"):
                                for rev in revisions[:5]:
                                    st.caption(rev["revision_at"])
                                    st.json(rev["snapshot_json"])

    with tab_import:
        st.markdown("#### ورود گروهی اصطلاحات از CSV")
        st.write("فایل CSV باید ستون‌های `english_term` و `persian_term` را داشته باشد. سایر ستون‌ها اختیاری‌اند.")
        template = Path(__file__).with_name("sample_terms.csv").read_bytes()
        st.download_button("دانلود فایل نمونه CSV", template, file_name="maritime_terms_template.csv", mime="text/csv")
        upload_csv = st.file_uploader("انتخاب فایل CSV", type=["csv"], key="terms_csv")
        if upload_csv is not None and st.button("وارد کردن واژگان", type="primary"):
            count, errors = db.import_terms_csv(upload_csv.getvalue())
            st.success(f"{count} مدخل وارد شد؛ همه با وضعیت پیش‌نویس.")
            if errors:
                with st.expander(f"گزارش موارد واردنشده ({len(errors)})"):
                    for error in errors:
                        st.write("- " + error)
        st.divider()
        st.download_button("خروجی پشتیبان واژگان (CSV)", db.export_terms_csv(), file_name=f"maritime_terms_backup_{datetime.now():%Y%m%d}.csv", mime="text/csv")

elif page == "بانک منابع":
    st.title("بانک منابع تخصصی")
    st.caption("ثبت اسناد، استخراج متن صفحه‌به‌صفحه و اتصال اصطلاحات به منبع")
    with st.expander("افزودن منبع جدید", expanded=True):
        file = st.file_uploader("فایل منبع (PDF متنی یا TXT)", type=["pdf", "txt"], key="source_file")
        with st.form("source_metadata"):
            title = st.text_input("عنوان سند *")
            organization = st.text_input("سازمان ناشر / سازنده", placeholder="مثلاً IMO یا نام سازنده")
            author = st.text_input("نویسنده")
            publication_date = st.text_input("تاریخ انتشار")
            edition = st.text_input("ویرایش / نسخه")
            doc_type = st.selectbox("نوع سند", ["کتاب", "استاندارد", "مقررات", "راهنمای سازنده", "نقشه فنی", "مقاله", "سایر"])
            language = st.selectbox("زبان", ["English", "فارسی", "دو زبانه", "سایر"])
            rights_note = st.text_input("مجوز و محدودیت استفاده", placeholder="مثلاً فقط استفاده داخلی؛ بدون اجازه بازنشر")
            source_submit = st.form_submit_button("پردازش و ثبت منبع", type="primary", use_container_width=True)
        if source_submit:
            if file is None:
                st.error("ابتدا فایل PDF یا TXT را انتخاب کنید.")
            elif not title.strip():
                st.error("عنوان سند الزامی است.")
            else:
                raw = file.getvalue()
                pages = []
                try:
                    if file.name.lower().endswith(".pdf"):
                        reader = PdfReader(io.BytesIO(raw))
                        if reader.is_encrypted:
                            st.error("این PDF رمزگذاری شده است. ابتدا نسخه مجاز و بدون رمز آن را تهیه کنید.")
                            st.stop()
                        pages = [(p.extract_text() or "").strip() for p in reader.pages]
                    else:
                        decoded = raw.decode("utf-8-sig", errors="replace")
                        pages = [part.strip() for part in decoded.split("\f") if part.strip()] or [decoded.strip()]
                    source_id, created = db.add_source({
                        "title": title, "organization": organization, "author": author,
                        "publication_date": publication_date, "edition": edition,
                        "document_type": doc_type, "language": language, "rights_note": rights_note,
                    }, file.name, raw, pages)
                    if created:
                        st.success(f"منبع با شناسه {source_id} ثبت شد؛ {len(pages)} صفحه پردازش شد.")
                        extracted = sum(1 for p in pages if p.strip())
                        if extracted == 0:
                            st.warning("متنی از فایل استخراج نشد. احتمالاً PDF اسکن‌شده یا تصویری است؛ OCR در نسخه بعدی اضافه می‌شود.")
                        elif extracted < len(pages):
                            st.warning(f"از {len(pages)} صفحه، فقط {extracted} صفحه متن قابل استخراج داشتند.")
                    else:
                        st.warning(f"این فایل قبلاً ثبت شده است (شناسه منبع {source_id}). از ثبت تکراری جلوگیری شد.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"پردازش فایل ناموفق بود: {exc}")
    st.markdown("### منابع ثبت‌شده")
    sources = db.get_sources()
    if not sources:
        st.info("هنوز منبعی ثبت نشده است.")
    for source in sources:
        pages = db.get_source_pages(int(source["id"]))
        extracted = sum(1 for p in pages if p["content"].strip())
        with st.expander(f"{source['title']} · شناسه {source['id']} · {len(pages)} صفحه"):
            meta = " | ".join(x for x in [source["organization"], source["edition"], source["publication_date"], source["document_type"]] if x)
            st.write(meta or "مشخصات تکمیلی ثبت نشده")
            st.caption(f"فایل: {source['original_filename']} · صفحات دارای متن: {extracted}/{len(pages)}")
            if source["rights_note"]:
                st.warning("محدودیت مجوز: " + source["rights_note"])
            if pages:
                pnums = [int(p["page_number"]) for p in pages]
                chosen = st.selectbox("نمایش صفحه", pnums, key=f"page_{source['id']}")
                selected = next(p for p in pages if int(p["page_number"]) == chosen)
                st.text_area("متن استخراج‌شده", selected["content"] or "متنی استخراج نشد.", height=180, key=f"content_{source['id']}_{chosen}")

elif page == "درباره و پشتیبان‌گیری":
    st.title("درباره پروژه")
    if PUBLIC_DEMO:
        st.markdown("### پیش‌نمایش عمومی تحت وب")
        st.success("نسخه نمایشی 0.2.0 — جست‌وجوی دو‌زبانه در مدخل‌های نمونه فعال است.")
        st.warning("این نسخه برای ارزیابی ظاهر و تجربه جست‌وجو است؛ بانک اطلاعاتی پایدار، پنل مدیریت عمومی، بارگذاری منابع و هوش مصنوعی هنوز به زیرساخت جداگانه نیاز دارند.")
        st.markdown("### اصول اعتبار علمی")
        st.markdown("- همه مدخل‌های نمونه با وضعیت پیش‌نویس نمایش داده می‌شوند.\n- هیچ ترجمه‌ای صرفاً به دلیل نمایش در سایت تأییدشده تلقی نمی‌شود.\n- برای استفاده تخصصی، منبع معتبر و بررسی انسانی لازم است.")
    else:
        st.markdown("### وضعیت نسخه فعلی")
        st.success("نسخه 0.1.0 — جست‌وجوی واژگان، بانک SQLite، ورود CSV، ثبت PDF/TXT و ارجاعات صفحه‌دار فعال است.")
        st.warning("هوش مصنوعی مولد، جست‌وجوی برداری، OCR اسناد اسکن‌شده، صوت، نقشه تعاملی و همگام‌سازی آنلاین هنوز در این نسخه پیاده‌سازی نشده‌اند.")
        st.markdown("### پشتیبان‌گیری")
        if db.DB_PATH.exists():
            st.download_button("دریافت نسخه پشتیبان پایگاه داده", db.DB_PATH.read_bytes(), file_name=f"maritime_kb_backup_{datetime.now():%Y%m%d_%H%M}.sqlite3", mime="application/vnd.sqlite3")
        st.download_button("دریافت خروجی واژگان CSV", db.export_terms_csv(), file_name="maritime_terms_backup.csv", mime="text/csv")
        st.markdown("### نکات استفاده")
        st.markdown("""
        - هر مدخل جدید ابتدا **پیش‌نویس** است.
        - برای وضعیت **تأیید انسانی**، بررسی انسانی و ثبت منبع و شماره صفحه لازم است.
        - ورود گروهی CSV همیشه مدخل‌ها را پیش‌نویس نگه می‌دارد.
        - فایل‌های منبع در پوشه `data/uploads` ذخیره می‌شوند؛ در پشتیبان‌گیری کامل، این پوشه را هم نگهداری کنید.
        - متن PDFهای اسکن‌شده در این نسخه استخراج نمی‌شود.
        - فایل‌های دارای حق نشر را فقط مطابق مجوزشان وارد و استفاده کنید.
        """)
        st.markdown("### مسیر بانک اطلاعاتی")
        st.code(str(db.DB_PATH), language="text")

st.markdown("<div style='text-align:center;color:#83919B;font-size:0.78rem;margin-top:34px'>Maritime Knowledge Base · نسخه اولیه · داده‌های تأییدنشده نباید مرجع عملیاتی تلقی شوند</div>", unsafe_allow_html=True)
