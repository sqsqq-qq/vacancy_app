"""app.py — Flask-дашборд анализа вакансий (категория 7). Запуск: python app.py"""

from flask import Flask, jsonify, render_template, request

from db import (
    DB_PATH, JSONL_PATH, get_conn, get_generalized_vacancy, get_stats,
    get_top_employers, get_top_skills_all, get_typical_vacancy, load_data, search_by_skills,
)

app = Flask(__name__)
PROFESSIONS = ["System Analyst", "IT Project Manager", "Product Manager (IT)", "Technical Writer"]

# ПРОВЕРКА БД
def init_app() -> None:
    """Загружает данные в БД, если она отсутствует или пуста."""
    if DB_PATH.exists():
        conn = get_conn()
        empty = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0] == 0
        conn.close()
        if not empty:
            return
    load_data(JSONL_PATH, DB_PATH)


def _to_int(value):
    try:
        return int(value) if value not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


@app.route("/")
def index():
    return render_template("index.html", professions=PROFESSIONS)


@app.route("/api/professions")
def api_professions():
    return jsonify(PROFESSIONS)


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/top_skills")
def api_top_skills():
    return jsonify(get_top_skills_all())


@app.route("/api/top_employers")
def api_top_employers():
    return jsonify(get_top_employers())


@app.route("/api/typical/<profession>")
def api_typical(profession: str):
    v = get_typical_vacancy(profession)
    return (jsonify(v), 200) if v else (jsonify({"error": "Вакансия не найдена"}), 404)


@app.route("/api/generalized/<profession>")
def api_generalized(profession: str):
    return jsonify(get_generalized_vacancy(profession))


@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json(force=True)
    skills = data.get("skills", "").strip()
    if not skills:
        return jsonify({"error": "Укажите навыки"}), 400

    salary_min, salary_max = _to_int(data.get("salary_min")), _to_int(data.get("salary_max"))
    if salary_min is not None and salary_max is not None and salary_min > salary_max:
        salary_min, salary_max = salary_max, salary_min

    return jsonify(search_by_skills(skills, salary_min=salary_min, salary_max=salary_max))


if __name__ == "__main__":
    init_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
