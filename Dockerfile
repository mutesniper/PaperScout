FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements-lock.txt /app/requirements-lock.txt
RUN pip install --no-cache-dir -r requirements-lock.txt \
    && useradd --uid 10001 --create-home paperscout
COPY backend/app /app/backend/app
RUN mkdir -p /app/backend/data && chown -R paperscout:paperscout /app/backend/data
USER paperscout
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
