FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=5000
ENV DOWNLOAD_ROOT=/app/downloads
EXPOSE 5000
CMD ["python", "app.py"]
