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

# Shell form (not exec-form CMD) so $PORT actually expands -- Render (and most PaaS Docker
# hosts) inject PORT at runtime and expect the app to bind to it, not to a value baked in at
# build time. Defaults to 8000 for docker-compose / a plain `docker run`, where PORT is unset.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
