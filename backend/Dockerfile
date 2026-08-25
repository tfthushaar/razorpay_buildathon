FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite state (audit log, calibration history) lives here — self-creates on first write,
# mounted as a volume in docker-compose.yml so a demo's accumulated trust survives a restart.
VOLUME ["/app/data"]

ENV LLM_PROVIDER=mock
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
