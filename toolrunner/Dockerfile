FROM python:3.12-slim

WORKDIR /app

COPY toolrunner/requirements.txt .

RUN python -m pip install --no-cache-dir -r requirements.txt

COPY toolrunner /app/toolrunner

ENV SANDBOX_ROOT=/var/agentmaestro/sandbox
VOLUME /var/agentmaestro/sandbox

EXPOSE 8001

CMD ["uvicorn", "toolrunner.app:app", "--host", "0.0.0.0", "--port", "8001"]
