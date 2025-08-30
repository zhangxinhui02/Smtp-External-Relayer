import time
import hashlib
import inspect
import asyncio
import logging
import importlib
from datetime import datetime, timedelta
from email import message_from_bytes
from email.utils import getaddresses
from email.message import EmailMessage
from email.utils import parseaddr, formataddr
from aiosmtpd.smtp import Envelope
from aiosmtpd.controller import Controller
from config import SMTP, ADAPTER

logger = logging.getLogger(__name__)
logging.getLogger('mail.log').setLevel(logging.WARNING)
adapter_module = importlib.import_module(f'adapter.{ADAPTER.use}')
adapter = getattr(adapter_module, 'Adapter')()


class Handler:
    __email_loop_check_hash = {}

    @classmethod
    async def task_clean_email_loop_check_hash(cls):
        """定时清理无用的邮件哈希记录的后台任务"""

        def __show_email_loop_check_hash():
            if len(cls.__email_loop_check_hash) == 0:
                logger.debug(f'\tEmpty record.')
            for _mail_hash, _mail_data in cls.__email_loop_check_hash.items():
                debug_msg = 'OK.' if _mail_data is None \
                    else f'Banned until {_mail_data["ban_until"].strftime("%Y-%m-%d %H:%M:%S")}.'
                logger.debug(f'\t{_mail_hash}: {debug_msg}')
                for _mail_time in mail_data['time_history']:
                    logger.debug(f'\t\t{_mail_time.strftime("%Y-%m-%d %H:%M:%S")}')

        while True:
            await asyncio.sleep(600)

            logger.debug(f'Before cleaning email loop check hash:')
            __show_email_loop_check_hash()

            now = datetime.now()
            for mail_hash, mail_data in cls.__email_loop_check_hash.items():
                if mail_data['ban_until'] is not None:
                    if now > mail_data['ban_until']:
                        del cls.__email_loop_check_hash[mail_hash]
                        continue

                mail_time_history = []
                for mail_time in mail_data['time_history']:
                    if now - mail_time < timedelta(minutes=SMTP.email_loop_check_time_minutes):
                        mail_time_history.append(mail_time)
                if len(mail_time_history) == 0:
                    del cls.__email_loop_check_hash[mail_hash]
                else:
                    cls.__email_loop_check_hash[mail_hash]['time_history'] = mail_time_history

            logger.debug(f'After cleaning email loop check hash:')
            __show_email_loop_check_hash()

    @staticmethod
    def __gen_email_loop_alert_envelope(from_name, from_addr, to_addr, text, attachment):
        """生成告警邮件的Envelope对象"""
        msg = EmailMessage()
        msg['From'] = formataddr((from_name, from_addr))
        msg['To'] = to_addr
        msg['Subject'] = 'Email Loop Alert'
        msg.set_content(text)
        attachment_data = attachment
        msg.add_attachment(
            attachment_data,
            maintype='message',
            subtype='rfc822',
            filename="last-loop-email.eml"
        )
        envelope = Envelope()
        envelope.mail_from = from_addr
        envelope.rcpt_tos = [to_addr]
        envelope.content = msg.as_bytes()
        return envelope

    @classmethod
    async def __email_loop_check(cls, envelope: Envelope):
        """检查是否出现邮件死循环"""
        logger.info(f'Checking email loop...')

        # 解析收件人和发件人
        content = envelope.content
        message = message_from_bytes(content)
        from_header = message.get("From")
        _, from_addr = parseaddr(from_header)
        recipients = []
        for header in ("To", "Cc", "Bcc"):
            if header in message:
                recipients.extend(getaddresses([message[header]]))
        to_addrs = [addr for _, addr in recipients]

        # 拼接收发地址和邮件内容，计算哈希
        sep = b"\r\n\r\n"
        pos = content.find(sep)
        if pos == -1:
            sep = b"\n\n"
            pos = content.find(sep)
        if pos == -1:
            body = content
        else:
            body = content[pos + len(sep):]
        body = f'From:{from_addr} To:{to_addrs} '.encode() + body
        body_hash = hashlib.sha256(body).hexdigest()
        logger.debug(f'Email hash: {body_hash}')
        now = datetime.now()

        # 根据哈希判断是否为已经被ban的循环邮件
        cls.__email_loop_check_hash.setdefault(body_hash, {'ban_until': None, 'time_history': []})
        if cls.__email_loop_check_hash[body_hash]['ban_until'] is not None:
            if now <= cls.__email_loop_check_hash[body_hash]['ban_until']:
                error = (f'Found email loop. Same email will be rejected until '
                         f'{cls.__email_loop_check_hash[body_hash]["ban_until"].strftime("%Y-%m-%d %H:%M:%S")}.')
                logger.warning(error)
                raise Exception(f'550 {error}')
            else:
                del cls.__email_loop_check_hash[body_hash]

        # 不是已经被ban的循环邮件，统计此邮件在规定时间内被发送的次数
        body_hash_history = []
        for body_hash_time in cls.__email_loop_check_hash[body_hash]['time_history']:
            if now - body_hash_time < timedelta(minutes=SMTP.email_loop_check_time_minutes):
                body_hash_history.append(body_hash_time)
        body_hash_history.append(now)
        cls.__email_loop_check_hash[body_hash]['time_history'] = body_hash_history
        # 如果超过阈值，利用哈希ban掉此邮件
        if len(cls.__email_loop_check_hash[body_hash]['time_history']) >= SMTP.email_loop_threshold:
            cls.__email_loop_check_hash[body_hash]['ban_until'] = (
                    now + timedelta(minutes=SMTP.email_loop_ban_time_minutes))
            to_addrs_str = ','.join(to_addrs)
            error = (f'Found email loop from `{from_addr}` to `{to_addrs_str}` '
                     f'in {SMTP.email_loop_check_time_minutes} minutes. '
                     f'Same email will be rejected in {SMTP.email_loop_ban_time_minutes} minutes.')
            logger.error(error)
            # 如果启用邮件告警，发送告警邮件并附上此循环邮件的eml
            if SMTP.email_loop_alert_to_email:
                if not SMTP.email_loop_alert_from_email:
                    logger.warning(f'A `smtp_server.email_loop_alert_from_email` config is required when'
                                   f'`smtp_server.email_loop_alert_to_email` is configured.')
                else:
                    try:
                        alert_envelope = cls.__gen_email_loop_alert_envelope(
                            from_name='SMTP External Relayer Alerter',
                            from_addr=SMTP.email_loop_alert_from_email,
                            to_addr=SMTP.email_loop_alert_to_email,
                            text=error,
                            attachment=content
                        )
                        if inspect.iscoroutinefunction(adapter.send_mail):
                            await adapter.send_mail(alert_envelope)
                        else:
                            adapter.send_mail(alert_envelope)
                    except Exception as e:
                        logger.error(f'Error sending alert email: {e}')

            raise Exception(f'550 {error}')
        else:
            logger.info('Email loop check OK.')

    @classmethod
    async def handle_DATA(cls, _, __, envelope):
        logger.info('Received a new mail.')
        try:
            if SMTP.stop_email_loop:
                await cls.__email_loop_check(envelope)
            logger.info(f'Sending mail by adapter {ADAPTER.use}...')
            __timer_start = time.perf_counter()
            if inspect.iscoroutinefunction(adapter.send_mail):
                result = await adapter.send_mail(envelope)
            else:
                result = adapter.send_mail(envelope)
            __elapsed = time.perf_counter() - __timer_start
            logger.info(f'Mail has been sent.')
            logger.debug(f'Sending time: {__elapsed:.2f}s.')

            return result

        except Exception as e:
            logger.error(f'Failed to send mail:\n\t{e}')
            try:
                error_message = str(e)
                error_code = int(error_message[:3])
                error_message = error_message[4:]
                return f'{error_code} {error_message}'
            except (ValueError, IndexError):
                return f'451 Temporary failure: {e}'

async def start():
    if inspect.iscoroutinefunction(adapter.start):
        await adapter.start()
    else:
        adapter.start()
    asyncio.create_task(Handler.task_clean_email_loop_check_hash())
    controller = Controller(Handler(), hostname=SMTP.listen_host, port=SMTP.listen_port)
    controller.start()
    logger.info(f'SMTP server listening on {SMTP.listen_host}:{SMTP.listen_port}.')
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        if inspect.iscoroutinefunction(adapter.stop):
            await adapter.stop()
        else:
            adapter.stop()
        logger.info(f'SMTP Server stopped.')
