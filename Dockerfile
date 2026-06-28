# Dockerfile — кладите в корень репозитория, рядом с папкой vacancy_app

FROM python:3.12-slim

WORKDIR /app

# Сначала копируем только requirements.txt, чтобы Docker кэшировал слой
# с зависимостями и не переустанавливал их при каждом изменении кода
COPY vacancy_app/requirements.txt vacancy_app/requirements.txt
RUN pip install --no-cache-dir -r vacancy_app/requirements.txt

# Копируем код приложения (включая уже готовый vacancies.db)
COPY vacancy_app/ vacancy_app/

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "vacancy_app/app.py"]
