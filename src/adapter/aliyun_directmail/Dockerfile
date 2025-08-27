# 对应阿里云DirectMail适配器的镜像
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

# 删除其他适配器，修改配置文件并安装最小依赖
RUN find src/adapter -maxdepth 1 -type d ! -name '.' ! -name 'aliyun_directmail' -exec rm -rf {} + && \
    sed -i 's/^\(\s*use:\s*\).*/\1aliyun_directmail/' config/config.yaml && \
    pip install --no-cache-dir -r src/requirements.txt && \
    pip install --no-cache-dir -r src/adapter/aliyun_directmail/requirements.txt

EXPOSE 2525

WORKDIR /app/src

CMD ["python3", "main.py"]
