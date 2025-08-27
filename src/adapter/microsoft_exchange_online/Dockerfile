# 对应Microsoft Exchange Online适配器的镜像
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

# 删除其他适配器，修改配置文件并安装最小依赖
RUN cd src/adapter && \
    find . -maxdepth 1 -type d ! -name '.' ! -name 'microsoft_exchange_online' -exec rm -rf {} + && \
    cd /app && \
    sed -i 's/^\(\s*use:\s*\).*/\1microsoft_exchange_online/' config/config.yaml && \
    pip install --no-cache-dir -r src/requirements.txt && \
    pip install --no-cache-dir -r src/adapter/microsoft_exchange_online/requirements.txt

# 安装Powershell和ExchangeOnlineManagement工具
RUN apt-get update && \
    apt-get install -y wget apt-transport-https software-properties-common && \
    wget -q https://packages.microsoft.com/keys/microsoft.asc -O- | apt-key add - && \
    sh -c 'echo "deb [arch=amd64] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/microsoft.list' && \
    apt-get update && \
    apt-get install -y --no-install-recommends powershell && \
    rm -rf /var/lib/apt/lists/* && \
    pwsh -Command "Install-Module ExchangeOnlineManagement -Force -AllowClobber -Scope AllUsers"

EXPOSE 2525

WORKDIR /app/src

CMD ["python3", "main.py"]
