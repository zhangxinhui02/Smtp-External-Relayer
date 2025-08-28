import os
import yaml
import base64
import asyncio
import aiohttp
import logging
from pydantic import BaseModel
from aiosmtpd.smtp import Envelope
from email import message_from_bytes
from email.utils import parseaddr
from email.header import decode_header, make_header
from datetime import datetime, timedelta
from adapter.base import AdapterBase

logger = logging.getLogger(__name__)


class Config(BaseModel):
    organization: str
    tenant_id: str
    client_id: str
    client_secret: str
    sender: str
    certificate_path: str | None = None
    certificate_b64: str | None = None
    certificate_password: str = ''
    powershell_cmd: str = 'pwsh'


class Adapter(AdapterBase):
    def __init__(self):
        super().__init__()
        self.name = 'microsoft_exchange_online'
        with open('../config/config.yaml', 'r', encoding='utf-8') as f:
            self.CONFIG = Config(
                **yaml.safe_load(f)[self.name]
            )
        # 环境变量覆盖
        for field, info in self.CONFIG.model_fields.items():
            if val_raw := os.environ.get(f'APP_{self.name.upper()}_{field.upper()}'):
                val_type = info.annotation
                try:
                    setattr(self.CONFIG, field, val_type(val_raw))
                except Exception as e:
                    raise ValueError(f'Failed to parse config `{self.name}.{field}` '
                                     f'from env `APP_{self.name.upper()}_{field.upper()}`: {e}')
        # 必须指定证书路径或base64编码的证书
        if (not self.CONFIG.certificate_path) and (not self.CONFIG.certificate_b64):
            error = 'You must specify either a certificate_path or a certificate_b64.'
            logger.error(error)
            raise ValueError(error)
        # 解码出证书
        if self.CONFIG.certificate_b64:
            with open('../cert.pfx', 'wb') as f:
                f.write(base64.b64decode(self.CONFIG.certificate_b64))
            self.CONFIG.certificate_path = '../cert.pfx'

        self.__access_token: str | None = None
        self.__access_token_expiring_time: datetime | None = None
        self.__existing_users: list = []  # 已有用户的缓存

    async def __check_access_token(self):
        # 当token不存在或者即将过期，更新token
        if self.__access_token is None or self.__access_token_expiring_time - datetime.now() < timedelta(minutes=5):
            url = f"https://login.microsoftonline.com/{self.CONFIG.tenant_id}/oauth2/v2.0/token"
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            data = {
                'client_id': self.CONFIG.client_id,
                'scope': 'https://graph.microsoft.com/.default',
                'client_secret': self.CONFIG.client_secret,
                'grant_type': 'client_credentials'
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=data) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        self.__access_token = json_response['access_token']
                        self.__access_token_expiring_time = datetime.now() + timedelta(
                            seconds=json_response['expires_in'])
                        logger.info('Access token renewed.')
                    else:
                        logger.error(f"Failed to renew access token. Status code: {response.status}")
                        error_message = await response.text()
                        logger.error(f"Error message: {error_message}")

    async def __check_users(self, user_name: str, user_addr: str):
        # 当用户不存在时，创建用户的共享邮箱并分配`SendAs`权限
        if user_addr not in self.__existing_users:
            logger.info(f"New user found. Creating user `{user_addr}`...")
            if user_name == '':
                user_name = user_addr.split('@')[0]
            cmd = (f'{self.CONFIG.powershell_cmd} '
                   f'-File adapter/microsoft_exchange_online/init-new-user.ps1 '
                   f'-AppId {self.CONFIG.client_id} '
                   f'-Organization {self.CONFIG.organization} '
                   f'-CertificatePath {self.CONFIG.certificate_path} '
                   f'-TargetAddress {user_addr} '
                   f'-TargetName {user_name} '
                   f'-SenderAddress {self.CONFIG.sender} ')
            if self.CONFIG.certificate_password:
                cmd += f' -CertificatePassword {self.CONFIG.certificate_password} '
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                logger.debug(f"init-new-user.ps1 OUTPUT: {line.decode().strip()}")

            await process.wait()
            logger.debug(f"init-new-user.ps1 EXIT CODE: {process.returncode}")
            if process.returncode != 0:
                error_message = await process.stderr.read()
                logger.error(f"init-new-user.ps1 ERROR: {error_message.decode()}")
                raise RuntimeError(f"550 Failed to create user: {error_message}")
            else:
                logger.info(f'Created new user `{user_name} <{user_addr}>`.')
                self.__existing_users.append(user_addr)
                logger.info('Waiting 15s for Exchange Online...')
                await asyncio.sleep(15)  # Exchange Online 处理较慢，需要等待


    async def start(self):
        # 初始化适配器，缓存已有的用户列表，不重复创建用户
        await self.__check_access_token()
        url = 'https://graph.microsoft.com/v1.0/users'
        headers = {
            'Authorization': f'Bearer {self.__access_token}',
            'Content-Type': 'application/json'
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    json_response = await response.json()
                    self.__existing_users = [user['mail'] for user in json_response['value']]
                    logger.info(f"Initialized OK. Number of existing users: {len(self.__existing_users)}.")
                else:
                    response.raise_for_status()

    async def send_mail(self, envelope: Envelope) -> str:
        # 解析发信用户名和地址，检查是否需要创建
        mail = message_from_bytes(envelope.content)
        from_header = mail.get("From")
        from_name, from_addr = parseaddr(from_header)
        from_name = str(make_header(decode_header(from_name)))
        await self.__check_users(from_name, from_addr)
        # 发送邮件
        await self.__check_access_token()
        url = f'https://graph.microsoft.com/v1.0/users/{self.CONFIG.sender}/sendMail'
        headers = {
            'Authorization': f'Bearer {self.__access_token}',
            'Content-Type': 'text/plain'
        }
        mime_b64 = base64.b64encode(envelope.content).decode("ascii")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=mime_b64) as response:
                if response.status == 202:
                    return '250 Message accepted for delivery'
                else:
                    return f'550 {await response.text()}'
