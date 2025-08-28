# 适配阿里云邮件推送服务(DirectMail)
import os
import time
import yaml
import random
import string
import smtplib
import logging
import multiprocessing as mp
from aiosmtpd.smtp import Envelope
from multiprocessing.managers import SyncManager
from alibabacloud_tea_openapi.exceptions import ClientException
from pydantic import BaseModel
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_dm20151123.client import Client as Client
from alibabacloud_dm20151123 import models as models
from adapter.base import AdapterBase
from email import message_from_bytes
from email.utils import parseaddr


logger = logging.getLogger(__name__)

class Config(BaseModel):
    access_key_id: str
    access_key_secret: str
    smtp_ssl_encrypt: bool = True
    static_addresses_password: dict[str, str] = {}
    mail_addresses_pool_count: int = 6

class Adapter(AdapterBase):
    def __init__(self):
        super().__init__()
        self.name = 'aliyun_directmail'

        self.__multiprocessing_manager: SyncManager | None = None
        self.working_addresses: dict | None = None

        with open('../config/config.yaml', 'r', encoding='utf-8') as f:
            self.CONFIG = Config(
                **yaml.safe_load(f)['aliyun_directmail']
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
        __config = open_api_models.Config(
            access_key_id=self.CONFIG.access_key_id,
            access_key_secret=self.CONFIG.access_key_secret
        )
        __config.endpoint = 'dm.aliyuncs.com'

        self.client = Client(config=__config)

    @classmethod
    def __generate_password(cls):
        # 选择密码长度 10~20
        length = random.randint(10, 20)

        # 基本字符集
        digits = string.digits
        lowers = string.ascii_lowercase
        uppers = string.ascii_uppercase

        # 确保至少 2 个数字、2 个大写字母、2 个小写字母
        password_chars = [
            random.choice(digits), random.choice(digits),
            random.choice(lowers), random.choice(lowers),
            random.choice(uppers), random.choice(uppers),
        ]

        # 剩余的长度用随机字符补充（数字+大小写字母）
        all_chars = digits + lowers + uppers
        remaining = length - len(password_chars)
        password_chars += random.choices(all_chars, k=remaining)

        # 打乱顺序
        random.shuffle(password_chars)
        password = "".join(password_chars)

        # 检查密码是否符合“不能单一字符重复”要求
        def valid(pwd):
            has_digit = sum(c.isdigit() for c in pwd) >= 2
            has_upper = sum(c.isupper() for c in pwd) >= 2
            has_lower = sum(c.islower() for c in pwd) >= 2

            # 检查数字是否不是同一个重复字符
            digits_in_pwd = [c for c in pwd if c.isdigit()]
            digit_valid = len(set(digits_in_pwd)) > 1

            # 检查字母是否不是同一个重复字符
            letters_in_pwd = [c for c in pwd if c.isalpha()]
            letter_valid = len(set(letters_in_pwd)) > 1

            return has_digit and has_upper and has_lower and digit_valid and letter_valid

        # 如果不符合条件则重新生成
        while not valid(password):
            password = cls.__generate_password()

        return password

    def __create_address(self, address: str):
        while ((address in self.working_addresses) or
               (len(self.working_addresses) >= self.CONFIG.mail_addresses_pool_count)):
            time.sleep(0.1)
        self.working_addresses[address] = self.__multiprocessing_manager.dict(
            {'id': None, 'password': None}
        )
        request = models.CreateMailAddressRequest()
        request.account_name = address
        request.sendtype = 'trigger'
        try:
            response = self.client.create_mail_address(request)
            self.working_addresses[address] = self.__multiprocessing_manager.dict(
                {'id': response.body.mail_address_id, 'password': None}
            )
        except Exception as e:
            error = f'Failed to create working mail address `{address}`:\n\t{e}'
            logger.error(error)
            del self.working_addresses[address]
            raise Exception(error)

    def __set_smtp_password(self, address: str):
        self.working_addresses[address] = self.__multiprocessing_manager.dict(
            {'id': self.working_addresses[address]["id"], 'password': self.__generate_password()}
        )
        request = models.ModifyMailAddressRequest()
        request.mail_address_id = self.working_addresses[address]['id']
        request.password = self.working_addresses[address]['password']
        try:
            self.client.modify_mail_address(request)
        except Exception as e:
            error = f'Failed to set SMTP password for working mail address `{address}`:\n\t{e}'
            logger.error(error)
            del self.working_addresses[address]
            raise Exception(error)

    def __send_mail(self, envelope: Envelope, username: str, password: str):
        try:
            if self.CONFIG.smtp_ssl_encrypt:
                with smtplib.SMTP_SSL('smtpdm.aliyun.com', 465) as smtp:
                    smtp.login(username, password)
                    smtp.sendmail(username, envelope.rcpt_tos, envelope.content)
            else:
                with smtplib.SMTP('smtpdm.aliyun.com', 80) as smtp:
                    smtp.login(username, password)
                    smtp.sendmail(username, envelope.rcpt_tos, envelope.content)
        except Exception as e:
            error = f'Failed to send mail by `{username}`:\n\t{e}'
            logger.error(error)
            if username in self.working_addresses:
                del self.working_addresses[username]
            raise Exception(error)

    def __delete_address(self, address: str):
        request = models.DeleteMailAddressRequest()
        request.mail_address_id = self.working_addresses[address]
        try:
            self.client.delete_mail_address(request)
            del self.working_addresses[address]
        except ClientException as e:
            if e.status_code == 404:
                logger.warning(f'Working address `{address}` does not exist. Did you delete it manually?')
                del self.working_addresses[address]
        except Exception as e:
            error = f'Failed to delete working mail address `{address}`:\n\t{e}'
            logger.error(error)
            raise Exception(error)

    def main_start(self):
        # 地址创建队列
        self.__multiprocessing_manager = mp.Manager()
        self.working_addresses = self.__multiprocessing_manager.dict()

    def send_mail(self, envelope: Envelope) -> str:
        logger.info('Sending mail...')
        raw_mail = envelope.content  # bytes
        mail = message_from_bytes(raw_mail)
        from_header = mail.get("From")
        _, from_addr = parseaddr(from_header)
        if from_addr == '':
            error = '550 A `from address` field is required.'
            logger.error(error)
            return error

        if from_addr not in self.CONFIG.static_addresses_password:
            logger.debug(f'Sender address `{from_addr}` is not a static mail address.')
            logger.debug('Creating working mail address...')
            self.__create_address(from_addr)
            logger.debug('Setting SMTP password for working mail address...')
            self.__set_smtp_password(from_addr)
            logger.debug('Sending mail by working mail address...')
            password = self.working_addresses[from_addr]['password']
        else:
            logger.debug(f'Sender address `{from_addr}` is a static mail address.')
            logger.debug('Sending mail by static mail address...')
            password = self.CONFIG.static_addresses_password[from_addr]
        self.__send_mail(envelope, from_addr, password)
        if from_addr not in self.CONFIG.static_addresses_password:
            logger.debug('Deleting working mail address...')
            self.__delete_address(from_addr)

        return "250 Message accepted for delivery"

    def stop(self):
        self.__multiprocessing_manager.shutdown()
