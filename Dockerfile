FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    requests \
    beautifulsoup4

COPY puppet_watcher.py /app/puppet_watcher.py

RUN mkdir -p /data

CMD ["python", "/app/puppet_watcher.py"]
