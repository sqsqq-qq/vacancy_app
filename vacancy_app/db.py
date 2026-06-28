"""db.py — работа с БД вакансий категории 7 (System Analyst, IT PM, Product Manager IT, Technical Writer)."""

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

DB_PATH = Path(__file__).parent / "vacancies.db"
JSONL_PATH = Path(__file__).parent.parent / "TSSAa_extracted/TSSAa/hh_raw_vacancies.jsonl"

# ---- классификация по названию ----
_RULES = [
    (("системный аналитик", "system analyst"), "System Analyst"),
    (("технический писатель", "technical writer"), "Technical Writer"),
    (("product manager", "продуктовый менеджер"), "Product Manager (IT)"),
    (("it project manager",), "IT Project Manager"),
]

# proverka shodstva vacancyi
def classify_vacancy(name: str) -> str | None:
    n = name.lower()
    if "продакт" in n and "менеджер" in n:
        return "Product Manager (IT)"
    for keywords, prof in _RULES:
        if any(k in n for k in keywords):
            return prof
    if "руководитель" in n and "проект" in n and any(k in n for k in ("it", "ит", "цифр", "digital")):
        return "IT Project Manager"
    if "project manager" in n and any(k in n for k in ("it", "ит", "digital", "программ", "разработ", "tech")):
        return "IT Project Manager"
    return None


# ---- парсинг HTML-описания ----
_SECTION_KW = {
    "responsibilities": ("обязанност", "задач", "функци", "что нужно делать", "чем предстоит"),
    "requirements": ("требован", "что мы ждём", "что ждём", "от вас", "от кандидата", "необходим"),
    "conditions": ("условия", "что предлагаем", "мы предлагаем", "бенефит", "плюс"),
    "skills": ("навык", "знани", "умени", "стек", "технолог", "инструмент"),
}


def parse_description(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    sections = {k: [] for k in _SECTION_KW}

    h3 = soup.find("h3", string=lambda x: x and "Требования" in x)
    if h3 and h3.find_next("ul"):
        sections["requirements"] += [li.get_text(strip=True) for li in h3.find_next("ul").find_all("li")
                                      if li.get_text(strip=True)]

    current = None
    for elem in soup.find_all(["h1", "h2", "h3", "h4", "strong", "li", "p"]):
        t = elem.get_text(strip=True).lower()
        matched = next((sec for sec, kws in _SECTION_KW.items() if any(k in t for k in kws)), None)
        if matched:
            current = matched
        elif elem.name == "li" and current and len(elem.get_text(strip=True)) > 3:
            sections[current].append(elem.get_text(strip=True))

    return {**sections, "raw_text": soup.get_text(separator=" ", strip=True)[:4000]}


# ---- извлечение навыков ----
SKILL_PATTERNS = [
    r"\bSQL\b", r"\bPython\b", r"\bJira\b", r"\bConfluence\b",
    r"\bBPMN\b", r"\bUML\b", r"\bAgile\b", r"\bScrum\b", r"\bKanban\b",
    r"\bREST\b", r"\bAPI\b", r"\bExcel\b", r"\bPowerPoint\b",
    r"\b1С\b", r"\bSAP\b", r"\bNoSQL\b", r"\bGit\b",
    r"\bJavaScript\b", r"\bJava\b", r"\bC#\b", r"\bC\+\+\b",
    r"\bMS Project\b", r"\bPowerBI\b", r"\bTableau\b",
    r"\bFigma\b", r"\bMiro\b", r"\bVisio\b",
]
SKILL_NAMES = [re.sub(r"\\b|\\B|\\\+", "", p).strip("\\") for p in SKILL_PATTERNS]


def extract_skills(text: str) -> list[str]:
    return [name for name, pat in zip(SKILL_NAMES, SKILL_PATTERNS) if re.search(pat, text, re.IGNORECASE)]


# ---- извлечение зарплаты (в исходных данных нет структурированного поля
# salary, поэтому ищем числа рядом со словами "оклад"/"доход"/"зарплата") ----
_SALARY_KW = re.compile(r"(оклад|доход|зарплат|заработная плата|з/?п\b)", re.IGNORECASE)
_SALARY_NUM = re.compile(r"(\d+(?:[\s.,]\d{3})*)")
_SALARY_RANGE = (10_000, 2_000_000)


def extract_salary(text: str) -> tuple[int | None, int | None]:
    """Возвращает (salary_from, salary_to) или (None, None), если зарплата не указана явно."""
    if not text:
        return None, None
    lo, hi = _SALARY_RANGE
    for kw in _SALARY_KW.finditer(text):
        window = text[max(0, kw.start() - 60): kw.end() + 120]
        if "млн" in window.lower():
            continue
        found = []
        for num in _SALARY_NUM.finditer(window):
            value = int(re.sub(r"[\s.,]", "", num.group(0)))
            if "тыс" in window[num.end(): num.end() + 6].lower() and value < lo:
                value *= 1000
            if lo <= value <= hi:
                found.append(value)
            if len(found) >= 2:
                break
        if found:
            return (found[0], found[0]) if len(found) == 1 else (min(found), max(found))
    return None, None


# ---- работа с БД ----
def create_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vacancies (
            id TEXT PRIMARY KEY, url TEXT, name TEXT, employer TEXT, profession TEXT,
            raw_text TEXT, skills_json TEXT, salary_from INTEGER, salary_to INTEGER
        );
        CREATE TABLE IF NOT EXISTS requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, vacancy_id TEXT, section TEXT, item TEXT
        );
    """)
    conn.commit()
    migrate_salary_columns(conn)


def migrate_salary_columns(conn: sqlite3.Connection) -> None:
    """Добивляет salary_from/salary_to к старой БД и заполняет их разбором raw_text. Безопасно для повторных вызовов."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(vacancies)").fetchall()}
    missing = {"salary_from", "salary_to"} - cols
    for col in missing:
        conn.execute(f"ALTER TABLE vacancies ADD COLUMN {col} INTEGER")
    conn.commit()
    if missing:
        for vac_id, raw_text in conn.execute("SELECT id, raw_text FROM vacancies").fetchall():
            s_from, s_to = extract_salary(raw_text)
            if s_from is not None:
                conn.execute("UPDATE vacancies SET salary_from=?, salary_to=? WHERE id=?", (s_from, s_to, vac_id))
        conn.commit()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    migrate_salary_columns(conn)
    return conn


def load_data(jsonl_path: Path = JSONL_PATH, db_path: Path = DB_PATH) -> int:
    """Читает JSONL, фильтрует вакансии категории 7, сохраняет в БД. Не делает ничего, если БД уже заполнена."""
    conn = sqlite3.connect(db_path)
    create_db(conn)
    existing = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]
    if existing > 0:
        conn.close()
        return existing

    inserted = 0
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            if v.get("download_status") != "ok" or not v.get("name"):
                continue
            prof = classify_vacancy(v["name"])
            if not prof:
                continue

            parsed = parse_description(v.get("description", ""))
            skills = extract_skills(parsed["raw_text"])
            salary_from, salary_to = extract_salary(parsed["raw_text"])

            conn.execute(
                """INSERT OR IGNORE INTO vacancies
                   (id, url, name, employer, profession, raw_text, skills_json, salary_from, salary_to)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (v["id"], v.get("url", ""), v["name"], v.get("employer", ""), prof,
                 parsed["raw_text"], json.dumps(skills, ensure_ascii=False), salary_from, salary_to),
            )
            for section in ("responsibilities", "requirements", "conditions", "skills"):
                conn.executemany(
                    "INSERT INTO requirements (vacancy_id, section, item) VALUES (?,?,?)",
                    [(v["id"], section, item) for item in parsed[section]],
                )
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


# ---- запросы ----
def top_counts(values: list[str], n: int = 20) -> list[dict]:
    counter = Counter(v.strip() for v in values if v and v.strip())
    return [{"name": name, "count": count} for name, count in counter.most_common(n)]


def get_stats() -> dict:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]
    by_prof = dict(conn.execute("SELECT profession, COUNT(*) FROM vacancies GROUP BY profession").fetchall())
    employers = conn.execute("SELECT COUNT(DISTINCT employer) FROM vacancies").fetchone()[0]
    conn.close()
    return {"total": total, "by_profession": by_prof, "unique_employers": employers}

#БЕРЕТ 1 РИЛ ВАКАНСИЮ
def get_typical_vacancy(profession: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM vacancies WHERE profession=? ORDER BY ROWID LIMIT 1", (profession,)).fetchone()
    if not row:
        conn.close()
        return None
    v = dict(row)
    for section in ("requirements", "responsibilities", "conditions"):
        v[section] = [r[0] for r in conn.execute(
            "SELECT item FROM requirements WHERE vacancy_id=? AND section=?", (v["id"], section)
        ).fetchall()]
    v["skills"] = json.loads(v.get("skills_json", "[]"))
    conn.close()
    return v

# ТОП НАВЫКИ ТОП ТРЕБОВАНИЙ И ТД
def get_generalized_vacancy(profession: str) -> dict:
    conn = get_conn()

    def items_for(section: str) -> list[str]:
        return [r[0].lower() for r in conn.execute(
            """SELECT item FROM requirements r JOIN vacancies v ON r.vacancy_id = v.id
               WHERE v.profession=? AND r.section=?""", (profession, section)
        ).fetchall() if len(r[0]) > 5]

    result = {sec: [c["name"].capitalize() for c in top_counts(items_for(sec))]
              for sec in ("requirements", "responsibilities", "conditions")}

    all_skills = [s for (sj,) in conn.execute("SELECT skills_json FROM vacancies WHERE profession=?", (profession,))
                  for s in json.loads(sj or "[]")]
    conn.close()

    return {
        "profession": profession,
        **result,
        "top_skills": [{"skill": c["name"], "count": c["count"]} for c in top_counts(all_skills, n=15)],
    }


def get_top_skills_all() -> list[dict]:
    conn = get_conn()
    all_skills = [s for (sj,) in conn.execute("SELECT skills_json FROM vacancies").fetchall()
                  for s in json.loads(sj or "[]")]
    conn.close()
    return [{"skill": c["name"], "count": c["count"]} for c in top_counts(all_skills, n=20)]


def get_top_employers(n: int = 10) -> list[dict]:
    conn = get_conn()
    employers = [e for (e,) in conn.execute(
        "SELECT employer FROM vacancies WHERE employer IS NOT NULL AND employer != ''"
    ).fetchall()]
    conn.close()
    return [{"employer": c["name"], "count": c["count"]} for c in top_counts(employers, n=n)]

#читает косинусное сходство запроса с каждой вакансией
def search_by_skills(
    skills_input: str, top_n: int = 5, salary_min: int | None = None, salary_max: int | None = None,
) -> list[dict]:
    """TF-IDF косинусное сходство навыков пользователя с описаниями вакансий.
    Если задан диапазон зарплаты, оставляет только вакансии с пересекающимся
    диапазоном — вакансии без распознанной зарплаты из выдачи исключаются."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np

    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, employer, profession, url, raw_text, salary_from, salary_to FROM vacancies"
    ).fetchall()
    conn.close()
    if not rows:
        return []

    if salary_min is not None or salary_max is not None:
        lo, hi = salary_min or 0, salary_max if salary_max is not None else float("inf")
        rows = [r for r in rows if r["salary_from"] is not None and r["salary_from"] <= hi and r["salary_to"] >= lo]
        if not rows:
            return []

    vec = TfidfVectorizer(max_features=5000, ngram_range=(1, 2))
    tfidf = vec.fit_transform([r["raw_text"] or "" for r in rows] + [skills_input])
    scores = cosine_similarity(tfidf[-1], tfidf[:-1]).flatten()
    top_idx = np.argsort(scores)[::-1][:top_n]

    return [
        {**{k: rows[i][k] for k in ("id", "name", "employer", "profession", "url", "salary_from", "salary_to")},
         "score": round(float(scores[i]), 4)}
        for i in top_idx
    ]
