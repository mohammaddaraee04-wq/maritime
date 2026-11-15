from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "maritime_kb.sqlite3"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

STATUS_LABELS = {
    "draft": "پیش‌نویس",
    "ai_suggested": "پیشنهاد هوش مصنوعی",
    "under_review": "در حال بررسی",
    "human_verified": "تأیید انسانی",
    "rejected": "ردشده",
    "deprecated": "منسوخ",
}
STATUS_COLORS = {
    "draft": "#8a8f98",
    "ai_suggested": "#b7791f",
    "under_review": "#2878a8",
    "human_verified": "#16835d",
    "rejected": "#b42318",
    "deprecated": "#667085",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(value: str | None) -> str:
    """Normalize Persian/Arabic letter variants for reliable lookup."""
    if not value:
        return ""
    text = str(value).strip().casefold()
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    text = text.replace("ۀ", "ه").replace("ة", "ه").replace("ؤ", "و")
    text = text.replace("أ", "ا").replace("إ", "ا").replace("ٱ", "ا")
    text = text.replace("ـ", "")
    for mark in ("َ", "ُ", "ِ", "ّ", "ْ", "ً", "ٌ", "ٍ", "ٰ"):
        text = text.replace(mark, "")
    text = text.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    return " ".join(text.split())


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                english_term TEXT NOT NULL,
                persian_term TEXT NOT NULL,
                synonyms TEXT DEFAULT '',
                definition_fa TEXT DEFAULT '',
                explanation_fa TEXT DEFAULT '',
                discipline TEXT DEFAULT '',
                system_name TEXT DEFAULT '',
                examples_en TEXT DEFAULT '',
                examples_fa TEXT DEFAULT '',
                pronunciation TEXT DEFAULT '',
                ipa TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                organization TEXT DEFAULT '',
                author TEXT DEFAULT '',
                publication_date TEXT DEFAULT '',
                edition TEXT DEFAULT '',
                document_type TEXT DEFAULT '',
                language TEXT DEFAULT '',
                rights_note TEXT DEFAULT '',
                original_filename TEXT DEFAULT '',
                stored_filename TEXT DEFAULT '',
                sha256 TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                UNIQUE(source_id, page_number)
            );

            CREATE TABLE IF NOT EXISTS term_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL,
                note TEXT DEFAULT '',
                UNIQUE(term_id, source_id, page_number)
            );

            CREATE TABLE IF NOT EXISTS term_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
                snapshot_json TEXT NOT NULL,
                revision_at TEXT NOT NULL,
                change_note TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_terms_english ON terms(english_term);
            CREATE INDEX IF NOT EXISTS idx_terms_persian ON terms(persian_term);
            CREATE INDEX IF NOT EXISTS idx_terms_status ON terms(status);
            CREATE INDEX IF NOT EXISTS idx_source_pages_source ON source_pages(source_id, page_number);
            """
        )
        # FTS5 is optional; fall back to substring search when unavailable.
        try:
            conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS terms_fts USING fts5(
                    english_term, persian_term, synonyms, definition_fa,
                    explanation_fa, discipline, system_name,
                    content='terms', content_rowid='id', tokenize='unicode61'
                )"""
            )
            conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS terms_ai AFTER INSERT ON terms BEGIN
                    INSERT INTO terms_fts(rowid, english_term, persian_term, synonyms, definition_fa, explanation_fa, discipline, system_name)
                    VALUES (new.id, new.english_term, new.persian_term, new.synonyms, new.definition_fa, new.explanation_fa, new.discipline, new.system_name);
                END;
                CREATE TRIGGER IF NOT EXISTS terms_ad AFTER DELETE ON terms BEGIN
                    INSERT INTO terms_fts(terms_fts, rowid, english_term, persian_term, synonyms, definition_fa, explanation_fa, discipline, system_name)
                    VALUES ('delete', old.id, old.english_term, old.persian_term, old.synonyms, old.definition_fa, old.explanation_fa, old.discipline, old.system_name);
                END;
                CREATE TRIGGER IF NOT EXISTS terms_au AFTER UPDATE ON terms BEGIN
                    INSERT INTO terms_fts(terms_fts, rowid, english_term, persian_term, synonyms, definition_fa, explanation_fa, discipline, system_name)
                    VALUES ('delete', old.id, old.english_term, old.persian_term, old.synonyms, old.definition_fa, old.explanation_fa, old.discipline, old.system_name);
                    INSERT INTO terms_fts(rowid, english_term, persian_term, synonyms, definition_fa, explanation_fa, discipline, system_name)
                    VALUES (new.id, new.english_term, new.persian_term, new.synonyms, new.definition_fa, new.explanation_fa, new.discipline, new.system_name);
                END;
                """
            )
            initialized = conn.execute("SELECT value FROM app_meta WHERE key='fts_initialized'").fetchone()
            if not initialized:
                conn.execute("INSERT INTO terms_fts(terms_fts) VALUES ('rebuild')")
                conn.execute("INSERT INTO app_meta(key, value) VALUES ('fts_initialized', '1')")
        except sqlite3.OperationalError:
            pass


def add_term(data: dict[str, Any], source_id: int | None = None, page_number: int | None = None, citation_note: str = "") -> int:
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO terms (
                english_term, persian_term, synonyms, definition_fa, explanation_fa,
                discipline, system_name, examples_en, examples_fa, pronunciation,
                ipa, notes, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["english_term"].strip(), data["persian_term"].strip(),
                data.get("synonyms", "").strip(), data.get("definition_fa", "").strip(),
                data.get("explanation_fa", "").strip(), data.get("discipline", "").strip(),
                data.get("system_name", "").strip(), data.get("examples_en", "").strip(),
                data.get("examples_fa", "").strip(), data.get("pronunciation", "").strip(),
                data.get("ipa", "").strip(), data.get("notes", "").strip(),
                data.get("status", "draft"), now, now,
            ),
        )
        term_id = int(cur.lastrowid)
        if source_id is not None and page_number is not None:
            conn.execute(
                "INSERT OR IGNORE INTO term_sources(term_id, source_id, page_number, note) VALUES (?, ?, ?, ?)",
                (term_id, source_id, page_number, citation_note.strip()),
            )
        return term_id


def update_term(term_id: int, data: dict[str, Any], source_id: int | None = None, page_number: int | None = None, citation_note: str = "") -> None:
    now = utc_now()
    with connect() as conn:
        previous = conn.execute("SELECT * FROM terms WHERE id=?", (term_id,)).fetchone()
        if previous is not None:
            conn.execute(
                "INSERT INTO term_revisions(term_id, snapshot_json, revision_at, change_note) VALUES (?, ?, ?, ?)",
                (term_id, json.dumps(dict(previous), ensure_ascii=False), now, "ویرایش از پنل مدیریت"),
            )
        conn.execute(
            """UPDATE terms SET english_term=?, persian_term=?, synonyms=?, definition_fa=?,
            explanation_fa=?, discipline=?, system_name=?, examples_en=?, examples_fa=?,
            pronunciation=?, ipa=?, notes=?, status=?, updated_at=? WHERE id=?""",
            (
                data["english_term"].strip(), data["persian_term"].strip(), data.get("synonyms", "").strip(),
                data.get("definition_fa", "").strip(), data.get("explanation_fa", "").strip(),
                data.get("discipline", "").strip(), data.get("system_name", "").strip(),
                data.get("examples_en", "").strip(), data.get("examples_fa", "").strip(),
                data.get("pronunciation", "").strip(), data.get("ipa", "").strip(),
                data.get("notes", "").strip(), data.get("status", "draft"), now, term_id,
            ),
        )
        if source_id is not None and page_number is not None:
            conn.execute(
                "INSERT OR IGNORE INTO term_sources(term_id, source_id, page_number, note) VALUES (?, ?, ?, ?)",
                (term_id, source_id, page_number, citation_note.strip()),
            )


def get_term(term_id: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM terms WHERE id=?", (term_id,)).fetchone()


def list_terms(limit: int = 5000):
    with connect() as conn:
        return conn.execute("SELECT * FROM terms ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()


def count_rows(table: str) -> int:
    allowed = {"terms", "sources", "source_pages"}
    if table not in allowed:
        raise ValueError("Invalid table")
    with connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def search_terms(query: str, limit: int = 50):
    q = normalize_text(query)
    if not q:
        return []
    like = f"%{q}%"
    with connect() as conn:
        # Hybrid retrieval: FTS5 token matching first, then substring fallback.
        fts_ids: list[int] = []
        try:
            import re
            tokens = re.findall(r"[\w]+", query, flags=re.UNICODE)
            if tokens:
                expression = " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)
                matches = conn.execute(
                    "SELECT rowid FROM terms_fts WHERE terms_fts MATCH ? ORDER BY bm25(terms_fts) LIMIT ?",
                    (expression, limit),
                ).fetchall()
                fts_ids = [int(row[0]) for row in matches]
        except sqlite3.OperationalError:
            pass

        rows_by_id = {}
        if fts_ids:
            placeholders = ",".join("?" for _ in fts_ids)
            found = conn.execute(f"SELECT * FROM terms WHERE id IN ({placeholders})", fts_ids).fetchall()
            rows_by_id = {int(row["id"]): row for row in found}

        # Normalize common Arabic/Persian variants on the database side too.
        like_rows = conn.execute(
            """SELECT * FROM terms
            WHERE lower(english_term) LIKE lower(?)
               OR lower(replace(replace(persian_term,'ي','ی'),'ك','ک')) LIKE lower(?)
               OR lower(replace(replace(synonyms,'ي','ی'),'ك','ک')) LIKE lower(?)
               OR lower(replace(replace(definition_fa,'ي','ی'),'ك','ک')) LIKE lower(?)
               OR lower(replace(replace(explanation_fa,'ي','ی'),'ك','ک')) LIKE lower(?)
               OR lower(discipline) LIKE lower(?)
               OR lower(system_name) LIKE lower(?)
            ORDER BY CASE
                WHEN lower(english_term)=lower(?) OR lower(replace(replace(persian_term,'ي','ی'),'ك','ک'))=lower(?) THEN 0
                WHEN lower(english_term) LIKE lower(?) OR lower(replace(replace(persian_term,'ي','ی'),'ك','ک')) LIKE lower(?) THEN 1
                ELSE 2 END,
                CASE status WHEN 'human_verified' THEN 0 WHEN 'under_review' THEN 1 ELSE 2 END,
                english_term COLLATE NOCASE
            LIMIT ?""",
            (like, like, like, like, like, like, like, q, q, f"{q}%", f"{q}%", limit),
        ).fetchall()
        for row in like_rows:
            rows_by_id.setdefault(int(row["id"]), row)

        # Exact and prefix hits are promoted; the remaining order follows FTS/LIKE retrieval.
        result = list(rows_by_id.values())
        result.sort(key=lambda row: (
            0 if normalize_text(row["english_term"]) == q or normalize_text(row["persian_term"]) == q else
            1 if normalize_text(row["english_term"]).startswith(q) or normalize_text(row["persian_term"]).startswith(q) else 2,
            0 if row["status"] == "human_verified" else 1,
            row["english_term"].casefold(),
        ))
        return result[:limit]


def get_sources():
    with connect() as conn:
        return conn.execute("SELECT * FROM sources ORDER BY created_at DESC").fetchall()


def get_source(source_id: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()


def get_source_pages(source_id: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM source_pages WHERE source_id=? ORDER BY page_number", (source_id,)).fetchall()


def get_term_citations(term_id: int):
    with connect() as conn:
        return conn.execute(
            """SELECT ts.*, s.title, s.organization, s.edition, s.publication_date, sp.content
            FROM term_sources ts JOIN sources s ON s.id=ts.source_id
            LEFT JOIN source_pages sp ON sp.source_id=ts.source_id AND sp.page_number=ts.page_number
            WHERE ts.term_id=? ORDER BY s.title, ts.page_number""",
            (term_id,),
        ).fetchall()


def add_source(metadata: dict[str, str], original_filename: str, file_bytes: bytes, pages: list[str]) -> tuple[int, bool]:
    digest = hashlib.sha256(file_bytes).hexdigest()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM sources WHERE sha256=?", (digest,)).fetchone()
        if existing:
            return int(existing[0]), False
        suffix = Path(original_filename).suffix.lower()
        stored_name = f"{digest[:16]}{suffix}"
        target = UPLOAD_DIR / stored_name
        target.write_bytes(file_bytes)
        cur = conn.execute(
            """INSERT INTO sources(title, organization, author, publication_date, edition,
            document_type, language, rights_note, original_filename, stored_filename, sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                metadata.get("title", "").strip() or original_filename,
                metadata.get("organization", "").strip(), metadata.get("author", "").strip(),
                metadata.get("publication_date", "").strip(), metadata.get("edition", "").strip(),
                metadata.get("document_type", "").strip(), metadata.get("language", "").strip(),
                metadata.get("rights_note", "").strip(), original_filename, stored_name, digest, utc_now(),
            ),
        )
        source_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO source_pages(source_id, page_number, content) VALUES (?, ?, ?)",
            [(source_id, i + 1, text) for i, text in enumerate(pages)],
        )
        return source_id, True


def import_terms_csv(file_bytes: bytes) -> tuple[int, list[str]]:
    errors: list[str] = []
    imported = 0
    decoded = None
    for encoding in ("utf-8-sig", "utf-8", "cp1256"):
        try:
            decoded = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        return 0, ["فایل CSV با کدگذاری UTF-8 یا فارسی قابل خواندن نیست."]
    reader = csv.DictReader(io.StringIO(decoded))
    required = {"english_term", "persian_term"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        return 0, ["ستون‌های ضروری english_term و persian_term در فایل وجود ندارند."]
    for line_no, row in enumerate(reader, start=2):
        english = (row.get("english_term") or "").strip()
        persian = (row.get("persian_term") or "").strip()
        if not english or not persian:
            errors.append(f"سطر {line_no}: اصطلاح انگلیسی یا معادل فارسی خالی است.")
            continue
        duplicate = False
        with connect() as conn:
            duplicate = conn.execute(
                "SELECT 1 FROM terms WHERE lower(english_term)=lower(?) AND persian_term=? LIMIT 1",
                (english, persian),
            ).fetchone() is not None
        if duplicate:
            errors.append(f"سطر {line_no}: این ترکیب اصطلاح و ترجمه قبلاً ثبت شده است.")
            continue
        add_term({
            "english_term": english,
            "persian_term": persian,
            "synonyms": row.get("synonyms", "") or "",
            "definition_fa": row.get("definition_fa", "") or "",
            "explanation_fa": row.get("explanation_fa", "") or "",
            "discipline": row.get("discipline", "") or "",
            "system_name": row.get("system_name", "") or "",
            "examples_en": row.get("examples_en", "") or "",
            "examples_fa": row.get("examples_fa", "") or "",
            "pronunciation": row.get("pronunciation", "") or "",
            "ipa": row.get("ipa", "") or "",
            "notes": row.get("notes", "") or "",
            # Imported records are always drafts. CSV cannot self-certify terms.
            "status": "draft",
        })
        imported += 1
    return imported, errors


def export_terms_csv() -> bytes:
    fields = ["id", "english_term", "persian_term", "synonyms", "definition_fa", "explanation_fa", "discipline", "system_name", "examples_en", "examples_fa", "pronunciation", "ipa", "notes", "status"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in list_terms(limit=100000):
        writer.writerow({field: row[field] for field in fields})
    return output.getvalue().encode("utf-8-sig")


def get_term_revisions(term_id: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM term_revisions WHERE term_id=? ORDER BY revision_at DESC", (term_id,)).fetchall()


def source_page_has_text(source_id: int, page_number: int) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT content FROM source_pages WHERE source_id=? AND page_number=?",
            (source_id, page_number),
        ).fetchone()
    return bool(row and str(row[0]).strip())
