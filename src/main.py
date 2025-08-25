#!/bin/python3
import sys
import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler
from config import LOG, ADAPTER
import smtp_server

def initialize_logging():
    logging_handlers = [logging.StreamHandler(sys.stdout)]
    if LOG.dump_enabled:
        logging_handlers.append(
            TimedRotatingFileHandler(
                filename='../log/smtp-external-relayer.log',
                when='D',
                interval=1,
                backupCount=LOG.dump_retain_days,
                encoding='utf-8',
                utc=False
            )
        )
    logging.basicConfig(
        level=LOG.level,
        format='[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s',
        handlers=logging_handlers
    )

if __name__ == '__main__':
    initialize_logging()
    logger = logging.getLogger('main')
    logger.info('Config loaded.')
    logger.info(f'Using adapter: {ADAPTER.use}')
    logger.info('Starting SMTP External Relayer...')
    smtp_server.adapter.start()
    asyncio.run(smtp_server.start())
    logger.info('SMTP External Relayer stopped.')
