import os
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
    # 环境变量覆盖
    for field, info in LOG.model_fields.items():
        if val_raw := os.environ.get(f'APP_LOG_{field}'):
            val_type = info.annotations
            try:
                setattr(LOG, field, val_type(val_raw))
            except Exception as e:
                raise ValueError(f'Failed to parse config `LOG.{field}` '
                                 f'from env `APP_LOG_{field}`: {e}')
    for field, info in SMTP.model_fields.items():
        if val_raw := os.environ.get(f'APP_SMTP_{field}'):
            val_type = info.annotations
            try:
                setattr(SMTP, field, val_type(val_raw))
            except Exception as e:
                raise ValueError(f'Failed to parse config `SMTP.{field}` '
                                 f'from env `APP_SMTP_{field}`: {e}')
    for field, info in ADAPTER.model_fields.items():
        if val_raw := os.environ.get(f'APP_ADAPTER_{field}'):
            val_type = info.annotations
            try:
                setattr(ADAPTER, field, val_type(val_raw))
            except Exception as e:
                raise ValueError(f'Failed to parse config `ADAPTER.{field}` '
                                 f'from env `APP_ADAPTER_{field}`: {e}')

if not initialized:
    initialize()
    initialized = True
