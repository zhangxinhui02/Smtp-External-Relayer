import os
import yaml
import time
import shlex
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

# 网络请求的最长等待时间。出口受限的环境里，请求可能被静默丢包而不是被拒绝，
# 不设超时会让 SMTP 会话一直挂着，最终在发信方表现为连接被断开。
NETWORK_TIMEOUT = aiohttp.ClientTimeout(total=60)

# 适配器用异常字符串的前 3 个字符回传要发给对端的 SMTP 状态码（见 smtp_server.Handler.handle_DATA）：
# 4xx 表示临时失败，发信方会保留队列稍后重试；5xx 表示永久失败，发信方会立刻退信。
TRANSIENT_STATUS = '451 4.4.2'

# 等待其他任务创建同一个共享邮箱的最长时间，创建流程卡住时不应无限等待。
USER_CREATING_WAIT_SECONDS = 300


def _describe_error(error: BaseException) -> str:
    """把异常整理成便于排查的一行"""
    return f'{type(error).__name__}: {error}'


class Config(BaseModel):
    organization: str
    tenant_id: str
    client_id: str
    client_secret: str
    sender: str
    certificate_path: str = ''
    certificate_b64: str = ''
    certificate_password: str = ''
    powershell_cmd: str = 'pwsh'
    initial_user_waiting_seconds: int = 30


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
                if val_type not in (str, int, float):
                    continue
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
        self.__creating_users: list = []

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
            try:
                async with aiohttp.ClientSession(timeout=NETWORK_TIMEOUT) as session:
                    async with session.post(url, headers=headers, data=data) as response:
                        if response.status == 200:
                            json_response = await response.json()
                            self.__access_token = json_response['access_token']
                            self.__access_token_expiring_time = datetime.now() + timedelta(
                                seconds=json_response['expires_in'])
                            logger.info('Access token renewed.')
                        else:
                            error_message = await response.text()
                            logger.error(f"Failed to renew access token. Status code: {response.status}")
                            logger.error(f"Error message: {error_message}")
                            # 拿不到 token 时必须让本次发信失败，而不是带着旧的/空的 token 继续，
                            # 否则后面 Graph 会回 401，被当成永久失败直接退信。
                            if response.status == 429 or response.status >= 500:
                                raise RuntimeError(f'{TRANSIENT_STATUS} Failed to renew access token '
                                                   f'(HTTP {response.status}): {error_message}')
                            raise RuntimeError(f'550 Failed to renew access token '
                                               f'(HTTP {response.status}): {error_message}')
            except asyncio.TimeoutError as e:
                raise RuntimeError(f'{TRANSIENT_STATUS} Timeout while renewing access token: '
                                   f'{_describe_error(e)}')
            except aiohttp.ClientError as e:
                raise RuntimeError(f'{TRANSIENT_STATUS} Cannot reach {url}: {_describe_error(e)}')

    async def __check_users(self, user_name: str, user_addr: str):
        # 当用户不存在时，创建用户的共享邮箱并分配`SendAs`权限
        if user_addr in self.__existing_users:
            return
        logger.info(f"New user found. Creating user `{user_addr}`...")

        # 检查待创建的地址是否正在被其他任务创建，如果是的话，等待创建完毕后返回
        if user_addr in self.__creating_users:
            logger.info(f'User `{user_addr}` is being created by another task. Waiting...')
            deadline = time.monotonic() + USER_CREATING_WAIT_SECONDS
            while user_addr in self.__creating_users:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'{TRANSIENT_STATUS} Timed out waiting for `{user_addr}` '
                                       f'to be created by another task.')
                await asyncio.sleep(1)
            return

        self.__creating_users.append(user_addr)
        # 无论创建成功、失败还是被打断，都必须把地址从 __creating_users 中移除，
        # 否则之后来自该地址的邮件会一直卡在上面那个等待循环里，永远不返回。
        try:
            if user_name == '':
                user_name = user_addr.split('@')[0]
            cmd = [
                self.CONFIG.powershell_cmd,
                '-File', 'adapter/microsoft_exchange_online/init-new-user.ps1',
                '-AppId', self.CONFIG.client_id,
                '-Organization', self.CONFIG.organization,
                '-CertificatePath', self.CONFIG.certificate_path,
                '-TargetAddress', user_addr,
                '-TargetName', user_name,
                '-SenderAddress', self.CONFIG.sender
            ]
            if self.CONFIG.certificate_password:
                cmd.extend(['-CertificatePassword', self.CONFIG.certificate_password])
            cmd_str = ' '.join(shlex.quote(x) for x in cmd)
            logger.debug(f'Running command: `{cmd_str}`')

            process = await asyncio.create_subprocess_exec(
                *cmd,
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
                # 创建失败通常是出口网络、凭据或 Exchange Online 侧的临时问题，
                # 按临时失败处理，让发信方稍后重试，而不是直接把邮件退回去。
                raise RuntimeError(f'{TRANSIENT_STATUS} Failed to create user: '
                                   f'{error_message.decode().strip()}')
            logger.info(f'Created new user `{user_name} <{user_addr}>`.')
            self.__existing_users.append(user_addr)
            logger.info(f'Waiting {self.CONFIG.initial_user_waiting_seconds}s for Exchange Online...')
            await asyncio.sleep(self.CONFIG.initial_user_waiting_seconds)  # Exchange Online 处理较慢，需要等待
        finally:
            if user_addr in self.__creating_users:
                self.__creating_users.remove(user_addr)

    async def start(self):
        # 初始化适配器，缓存已有的用户列表，不重复创建用户
        await self.__check_access_token()
        url = 'https://graph.microsoft.com/v1.0/users'
        headers = {
            'Authorization': f'Bearer {self.__access_token}',
            'Content-Type': 'application/json'
        }
        try:
            async with aiohttp.ClientSession(timeout=NETWORK_TIMEOUT) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        self.__existing_users = [user['mail'] for user in json_response['value']]
                        logger.info(f"Initialized OK. Number of existing users: {len(self.__existing_users)}.")
                    else:
                        response.raise_for_status()
        except asyncio.TimeoutError as e:
            raise RuntimeError(f'{TRANSIENT_STATUS} Timeout while fetching the user list: '
                               f'{_describe_error(e)}')
        except aiohttp.ClientError as e:
            raise RuntimeError(f'{TRANSIENT_STATUS} Cannot fetch the user list: {_describe_error(e)}')

    async def send_mail(self, envelope: Envelope) -> str:
        # 解析发信用户名和地址，检查是否需要创建
        mail = message_from_bytes(envelope.content)
        from_header = mail.get("From")
        from_name, from_addr = parseaddr(from_header)
        from_name = str(make_header(decode_header(from_name)))
        # 显示名未按 RFC 2047 编码时 parseaddr 会解析出空地址，
        # 不能拿着空地址去走建号流程，否则错误会以很莫名其妙的方式冒出来。
        if not from_addr:
            return f'550 Malformed From header: {str(from_header)[:200]}'
        await self.__check_users(from_name, from_addr)
        # 发送邮件
        await self.__check_access_token()
        url = f'https://graph.microsoft.com/v1.0/users/{self.CONFIG.sender}/sendMail'
        headers = {
            'Authorization': f'Bearer {self.__access_token}',
            'Content-Type': 'text/plain'
        }
        mime_b64 = base64.b64encode(envelope.content).decode("ascii")
        try:
            async with aiohttp.ClientSession(timeout=NETWORK_TIMEOUT) as session:
                async with session.post(url, headers=headers, data=mime_b64) as response:
                    if response.status == 202:
                        return '250 Message accepted for delivery'
                    response_text = await response.text()
                    # 429（限流）和 5xx 属于临时故障，让发信方稍后重试；其余按永久失败处理
                    if response.status == 429 or response.status >= 500:
                        logger.error(f'Graph API returned HTTP {response.status}, will be retried: '
                                     f'{response_text}')
                        return f'{TRANSIENT_STATUS} Graph API returned HTTP {response.status}: {response_text}'
                    logger.error(f'Graph API rejected the mail with HTTP {response.status}: {response_text}')
                    return f'550 Graph API returned HTTP {response.status}: {response_text}'
        except asyncio.TimeoutError as e:
            raise RuntimeError(f'{TRANSIENT_STATUS} Timeout while calling the Graph API: '
                               f'{_describe_error(e)}')
        except aiohttp.ClientError as e:
            raise RuntimeError(f'{TRANSIENT_STATUS} Cannot reach the Graph API: {_describe_error(e)}')
