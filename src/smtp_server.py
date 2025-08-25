import time
import inspect
import asyncio
import logging
import importlib
from aiosmtpd.controller import Controller
from config import SMTP, ADAPTER

logger = logging.getLogger(__name__)
logging.getLogger('mail.log').setLevel(logging.WARNING)
adapter_module = importlib.import_module(f'adapter.{ADAPTER.use}')
adapter = getattr(adapter_module, 'Adapter')()


class Handler:
    @staticmethod
    async def handle_DATA(_, __, envelope):
        logger.info('Received a new mail.')
        try:
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
            return f'451 Temporary failure: {e}'

async def start():
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
