# 完整构建，包含所有适配器和依赖，镜像体积较大
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 安装Powershell和ExchangeOnlineManagement工具
RUN apt-get update && \
    apt-get install -y wget apt-transport-https software-properties-common && \
    wget -q https://packages.microsoft.com/keys/microsoft.asc -O- | apt-key add - && \
    sh -c 'echo "deb [arch=amd64] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/microsoft.list' && \
    apt-get update && \
    apt-get install -y --no-install-recommends powershell && \
    rm -rf /var/lib/apt/lists/* && \
    pwsh -Command "Set-PSRepository -Name 'PSGallery' -InstallationPolicy Trusted; Install-Module ExchangeOnlineManagement -Force -AllowClobber -Scope AllUsers"

EXPOSE 2525

WORKDIR /app/src

CMD ["python3", "main.py"]
