#!/usr/bin/env python3
"""适配器失败语义的最小回归测试（不依赖 pytest，直接 python3 运行）。

覆盖三件事：
  1. 出口网络不通 / 超时 -> 回 451（临时失败，发信方会重试），而不是 550、也不是断开连接；
  2. Graph API 返回 429 / 5xx -> 451，其它 4xx -> 550（沿用返回值的写法）；
  3. init-new-user.ps1 失败后，地址必须从 __creating_users 中移除，
     之后来自同一地址的邮件不能卡死在等待循环里。

运行（工作目录必须是 src/，因为配置路径写成 ../config/config.yaml）：
    cd src && APP_MICROSOFT_EXCHANGE_ONLINE_CERTIFICATE_PATH=/tmp/nonexistent.pfx \
        PYTHONPATH=. python3 ../tests/test_error_handling.py
"""
import os
import sys
import time
import asyncio
import importlib
from datetime import datetime, timedelta

import aiohttp
from email.header import Header
from email.utils import formataddr
from aiosmtpd.smtp import Envelope

FAILURES = []
PASSED = []


def check(name, cond, detail=''):
    (PASSED if cond else FAILURES).append(name)
    print(f'  {"PASS" if cond else "FAIL"}  {name}{("  <- " + detail) if detail and not cond else ""}')


class FakeResponse:
    def __init__(self, status, body='{}'):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return self._body

    async def json(self):
        import json
        return json.loads(self._body)


def install_fake_session(behavior):
    """把 aiohttp.ClientSession 换成替身；返回还原函数"""
    original = aiohttp.ClientSession

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, **kwargs):
            return behavior('post', url)

        def get(self, url, **kwargs):
            return behavior('get', url)

    aiohttp.ClientSession = FakeSession
    return lambda: setattr(aiohttp.ClientSession, '__self__', None) if False else setattr(aiohttp, 'ClientSession', original)


def make_envelope(name='麦麦2号', addr='newbie@example.com', to='somebody@qq.com', body='hello'):
    # 显示名按 RFC 2047 编码，跟真实邮件客户端一致
    # （未编码的 UTF-8 显示名会被 parseaddr 解析成空地址，那是另一个用例）
    raw = (f'From: {formataddr((str(Header(name, "utf-8")), addr))}\r\n'
           f'To: {to}\r\nSubject: test\r\nMIME-Version: 1.0\r\n'
           f'Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n').encode('utf-8')
    envelope = Envelope()
    envelope.content = raw
    return envelope


def make_adapter(seed_sender=None):
    module = importlib.import_module('adapter.microsoft_exchange_online.microsoft_exchange_online')
    adapter = module.Adapter()
    adapter._Adapter__access_token = 'fake-token'
    adapter._Adapter__access_token_expiring_time = datetime.now() + timedelta(hours=1)
    if seed_sender:
        adapter._Adapter__existing_users.append(seed_sender)
    return module, adapter


async def test_network_failure_is_transient():
    print('\n[1] 网络不通 / 超时 -> 451')
    for label, exc in (('ClientError', aiohttp.ClientError('simulated: network unreachable')),
                       ('TimeoutError', asyncio.TimeoutError())):
        module, adapter = make_adapter()
        adapter._Adapter__access_token = None
        restore = install_fake_session(lambda m, u, e=exc: (_ for _ in ()).throw(e))
        try:
            await adapter._Adapter__check_access_token()
            check(f'{label}: 取 token 失败抛出异常', False, '没有抛异常')
        except RuntimeError as e:
            check(f'{label}: 取 token 失败回 451 -> {str(e)[:58]}', str(e).startswith('451'))
        except BaseException as e:
            check(f'{label}: 取 token 失败回 451', False, f'抛的是 {type(e).__name__}')
        finally:
            restore()

        module, adapter = make_adapter(seed_sender='sender@example.com')
        restore = install_fake_session(lambda m, u, e=exc: (_ for _ in ()).throw(e))
        try:
            result = await adapter.send_mail(make_envelope(name='麦麦2号', addr='sender@example.com'))
            check(f'{label}: 发信失败回 451 -> {result[:58]}', str(result).startswith('451'))
        except RuntimeError as e:
            check(f'{label}: 发信失败回 451 -> {str(e)[:58]}', str(e).startswith('451'))
        finally:
            restore()


async def test_http_status_mapping():
    print('\n[2] Graph 状态码映射')
    for status, expect in ((202, '250'), (429, '451'), (503, '451'), (400, '550'), (401, '550')):
        module, adapter = make_adapter(seed_sender='sender@example.com')
        restore = install_fake_session(lambda m, u, s=status: FakeResponse(s, 'simulated body'))
        try:
            result = await adapter.send_mail(make_envelope(name='麦麦2号', addr='sender@example.com'))
            check(f'HTTP {status} -> {expect}（实得 {str(result)[:40]}）', str(result).startswith(expect))
        finally:
            restore()


async def test_user_creation_leak():
    print('\n[3] 建号失败后不残留（修复前第二次会永久卡住）')
    module, adapter = make_adapter()
    adapter.CONFIG.powershell_cmd = 'false'  # 必定以退出码 1 结束
    envelope = make_envelope(name='新用户')

    try:
        await asyncio.wait_for(adapter.send_mail(envelope), 20)
        check('建号失败 -> 451', False, '没有抛异常')
    except RuntimeError as e:
        check(f'建号失败 -> 451 -> {str(e)[:58]}', str(e).startswith('451'))
    except asyncio.TimeoutError:
        check('建号失败 -> 451', False, '超时（卡住了）')

    check('失败后 __creating_users 已清空', adapter._Adapter__creating_users == [],
          f'残留 {adapter._Adapter__creating_users}')

    started = time.monotonic()
    try:
        await asyncio.wait_for(adapter.send_mail(envelope), 20)
    except (RuntimeError, asyncio.TimeoutError):
        pass
    elapsed = time.monotonic() - started
    check(f'同一地址第二次立即失败而不是卡死（{elapsed:.2f}s）', elapsed < 10, f'耗时 {elapsed:.2f}s')


async def test_handler_mapping():
    print('\n[4] 端到端：异常字符串 -> SMTP 响应')
    import smtp_server

    class StubAdapter:
        def __init__(self, exc):
            self.exc = exc

        async def send_mail(self, envelope):
            raise self.exc

    original = smtp_server.adapter
    try:
        smtp_server.adapter = StubAdapter(RuntimeError('451 4.4.2 Cannot reach the Graph API'))
        check('451 异常 -> "451 4.4.2 ..."',
              (await smtp_server.Handler.handle_DATA(None, None, make_envelope())).startswith('451 4.4.2'))
        smtp_server.adapter = StubAdapter(ValueError('something unexpected'))
        check('未知异常 -> 550 Service error',
              (await smtp_server.Handler.handle_DATA(None, None, make_envelope())).startswith('550 Service error'))
    finally:
        smtp_server.adapter = original


async def test_malformed_from():
    print('\n[5] From 头解析不出地址 -> 550（不拿空地址去跑建号流程）')
    module, adapter = make_adapter()
    bad = Envelope()
    bad.content = (b'From: \xe9\xba\xa6\xe9\xba\xa62\xe5\x8f\xb7 <x@y>\r\n'
                   b'To: a@b\r\nSubject: t\r\n\r\nhi\r\n')
    result = await adapter.send_mail(bad)
    check(f'畸形 From -> 550（实得 {str(result)[:46]}）', str(result).startswith('550'))


async def main():
    await test_network_failure_is_transient()
    await test_http_status_mapping()
    await test_user_creation_leak()
    await test_malformed_from()
    await test_handler_mapping()
    print(f'\n结果: {len(PASSED)} 通过, {len(FAILURES)} 失败')
    if FAILURES:
        for f in FAILURES:
            print(f'  失败: {f}')
        sys.exit(1)


if __name__ == '__main__':
    os.environ.setdefault('APP_MICROSOFT_EXCHANGE_ONLINE_CERTIFICATE_PATH', '/tmp/nonexistent.pfx')
    asyncio.run(main())
