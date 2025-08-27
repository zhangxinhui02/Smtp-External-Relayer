# 对应Microsoft Exchange Online适配器的镜像
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

# 删除其他适配器并安装最小依赖
RUN find src/adapter -maxdepth 1 -type d ! -name '.' ! -name 'microsoft_exchange_online' -exec rm -rf {} + && \
    pip install --no-cache-dir -r src/requirements.txt && \
    pip install --no-cache-dir -r src/adapter/microsoft_exchange_online/requirements.txt

EXPOSE 2525

WORKDIR /app/src

CMD ["python3", "main.py"]
