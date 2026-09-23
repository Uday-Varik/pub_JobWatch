import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "h1b_sponsors.db"

_is_sponsor_cache: dict[str, bool] = {}
_count_cache: dict[str, int] = {}


def _normalize(name: str) -> str:
    name = name.upper().strip()
    name = name.replace("&", " AND ")
    name = re.sub(r"\b(INC\.?|LLC\.?|CORP\.?|CO\.?|LTD\.?|L\.?P\.?|GROUP|SERVICES?|TECHNOLOGIES?|COMPANY|SYSTEMS?)\b", "", name)
    name = re.sub(r"[.,'\"()/-]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sponsors)")}
    if "normalized_name" not in columns:
        conn.execute("ALTER TABLE sponsors ADD COLUMN normalized_name TEXT")

    rows = conn.execute(
        "SELECT employer_name FROM sponsors WHERE normalized_name IS NULL OR normalized_name = ''"
    ).fetchall()
    if rows:
        conn.executemany(
            "UPDATE sponsors SET normalized_name = ? WHERE employer_name = ?",
            [(_normalize(employer_name), employer_name) for (employer_name,) in rows],
        )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_sponsors_normalized_name ON sponsors(normalized_name)")
    conn.commit()


def _match_conditions(normalized: str) -> tuple[str, list[str]]:
    boundary_params = [
        normalized,
        f"{normalized} %",
        f"% {normalized}",
        f"% {normalized} %",
    ]
    clause = (
        "normalized_name = ? OR normalized_name LIKE ? OR normalized_name LIKE ? OR normalized_name LIKE ?"
    )

    words = normalized.split()
    if len(words) >= 2:
        clause = f"({clause}) OR normalized_name LIKE ?"
        boundary_params.append("%" + "%".join(words) + "%")

    return clause, boundary_params


def is_h1b_sponsor(company_name: str) -> bool:
    if company_name in _is_sponsor_cache:
        return _is_sponsor_cache[company_name]

    if not DB_PATH.exists():
        return False

    normalized = _normalize(company_name)
    clause, params = _match_conditions(normalized)
    conn = _connect()
    cur = conn.cursor()
    cur.execute(f"SELECT employer_name FROM sponsors WHERE {clause} LIMIT 1", params)
    result = cur.fetchone()
    conn.close()
    is_sponsor = result is not None
    _is_sponsor_cache[company_name] = is_sponsor
    return is_sponsor


def get_sponsor_count(company_name: str) -> int:
    if company_name in _count_cache:
        return _count_cache[company_name]

    if not DB_PATH.exists():
        return 0

    normalized = _normalize(company_name)
    clause, params = _match_conditions(normalized)
    conn = _connect()
    cur = conn.cursor()
    cur.execute(f"SELECT COALESCE(SUM(lca_count), 0) FROM sponsors WHERE {clause}", params)
    result = cur.fetchone()
    conn.close()
    count = result[0] or 0
    _count_cache[company_name] = count
    return count
