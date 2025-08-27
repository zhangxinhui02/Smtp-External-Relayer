# 完整构建，包含所有适配器和依赖，镜像体积较大
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 2525

WORKDIR /app/src

CMD ["python3", "main.py"]
