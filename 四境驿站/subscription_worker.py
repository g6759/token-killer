"""App-owned ChatGPT plan inference, started only by an explicit game action.

Protocol: https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
The official per-app weekly cap (<100%) must be set by the user. Its local
confirmation is an attestation, not a readback of the platform setting.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = 'research_games'
APP_TITLE = '研究游戏：四境驿站与方格子容纳'
_spec = importlib.util.spec_from_file_location('_research_games_oauth', ROOT / '微观生命海' / 'subscription.py')
auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auth)
_subscription = None
_subscription_lock = threading.Lock()
_peek_cache = None
_peek_lock = threading.Lock()
_MIN_DISK = 10 * 1024**3
_MAX_BYTES = 4 * 1024**2


class DriverError(RuntimeError):
    """Sanitized failure; a request may already have been sent. Never auto-retry."""


class PreflightError(DriverError):
    """No inference request has been sent."""


def _require_ordinary_plan():
    # Standalone game entry points may not have imported lab's runner modules.
    runner_path = str(ROOT / '自动运行')
    if runner_path not in sys.path:
        sys.path.insert(0, runner_path)
    from quota import QuotaError, get_shared_gate
    try:
        get_shared_gate().require()
    except QuotaError as exc:
        raise PreflightError(str(exc)) from None


class ResponseStreamEnded(DriverError):
    """The transport ended before returning a validated terminal response."""


class ResponseConnectionError(DriverError):
    """The response could not be read; the submitted request is unconfirmed."""


def _data_dir():
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA')
        if not base:
            raise PreflightError('需要 Windows 用户私有目录和 DPAPI 安全存储。')
        path = Path(base) / 'ResearchGames' / 'ChatGPTPlan'
    elif sys.platform == 'darwin':
        path = Path.home() / 'Library' / 'Application Support' / 'ResearchGames' / 'ChatGPTPlan'
    else:
        path = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'ResearchGames' / 'ChatGPTPlan'
    path = path.resolve()
    if path.is_relative_to(ROOT):
        raise PreflightError('应用授权资料必须保存在项目外的用户私有目录。')
    return path


def _check_disk(directory):
    paths = [ROOT, Path(directory), Path(tempfile.gettempdir()), Path(os.environ.get('SystemRoot', ROOT.anchor))]
    for path in paths:
        while not path.exists() and path != path.parent:
            path = path.parent
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            raise PreflightError('无法读取相关磁盘剩余空间，已停止。') from None
        if free < _MIN_DISK:
            raise PreflightError('相关磁盘可用空间低于 10 GiB，已停止。 / Free disk space below 10 GiB.')


def get_subscription():
    """Lazy, local-only initialization; never reads Codex or another app's auth."""
    global _subscription
    with _subscription_lock:
        if _subscription is None:
            directory = _data_dir()
            _check_disk(directory)
            instance = auth.Subscription(directory, app_name=APP_NAME,
                callback_message='登录成功。请返回研究游戏，并在 ChatGPT 官方用量设置中设置本应用周上限。')
            if not instance.available:
                raise PreflightError(instance.capability_error)
            if instance.record.get('app_name') != APP_NAME:
                raise PreflightError('此授权目录不属于研究游戏，请使用本应用独立登录。')
            _subscription = instance
        return _subscription


def _valid_limit(percent):
    return type(percent) in (int, float) and math.isfinite(percent) and 0 < percent < 100


def get_setup_status():
    return _safe_status(get_subscription().status())


def _safe_status(status):
    safe = {key: status.get(key) for key in ('connected', 'ready', 'plan_permission',
            'limit_confirmed', 'weekly_limit_percent', 'pending', 'error_message', 'auth_state')}
    safe['ready'] = bool(safe['ready'] and _valid_limit(safe['weekly_limit_percent']))
    safe.update(app_name=APP_NAME, app_title=APP_TITLE, usage_url=auth.USAGE_URL,
                limit_source='user_attestation_not_api_read')
    return safe


def peek_setup_status():
    """Read the local app connection state without creating an auth store."""
    global _peek_cache
    if os.name != 'nt':
        with _peek_lock:
            if (_peek_cache is not None and _subscription is not None
                    and bool(_subscription.pending) != bool(_peek_cache.get('pending'))):
                _peek_cache = None
            if _peek_cache is None:
                _peek_cache = _read_setup_status()
            return dict(_peek_cache)
    return _read_setup_status()


def _read_setup_status():
    try:
        directory = _data_dir()
    except PreflightError as exc:
        return _safe_status({'connected': False, 'error_message': str(exc)})
    instance = _subscription
    pending = bool(instance and instance.pending)
    try:
        data = auth.read_secure_record(directory) or {}
        if data.get('app_name') != APP_NAME:
            if data:
                raise ValueError('wrong app')
            account = {}
        else:
            account = data['accounts'].get(data.get('active')) or {}
    except Exception as exc:
        reason = str(exc) if isinstance(exc, RuntimeError) else '本地订阅连接无法读取，请检查系统钥匙串。'
        return _safe_status({'connected': False, 'pending': pending,
                             'error_message': reason})
    limit = account.get('limit_confirmation') or {}
    connected = bool(account.get('access_token'))
    permission = 'chatgpt.tokens.use.direct' in account.get('scopes', [])
    percent = limit.get('percent')
    auth_error = instance.last_error if instance else None
    return _safe_status({
        'connected': connected, 'plan_permission': permission, 'limit_confirmed': bool(limit),
        'ready': connected and permission and bool(limit), 'weekly_limit_percent': percent,
        'pending': pending, 'error_message': auth.auth_error_message(auth_error) if auth_error else None,
    })


def start_setup():
    global _peek_cache
    result = get_subscription().start_auth(enable_plan=True)
    if os.name != 'nt':
        status = _safe_status(get_subscription().status())
        with _peek_lock:
            _peek_cache = status
    return {'url': result['url']}


def confirm_setup(percent):
    global _peek_cache
    if not _valid_limit(percent):
        raise PreflightError('请填入官方设置中大于 0 且低于 100% 的应用周上限。')
    get_subscription().confirm_limit(percent, credits_disabled=True)
    status = get_setup_status()
    if os.name != 'nt':
        with _peek_lock:
            _peek_cache = status
    return status


def _app_token(subscription):
    state = subscription.status()
    if (subscription.record.get('app_name') != APP_NAME or not state.get('ready')
            or not state.get('plan_permission') or not state.get('limit_confirmed')
            or not _valid_limit(state.get('weekly_limit_percent'))):
        raise PreflightError('请先登录研究游戏，授权套餐使用，并确认官方应用周上限低于 100%。')
    try:
        token = subscription.access_token()
        parts = token.split('.')
        if len(parts) != 3:
            raise ValueError('invalid token')
        claims = json.loads(auth.unb64(parts[1]))
        # Tokens originate only in the validated OAuth exchange and DPAPI store.
        # Claims below are a binding check, not a substitute for signature validation.
        if (claims.get('iss') != auth.ISSUER or claims.get('aud') != auth.RESOURCE
                or claims.get('client_id') != subscription.record.get('active')
                or not claims.get('client_id') or claims.get('client_id') == 'dynamic_agent_client'
                or 'chatgpt.tokens.use.direct' not in claims.get('scope', '').split()
                or type(claims.get('exp')) not in (int, float)
                or not math.isfinite(claims['exp']) or claims['exp'] <= time.time()):
            raise ValueError('wrong authorization')
        return token, claims['client_id']
    except (auth.AuthRequired, auth.OAuthError, ValueError, TypeError, AttributeError, KeyError):
        raise PreflightError('本应用的套餐授权无效或已失效，请重新登录；不会使用 API Key 或其他付费凭据。') from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def _open_response(request, timeout):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _response_socket(response):
    return getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)


def _cancelled(callback):
    return bool(callback and (callback.is_set() if hasattr(callback, 'is_set') else callback()))


def _error_message(payload):
    error = payload.get('error', payload) if isinstance(payload, dict) else {}
    code = error.get('code') if isinstance(error, dict) else None
    if code in ('subscription_sharing_usage_limit_exceeded', 'insufficient_quota'):
        return '官方返回本应用套餐额度已达限制，已停止，不切换付费凭据。'
    if code in ('subscription_sharing_usage_unavailable', 'subscription_sharing_user_unavailable'):
        return '官方暂时无法提供本应用套餐用量，已停止，请稍后手动重试。'
    if code in auth.NONRETRYABLE_AUTH_CODES:
        return auth.auth_error_message(code)
    return '官方未完成本轮请求，已停止；结果未知时不会自动重发。'


def _candidate_schema():
    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    rows = {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'integer'}}}
    return obj({'pack_id': {'type': 'string'}, 'parent_hash': {'type': 'string'},
                'solution': {'anyOf': [obj({name: rows}) for name in ('blocks', 'poses')]},
                'handoff': {'type': 'string'}})


class SubscriptionDriver:
    def __init__(self, model=None, *, timeout=180):
        if model is not None and (not isinstance(model, str) or not model):
            raise PreflightError('模型标识无效。')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise PreflightError('请求超时必须是正秒数。')
        self.model, self.timeout = model, timeout

    def _select_model(self, subscription, token):
        try:
            catalog = subscription.fetch(auth.RESOURCE + '/models', bearer=token)
            models = [item['slug'] for item in catalog.get('models', [])
                      if isinstance(item, dict) and item.get('visibility') == 'list' and isinstance(item.get('slug'), str)]
        except Exception:
            raise PreflightError('无法读取本应用授权账户的模型列表，未发出推理请求。') from None
        model = self.model or next((item for name in ('luna', 'sol') for item in models if name in item.lower()), None)
        if not model or model not in models:
            raise PreflightError('账户模型列表中没有指定模型或可用的 Luna / Sol，未切换其他模型。')
        return model

    def prepare_model(self):
        """Resolve a single model for a comparison, bound to this app registration."""
        subscription = get_subscription()
        _check_disk(subscription.directory)
        token, registration = _app_token(subscription)
        return (self._select_model(subscription, token), registration)

    def infer(self, prompt, *, cancelled=None, on_ready=None, schema=None, prepared_model=None,
              large_game_prompt=False):
        prompt_limit = 1600 * 1024 if large_game_prompt else 256 * 1024
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > prompt_limit:
            raise PreflightError('题面为空或超过大小限制。')
        if _cancelled(cancelled):
            raise PreflightError('已取消，未发出模型请求。')
        _require_ordinary_plan()
        subscription = get_subscription()
        _check_disk(subscription.directory)
        token, active_registration = _app_token(subscription)
        if prepared_model is None:
            model = self._select_model(subscription, token)
        else:
            model, registration = prepared_model
            if registration != active_registration or (self.model and model != self.model):
                raise PreflightError('订阅授权或预选模型已变化，本轮未发出。')
        body = {'model': model, 'input': [{'role': 'user', 'content': prompt}],
                'instructions': 'Solve only the supplied puzzle. Return one JSON candidate. Use no tools.',
                'store': False, 'stream': True, 'tools': [], 'reasoning': {'effort': 'high'},
                'text': {'format': {'type': 'json_schema', 'name': 'game_candidate',
                                    'strict': True, 'schema': _candidate_schema() if schema is None else schema}}}
        request = urllib.request.Request(auth.RESOURCE + '/responses',
                  data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + token,
                  'Content-Type': 'application/json', 'Accept': 'text/event-stream', 'User-Agent': APP_NAME + '/0.1.0'})
        if _cancelled(cancelled):
            raise PreflightError('已取消，未发出模型请求。')
        if on_ready:
            on_ready()
        _require_ordinary_plan()
        if _cancelled(cancelled):
            raise PreflightError('已取消，未发出模型请求。')
        try:
            with _open_response(request, timeout=min(45 if large_game_prompt else 15, self.timeout)) as response:
                # High reasoning can be silent for longer than the connect timeout.
                # The collector owns the overall deadline and cancels the socket.
                connection = _response_socket(response)
                if connection is not None:
                    connection.settimeout(self.timeout)
                headers = getattr(response, 'headers', {})
                limits = {}
                for field in ('limit-tokens', 'limit-requests', 'remaining-tokens', 'remaining-requests',
                              'reset-tokens', 'reset-requests'):
                    value = headers.get('x-ratelimit-' + field)
                    if isinstance(value, str) and 0 < len(value) <= 64:
                        limits[field] = value
                reply = self._collect(response, model, subscription.directory, cancelled)
                if limits:
                    reply['rate_limit_headers'] = limits
                return reply
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read(100_000))
            except Exception:
                payload = {}
            details = payload.get('error') if isinstance(payload, dict) else None
            code = details.get('code') if isinstance(details, dict) else None
            label = f'HTTP {exc.code}'
            if isinstance(code, str) and len(code) < 100 and code.replace('_', '').isalnum():
                label += f' {code}'
            if 500 <= exc.code <= 599:
                headers = exc.headers or {}
                request_id = headers.get('x-request-id')
                retry_after = headers.get('retry-after')
                if (isinstance(request_id, str) and len(request_id) <= 128
                        and re.fullmatch(r'[A-Za-z0-9._-]{1,128}', request_id)
                        and token not in request_id):
                    label += f' x-request-id={request_id}'
                if (isinstance(retry_after, str) and len(retry_after) <= 64
                        and re.fullmatch(r'(?:[0-9]{1,10}|[A-Za-z]{3}, [0-9]{2} '
                                         r'[A-Za-z]{3} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT)',
                                         retry_after)):
                    label += f' retry-after={retry_after}'
            raise DriverError(f'{_error_message(payload)} [{label}]') from None
        except TimeoutError:
            raise DriverError('模型请求超时，已停止，不自动重试。') from None
        except DriverError:
            raise
        except Exception as exc:
            reason = getattr(exc, 'reason', exc)
            detail = type(reason).__name__
            error_number = getattr(reason, 'errno', None)
            if isinstance(error_number, int):
                detail += f' {error_number}'
            raise ResponseConnectionError(f'本轮连接中断或响应无效，已停止；请求可能已发出，不自动重试。[{detail}]') from None

    def _collect(self, response, model, directory, cancelled):
        events = queue.Queue(maxsize=64)
        stopped = threading.Event()
        def deliver(value):
            while not stopped.is_set():
                try:
                    events.put(value, timeout=0.2)
                    return
                except queue.Full:
                    pass
        def read():
            total, data = 0, []
            try:
                while not stopped.is_set():
                    line = response.readline(128 * 1024 + 1)
                    if not line:
                        tail = '\n'.join(data)
                        if tail and tail != '[DONE]':
                            try:
                                deliver(json.loads(tail))
                            except json.JSONDecodeError:
                                pass
                        break
                    total += len(line)
                    if len(line) > 128 * 1024 or total > _MAX_BYTES:
                        raise ValueError('stream too large')
                    line = line.decode('utf-8').strip('\r\n')
                    if not line and data:
                        raw = '\n'.join(data)
                        data = []
                        if raw == '[DONE]':
                            break
                        deliver(json.loads(raw))
                    elif line.startswith('data:'):
                        data.append(line[5:].lstrip(' '))
            except Exception:
                deliver({'type': 'error'})
            finally:
                deliver(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline, disk_at = time.monotonic() + self.timeout, time.monotonic() + 45
        try:
            while True:
                if _cancelled(cancelled):
                    raise DriverError('已停止本轮连接；已发出的请求可能已使用部分套餐用量。')
                now = time.monotonic()
                if now >= deadline:
                    raise DriverError('模型请求超时，已停止，不自动重试。')
                if now >= disk_at:
                    try:
                        _check_disk(directory)
                    except PreflightError as exc:
                        raise DriverError(str(exc)) from None
                    disk_at = now + 45
                try:
                    event = events.get(timeout=min(0.2, max(0.001, deadline-now)))
                except queue.Empty:
                    continue
                if event is None:
                    raise ResponseStreamEnded('响应流结束但没有完成回执，未提交候选。')
                if not isinstance(event, dict):
                    raise DriverError('响应事件无效，未提交候选。')
                kind = event.get('type')
                if kind in ('error', 'response.failed', 'response.incomplete'):
                    raise DriverError(_error_message(event.get('response', event)))
                if kind == 'response.output_item.added' and event.get('item', {}).get('type') not in ('message', 'reasoning'):
                    raise DriverError('响应包含未授权工具内容，已停止。')
                if kind == 'response.completed':
                    result = event.get('response', {})
                    if result.get('status') != 'completed' or result.get('model') != model:
                        raise DriverError('响应未完成或模型与请求不一致。')
                    output = result.get('output', [])
                    if any(item.get('type') not in ('message', 'reasoning') for item in output):
                        raise DriverError('响应包含未授权工具内容，未提交候选。')
                    text = '\n'.join(part['text'] for item in output if item.get('type') == 'message'
                                     for part in item.get('content', []) if part.get('type') == 'output_text' and isinstance(part.get('text'), str))
                    if not text.strip():
                        raise DriverError('模型未返回候选正文。')
                    usage = result.get('usage') or {}
                    input_details = usage.get('input_tokens_details') or {}
                    output_details = usage.get('output_tokens_details') or {}
                    safe_usage = {key: value for key, value in usage.items()
                                  if key in ('input_tokens', 'output_tokens', 'total_tokens') and type(value) is int and value >= 0}
                    for key, value in (('cached_input_tokens', input_details.get('cached_tokens')),
                                       ('reasoning_output_tokens', output_details.get('reasoning_tokens'))):
                        if type(value) is int and value >= 0:
                            safe_usage[key] = value
                    effort = (result.get('reasoning') or {}).get('effort')
                    if effort is not None and effort != 'high':
                        raise DriverError('本轮实际推理强度与请求不一致。')
                    response_id = result.get('id')
                    return {'text': text, 'model': model, 'usage': safe_usage,
                            'reasoning_effort': 'high', 'observed_reasoning_effort': effort,
                            'response_id': response_id if isinstance(response_id, str) else None}
        finally:
            stopped.set()
            connection = _response_socket(response)
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            response.close()
            reader.join(timeout=1)
