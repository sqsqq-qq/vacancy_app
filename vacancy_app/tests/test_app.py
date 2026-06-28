"""tests/test_app.py — минимальный набор тестов приложения анализа вакансий. Запуск: pytest tests/ -v"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import (
    classify_vacancy, create_db, extract_salary, extract_skills, get_generalized_vacancy,
    get_stats, get_top_employers, get_top_skills_all, get_typical_vacancy, parse_description,
    search_by_skills,
)

VACANCIES = [
    ("1", "https://hh.ru/1", "Системный аналитик", "Рога и Копыта", "System Analyst",
     "Знание SQL, Python, UML, BPMN.", '["SQL", "Python", "UML"]', 150000, 200000),
    ("2", "https://hh.ru/2", "IT Project Manager", "TechCorp", "IT Project Manager",
     "Управление командой, Jira, Agile, Scrum.", '["Jira", "Agile", "Scrum"]', 200000, 250000),
    ("3", "https://hh.ru/3", "Product Manager (IT)", "StartupXYZ", "Product Manager (IT)",
     "Продуктовая стратегия, Confluence, Excel.", '["Confluence", "Excel"]', None, None),
    ("4", "https://hh.ru/4", "Технический писатель", "Docs Inc.", "Technical Writer",
     "Документация, Confluence, Markdown.", '["Confluence"]', 80000, 100000),
    ("5", "https://hh.ru/5", "Системный аналитик Junior", "SoftHouse", "System Analyst",
     "Требования: SQL, REST API.", '["SQL", "REST"]', None, None),
    ("6", "https://hh.ru/6", "Product Manager", "TechCorp", "Product Manager (IT)",
     "Confluence, Jira, аналитика рынка.", '["Confluence", "Jira"]', 120000, 120000),
]
REQUIREMENTS = [
    ("1", "requirements", "Знание SQL и Python"), ("1", "responsibilities", "Анализ требований"),
    ("2", "requirements", "Опыт Agile/Scrum"),
]


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("db.DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    create_db(conn)
    conn.close()
    return db_file


@pytest.fixture
def populated_db(tmp_db, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_db)
    conn = sqlite3.connect(tmp_db)
    conn.executemany(
        """INSERT OR IGNORE INTO vacancies
           (id, url, name, employer, profession, raw_text, skills_json, salary_from, salary_to)
           VALUES (?,?,?,?,?,?,?,?,?)""", VACANCIES)
    conn.executemany("INSERT INTO requirements (vacancy_id, section, item) VALUES (?,?,?)", REQUIREMENTS)
    conn.commit()
    conn.close()
    return tmp_db


@pytest.fixture
def client(populated_db, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", populated_db)
    import app as app_module
    monkeypatch.setattr("app.DB_PATH", populated_db)
    app_module.app.config["TESTING"] = True
    app_module.app.root_path = str(Path(__file__).parent.parent)
    with app_module.app.test_client() as c:
        yield c


@pytest.mark.parametrize("name,expected", [
    ("Системный аналитик", "System Analyst"),
    ("IT Project Manager", "IT Project Manager"),
    ("Product Manager IT", "Product Manager (IT)"),
    ("Технический писатель", "Technical Writer"),
    ("Курьер", None),
])
def test_classify_vacancy(name, expected):
    assert classify_vacancy(name) == expected


@pytest.mark.parametrize("html,check", [
    ("", lambda r: r["requirements"] == [] and r["raw_text"] == ""),
    ("<h3>Требования</h3><ul><li>Знание Python</li></ul>", lambda r: len(r["requirements"]) >= 1),
])
def test_parse_description(html, check):
    assert check(parse_description(html))


def test_extract_skills():
    assert "SQL" in extract_skills("Требуется знание SQL и Python")
    assert extract_skills("") == []


@pytest.mark.parametrize("text,expected", [
    ("Оклад 150 000 рублей, полная занятость", (150000, 150000)),
    ("Заработная плата: от 150 000 до 200 000 руб.", (150000, 200000)),
    ("Дружный коллектив, удалённая работа", (None, None)),
    (None, (None, None)),
])
def test_extract_salary(text, expected):
    assert extract_salary(text) == expected


class TestDatabase:

    def test_get_stats(self, populated_db, monkeypatch):
        monkeypatch.setattr("db.DB_PATH", populated_db)
        stats = get_stats()
        assert stats["total"] == 6
        assert stats["by_profession"]["System Analyst"] == 2

    def test_get_typical_vacancy(self, populated_db, monkeypatch):
        monkeypatch.setattr("db.DB_PATH", populated_db)
        v = get_typical_vacancy("System Analyst")
        assert v is not None and v["profession"] == "System Analyst"
        assert get_typical_vacancy("Несуществующая профессия") is None

    def test_get_generalized_vacancy(self, populated_db, monkeypatch):
        monkeypatch.setattr("db.DB_PATH", populated_db)
        g = get_generalized_vacancy("System Analyst")
        assert g["profession"] == "System Analyst" and "top_skills" in g

    def test_get_top_skills_and_employers(self, populated_db, monkeypatch):
        monkeypatch.setattr("db.DB_PATH", populated_db)
        skill_map = {item["skill"]: item["count"] for item in get_top_skills_all()}
        assert skill_map["SQL"] == 2
        top = get_top_employers()
        assert top[0]["employer"] == "TechCorp" and top[0]["count"] == 2


class TestSearch:

    def test_basic_search(self, populated_db, monkeypatch):
        monkeypatch.setattr("db.DB_PATH", populated_db)
        results = search_by_skills("SQL Python", top_n=3)
        assert isinstance(results, list) and len(results) <= 3
        assert "score" in results[0]

    def test_empty_db_returns_empty(self, tmp_db, monkeypatch):
        monkeypatch.setattr("db.DB_PATH", tmp_db)
        assert search_by_skills("Python SQL") == []

    @pytest.mark.parametrize("salary_min,salary_max,expect_in,expect_out", [
        (180000, 220000, "1", "4"),
        (0, 10_000_000, "1", "3"),
    ])
    def test_search_salary_filter(self, populated_db, monkeypatch, salary_min, salary_max, expect_in, expect_out):
        monkeypatch.setattr("db.DB_PATH", populated_db)
        ids = {r["id"] for r in search_by_skills("SQL Python", salary_min=salary_min, salary_max=salary_max)}
        assert expect_in in ids and expect_out not in ids


class TestFlaskAPI:

    @pytest.mark.parametrize("path,json_check", [
        ("/", None),
        ("/api/professions", lambda d: "System Analyst" in d),
        ("/api/stats", lambda d: "total" in d),
        ("/api/typical/System%20Analyst", lambda d: d["profession"] == "System Analyst"),
    ])
    def test_get_endpoints_ok(self, client, path, json_check):
        resp = client.get(path)
        assert resp.status_code == 200
        if json_check:
            assert json_check(resp.get_json())

    def test_api_typical_404(self, client):
        assert client.get("/api/typical/Unknown%20Prof").status_code == 404

    def test_api_search(self, client):
        resp = client.post("/api/search", data=json.dumps({"skills": "SQL Python"}), content_type="application/json")
        assert resp.status_code == 200 and isinstance(resp.get_json(), list)

    def test_api_search_empty_skills(self, client):
        resp = client.post("/api/search", data=json.dumps({"skills": ""}), content_type="application/json")
        assert resp.status_code == 400

    def test_api_search_swapped_salary_range(self, client):
        """При min > max значения должны автоматически переставиться местами."""
        resp = client.post(
            "/api/search",
            data=json.dumps({"skills": "SQL Python", "salary_min": 220000, "salary_max": 180000}),
            content_type="application/json",
        )
        ids = {r["id"] for r in resp.get_json()}
        assert resp.status_code == 200 and "1" in ids
