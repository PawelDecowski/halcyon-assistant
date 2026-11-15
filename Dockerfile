FROM python:3.13-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# Railway sets PORT. One worker on purpose: background tasks and SQLite live in this process.
CMD ["sh", "-c", "uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
