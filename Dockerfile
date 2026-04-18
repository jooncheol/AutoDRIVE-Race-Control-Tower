# SPDX-License-Identifier: BSD-3-Clause

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RCT_HOST=0.0.0.0 \
    RCT_PORT=4567

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rct ./rct
COPY frontend ./frontend
COPY README.md AGENTS.md ./

EXPOSE 4567

CMD ["python", "-m", "rct"]
