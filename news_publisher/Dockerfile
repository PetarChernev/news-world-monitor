FROM python:3.11-slim
ENV PROJECT_ID=world-monitor
ENV TOPIC_ID=news.raw

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
