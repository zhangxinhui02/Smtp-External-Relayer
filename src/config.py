import yaml
from pydantic import BaseModel


class LogConfig(BaseModel):
  level: str = 'INFO'
  dump_enabled: bool = True
  dump_retain_days: int = 7

class SmtpServerConfig(BaseModel):
  listen_host: str = '0.0.0.0'
  listen_port: int = 25

class AdapterConfig(BaseModel):
    use: str = 'aliyun-directmail'


initialized = False
LOG: LogConfig | None = None
SMTP: SmtpServerConfig | None = None
ADAPTER: AdapterConfig | None = None

def initialize():
    global LOG, SMTP, ADAPTER
    with open('../config/config.yaml', 'r', encoding='utf-8') as f:
        __data = yaml.safe_load(f)
    LOG = LogConfig(**__data['log'])
    SMTP = SmtpServerConfig(**__data['smtp_server'])
    ADAPTER = AdapterConfig(**__data['adapter'])


if not initialized:
    initialize()
    initialized = True
