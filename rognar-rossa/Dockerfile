# Рогнар — слушатель Telegram. Отдельный образ, ни с чем в CRM не связан.
FROM python:3.12-slim

# tini — чтобы Ctrl+C и docker stop доходили до Python, а не оставляли зомби.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY watchlist.txt ignore.txt ./

# Сессия живёт на томе: пересборка образа не должна заставлять входить заново.
ENV ROGNAR_SESSION=/data/rognar.session \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
VOLUME ["/data"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "rognar.py"]
