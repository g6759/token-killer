"""App-owned Sign in with ChatGPT. Never reads Codex's credentials or usage APIs."""
from __future__ import annotations
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

ISSUER = 'https://auth.openai.com'
AUTHORIZE = ISSUER + '/api/accounts/authorize'
TOKEN = ISSUER + '/api/accounts/oauth/token'
RESOURCE = 'https://api.openai.com/v1'
USAGE_URL = 'https://chatgpt.com/settings/usage'
APP_NAME = 'micro_life_sea'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
TERMINAL_REFRESH = {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'}
NONRETRYABLE_AUTH_CODES = {'unsupported_country_region_territory', 'request_forbidden'}

def auth_error_message(code):
    if code == 'unsupported_country_region_territory':
        return 'OpenAI 不支持当前请求所在的国家、地区或领土，已停止自动重试。请按官方支持范围处理；应用无法解除此限制。'
    if code == 'request_forbidden':
        return 'OpenAI 拒绝了此授权请求，已停止自动重试。请查看官方错误页；若提示地区不受支持，应用无法解除此限制。'
    return code

class AuthRequired(RuntimeError):
    pass

class OAuthError(RuntimeError):
    def __init__(self, code, status=0):
        super().__init__(auth_error_message(code))
        self.code, self.status = code, status

def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

def unb64(data):
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))

def http_json(url, form=None, bearer=None):
    headers = {'Accept': 'application/json', 'User-Agent': APP_NAME + '/0.1.0'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    if form is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = urllib.request.Request(url, data=data, headers=headers)
    # Do not send OAuth secrets to a redirected destination.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(2_000_000)
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(100_000))
            value = payload.get('error', 'http_error')
            code = value.get('code', 'http_error') if isinstance(value, dict) else value
        except Exception:
            code = 'http_error'
        raise OAuthError(str(code)[:100], exc.code) from None

def dpapi(data: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('This build requires Windows DPAPI; plaintext auth storage is forbidden.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]
    source_buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), 'Micro Life Sea OAuth', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)

def _keyring(directory):
    """Return only an OS credential store, never a plaintext/fallback backend."""
    try:
        if sys.platform == 'darwin':
            from keyring.backends.macOS import Keyring
        elif sys.platform.startswith('linux'):
            from keyring.backends.SecretService import Keyring
        else:
            raise RuntimeError('未找到受支持的系统钥匙串后端。')
        backend = Keyring()
        service = 'research-games-oauth-' + hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()
        return backend, service
    except Exception as exc:
        raise RuntimeError('订阅连接不可用：需要 keyring 和可用的系统钥匙串（macOS Keychain 或 Linux Secret Service）。') from exc

def secure_store_available(directory):
    if os.name == 'nt':
        return True, None
    try:
        backend, service = _keyring(directory)
        # Importability alone does not imply a live Keychain/Secret Service session.
        backend.get_password(service, 'account-record')
        return True, None
    except Exception:
        return False, '订阅连接不可用：需要 keyring 和可用的系统钥匙串（macOS Keychain 或 Linux Secret Service）。'

def read_secure_record(directory):
    directory = Path(directory).resolve()
    if os.name == 'nt':
        path = directory / 'subscription.dpapi'
        return json.loads(dpapi(path.read_bytes(), True)) if path.exists() else None
    backend, service = _keyring(directory)
    value = backend.get_password(service, 'account-record')
    return json.loads(value) if value is not None else None

def write_secure_record(directory, record):
    directory = Path(directory).resolve()
    encoded = json.dumps(record)
    if os.name == 'nt':
        path = directory / 'subscription.dpapi'
        encrypted = dpapi(encoded.encode())
        temporary = path.with_name('subscription-' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_bytes(encrypted)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        backend, service = _keyring(directory)
        backend.set_password(service, 'account-record', encoded)

def validate_id_token(token, client_id, nonce=None, fetch=http_json):
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes
    parts = token.split('.')
    if len(parts) != 3:
        raise OAuthError('invalid_id_token')
    header, claims = (json.loads(unb64(part)) for part in parts[:2])
    if header.get('alg') != 'RS256':
        raise OAuthError('unsupported_id_token_algorithm')
    discovery = fetch(ISSUER + '/.well-known/openid-configuration')
    jwks_uri = discovery.get('jwks_uri', '')
    if discovery.get('issuer') != ISSUER or urllib.parse.urlparse(jwks_uri).netloc != 'auth.openai.com' or not jwks_uri.startswith('https://'):
        raise OAuthError('invalid_discovery')
    keys = fetch(jwks_uri).get('keys', [])
    key = next((k for k in keys if k.get('kid') == header.get('kid') and k.get('kty') == 'RSA'), None)
    if not key:
        raise OAuthError('unknown_signing_key')
    public = rsa.RSAPublicNumbers(int.from_bytes(unb64(key['e']), 'big'), int.from_bytes(unb64(key['n']), 'big')).public_key()
    public.verify(unb64(parts[2]), (parts[0] + '.' + parts[1]).encode(), padding.PKCS1v15(), hashes.SHA256())
    audience = claims.get('aud', [])
    audience = [audience] if isinstance(audience, str) else audience
    if claims.get('iss') != ISSUER or client_id not in audience or not claims.get('sub') or claims.get('exp', 0) <= time.time():
        raise OAuthError('invalid_identity_claims')
    if len(audience) > 1 and claims.get('azp') != client_id:
        raise OAuthError('invalid_authorized_party')
    if claims.get('nbf', 0) > time.time() + 30 or claims.get('iat', 0) > time.time() + 30:
        raise OAuthError('invalid_identity_time')
    if nonce is not None and not secrets.compare_digest(str(claims.get('nonce', '')), nonce):
        raise OAuthError('invalid_nonce')
    return claims

class Subscription:
    def __init__(self, data_dir, *, fetch=http_json, validate=validate_id_token,
                 app_name=APP_NAME, callback_message=None):
        self.app_name = app_name
        self.callback_message = callback_message or 'Connected. Return to Micro Life Sea and confirm your app limit in official ChatGPT settings.'
        self.directory = Path(data_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'subscription.dpapi'
        self.lock = threading.RLock()
        self._transaction_depth = 0
        self.fetch, self.validate = fetch, validate
        self.pending = None
        self.server = None
        self.last_error = None
        self.record = {'host_id': 'urn:uuid:' + str(uuid.uuid4()), 'accounts': {}, 'active': None,
                       'app_name': app_name}
        self.available, self.capability_error = secure_store_available(self.directory)
        if self.available:
            try:
                with self._transaction():
                    if read_secure_record(self.directory) is None:
                        self._save()
            except Exception:
                self.available = False
                self.capability_error = '订阅连接不可用：无法读取或写入系统安全存储。'

    @contextmanager
    def _transaction(self):
        """Serialize only credential transactions, not game/UI instances."""
        if not self.available:
            raise AuthRequired(self.capability_error)
        with self.lock:
            if self._transaction_depth:
                self._transaction_depth += 1
                try:
                    yield
                finally:
                    self._transaction_depth -= 1
                return
            with (self.directory / 'subscription.lock').open('a+b') as guard:
                if guard.seek(0, 2) == 0:
                    guard.write(b'\0')
                    guard.flush()
                deadline = time.monotonic() + 60
                while True:
                    guard.seek(0)
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Another instance is updating the app session; try again.') from None
                        time.sleep(0.05)
                self._transaction_depth = 1
                try:
                    stored = read_secure_record(self.directory)
                    if stored is not None:
                        self.record = stored
                    yield
                finally:
                    self._transaction_depth = 0
                    if os.name == 'nt':
                        guard.seek(0)
                        msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)

    def _save(self):
        write_secure_record(self.directory, self.record)

    def _account(self):
        return self.record['accounts'].get(self.record.get('active'))

    def status(self):
        if not self.available:
            return self._unavailable_status()
        try:
            with self._transaction():
                account = self._account() or {}
                permission = 'chatgpt.tokens.use.direct' in account.get('scopes', [])
                confirmed = bool(account.get('limit_confirmation'))
                connected = bool(account.get('access_token'))
                return {'connected': connected, 'ready': connected and permission and confirmed,
                        'plan_permission': permission, 'limit_confirmed': confirmed,
                        'weekly_limit_percent': account.get('limit_confirmation', {}).get('percent'),
                        'active_account': self.record.get('active'), 'email': account.get('email'),
                        'accounts': [{'id': key, 'email': val.get('email')} for key, val in self.record['accounts'].items()],
                        'pending': bool(self.pending), 'error': self.last_error,
                        'error_message': auth_error_message(self.last_error) if self.last_error else None,
                        'auth_state': 'AUTH_REQUIRED' if self.last_error in NONRETRYABLE_AUTH_CODES else None,
                        'usage_url': USAGE_URL}
        except Exception:
            self.capability_error = '订阅连接不可用：无法读取系统安全存储。'
            return self._unavailable_status()

    def _unavailable_status(self):
        return {'connected': False, 'ready': False, 'plan_permission': False,
                'limit_confirmed': False, 'weekly_limit_percent': None,
                'active_account': None, 'email': None, 'accounts': [],
                'pending': False, 'error': 'secure_store_unavailable',
                'error_message': self.capability_error, 'auth_state': 'AUTH_REQUIRED',
                'usage_url': USAGE_URL}

    def start_auth(self, account_id=None, enable_plan=False):
        self.close_auth()
        with self._transaction():
            account_id = account_id or self.record.get('active')
            account = self.record['accounts'].get(account_id, {})
            if account_id and not account:
                raise ValueError('Unknown account registration')
            outer = self
            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_GET(self):
                    parsed = urllib.parse.urlparse(self.path)
                    if parsed.path != '/auth/callback':
                        self.send_error(404)
                        return
                    try:
                        outer.finish_auth(urllib.parse.parse_qs(parsed.query))
                        body, status = outer.callback_message, 200
                    except Exception:
                        body, status = 'Sign-in failed. Return to the application and start a fresh sign-in.', 400
                    raw = body.encode()
                    self.send_response(status)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    threading.Thread(target=outer.close_auth, daemon=True).start()
            server = HTTPServer(('127.0.0.1', 0), Callback)
            self.server = server
            uri = f'http://127.0.0.1:{server.server_port}/auth/callback'
            pending = {'state': secrets.token_urlsafe(32), 'nonce': secrets.token_urlsafe(32), 'verifier': secrets.token_urlsafe(64), 'redirect_uri': uri, 'expires': time.time()+600, 'account_id': account_id, 'client_id': account.get('client_id') or self.record.get('incomplete_client_id')}
            self.pending = pending
            params = {'client_id': pending['client_id'] or 'dynamic_agent_client', 'ext_agent_host_id': self.record['host_id'], 'response_type': 'code', 'redirect_uri': uri, 'scope': SCOPES, 'resource': RESOURCE, 'state': pending['state'], 'nonce': pending['nonce'], 'code_challenge_method': 'S256', 'code_challenge': b64(hashlib.sha256(pending['verifier'].encode()).digest())}
            if not pending['client_id']:
                params['agent_name_hint'] = self.app_name
            # Avoid returning a retained ID token to the browser via this app's JSON API.
            # Routine reauthorization without id_token_hint is officially supported.
            # First registration already includes consent. Explicit re-consent is
            # documented only for an existing registration restoring plan access.
            if enable_plan and pending['client_id']:
                params['prompt'] = 'consent'
            self.last_error = None
            threading.Thread(target=server.serve_forever, daemon=True).start()
            timer = threading.Timer(600, lambda: self._expire_attempt(pending))
            timer.daemon = True
            timer.start()
            return {'url': AUTHORIZE + '?' + urllib.parse.urlencode(params), 'expires_in': 600}

    def _expire_attempt(self, pending):
        if self.pending is pending:
            self.close_auth()

    def close_auth(self):
        server, self.server = self.server, None
        self.pending = None
        if server:
            server.shutdown()
            server.server_close()

    def finish_auth(self, query):
        with self._transaction():
            pending = self.pending
            if not pending or time.time() > pending['expires'] or query.get('state') != [pending['state']]:
                raise OAuthError('invalid_state')
            self.pending = None  # One-time consumption, including denied/error callbacks.
            try:
                if query.get('error'):
                    raise OAuthError(query['error'][0])
                issued = query.get('client_id', [pending['client_id']])[0]
                if not issued or issued == 'dynamic_agent_client' or (pending['client_id'] and issued != pending['client_id']):
                    raise OAuthError('invalid_issued_client_id')
                old = self.record['accounts'].get(pending['account_id'], {})
                # Preserve issued registration after code failure for correct reauthorization.
                if not pending['client_id']:
                    self.record['incomplete_client_id'] = issued
                    self._save()
                tokens = self.fetch(TOKEN, form={'grant_type': 'authorization_code', 'client_id': issued, 'code': query['code'][0], 'code_verifier': pending['verifier'], 'redirect_uri': pending['redirect_uri'], 'resource': RESOURCE})
                identity = self.validate(tokens['id_token'], issued, pending['nonce'])
                if old.get('subject') and old['subject'] != identity['sub']:
                    raise OAuthError('account_identity_changed')
                account = {'client_id': issued, 'subject': identity['sub'], 'email': identity.get('email'), 'id_token': tokens['id_token']}
                self._replace_tokens(account, tokens)
                self.record['accounts'][issued] = account
                self.record['active'] = issued
                self.record.pop('incomplete_client_id', None)
                self._save()
            except Exception as exc:
                self.last_error = exc.code if isinstance(exc, OAuthError) else 'sign_in_failed'
                raise

    def _replace_tokens(self, account, tokens):
        if tokens.get('token_type', '').lower() != 'bearer' or not tokens.get('access_token'):
            raise OAuthError('invalid_token_response')
        account.update(access_token=tokens['access_token'], refresh_token=tokens.get('refresh_token', account.get('refresh_token')), expires_at=time.time()+int(tokens.get('expires_in', 3600)), scopes=tokens.get('scope', ' '.join(account.get('scopes', []))).split())
        if tokens.get('id_token'):
            account['id_token'] = tokens['id_token']

    def confirm_limit(self, percent, credits_disabled=True):
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 < percent < 100 or credits_disabled is not True:
            raise ValueError('Confirm an official weekly limit below 100% and credits disabled.')
        with self._transaction():
            account = self._account()
            if not account or not account.get('access_token'):
                raise AuthRequired('Sign in first')
            account['limit_confirmation'] = {'percent': percent, 'credits_disabled': True, 'confirmed_at': time.time(), 'source': 'user_attestation_not_api_read'}
            self._save()
        return self.status()

    def access_token(self):
        with self._transaction():
            if not self.status()['ready']:
                raise AuthRequired('Sign in, enable plan permission, and confirm official app limit and credits disabled.')
            account = self._account()
            if account['expires_at'] <= time.time() + 90:
                try:
                    tokens = self.fetch(TOKEN, form={'grant_type': 'refresh_token', 'client_id': account['client_id'], 'refresh_token': account.get('refresh_token', ''), 'resource': RESOURCE})
                    if tokens.get('id_token'):
                        identity = self.validate(tokens['id_token'], account['client_id'])
                        if identity['sub'] != account['subject']:
                            raise OAuthError('account_identity_changed')
                    self._replace_tokens(account, tokens)
                    self._save()
                except OAuthError as exc:
                    if exc.code in TERMINAL_REFRESH:
                        for key in ('access_token', 'refresh_token', 'id_token', 'limit_confirmation'):
                            account.pop(key, None)
                        self._save()
                        raise AuthRequired('Session expired; sign in again.') from None
                    raise
            if 'chatgpt.tokens.use.direct' not in account.get('scopes', []):
                raise AuthRequired('ChatGPT plan permission is absent')
            return account['access_token']

    def models(self):
        payload = self.fetch(RESOURCE + '/models', bearer=self.access_token())
        return [{'slug': m['slug'], 'display_name': m.get('display_name', m['slug'])} for m in payload.get('models', []) if m.get('visibility') == 'list']
