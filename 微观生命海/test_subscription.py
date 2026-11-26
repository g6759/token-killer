import base64
import hashlib
import json
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import subscription as subscription_module
from subscription import Subscription, AuthRequired, OAuthError, validate_id_token, b64

class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store_patches = []
        if os.name != 'nt':
            self.vault = {}
            def read(directory):
                value = self.vault.get(str(Path(directory).resolve()))
                return json.loads(json.dumps(value)) if value is not None else None
            def write(directory, record):
                self.vault[str(Path(directory).resolve())] = json.loads(json.dumps(record))
            self.store_patches = [patch.object(subscription_module, 'secure_store_available', return_value=(True, None)),
                                  patch.object(subscription_module, 'read_secure_record', side_effect=read),
                                  patch.object(subscription_module, 'write_secure_record', side_effect=write)]
            for item in self.store_patches: item.start()
        self.tokens = {'access_token': 'test-private-access', 'refresh_token': 'old-refresh', 'id_token': 'id', 'token_type': 'Bearer', 'expires_in': 3600, 'scope': 'openid chatgpt.tokens.use.direct'}
        self.calls = []
        def fetch(url, **kw):
            self.calls.append((url, kw))
            return dict(self.tokens)
        self.auth = Subscription(self.temp.name, fetch=fetch, validate=lambda *args: {'sub': 'user', 'email': 'test@example.invalid'})
    def tearDown(self):
        self.auth.close_auth()
        self.temp.cleanup()
        for item in reversed(self.store_patches): item.stop()
    def connect(self):
        result = self.auth.start_auth()
        values = parse_qs(urlparse(result['url']).query)
        self.auth.finish_auth({'state': values['state'], 'code': ['code'], 'client_id': ['oaiapp_test']})
        return values
    def test_pkce_gate_dpapi_and_confirmation(self):
        values = self.connect()
        self.assertEqual(values['client_id'], ['dynamic_agent_client'])
        self.assertEqual(values['code_challenge_method'], ['S256'])
        form = self.calls[0][1]['form']
        self.assertEqual(values['code_challenge'][0], b64(hashlib.sha256(form['code_verifier'].encode()).digest()))
        self.assertEqual(form['client_id'], 'oaiapp_test')
        with self.assertRaises(AuthRequired): self.auth.access_token()
        with self.assertRaises(ValueError): self.auth.confirm_limit(100)
        with self.assertRaises(ValueError): self.auth.confirm_limit(90, False)
        self.auth.confirm_limit(90)
        self.assertEqual(self.auth.access_token(), 'test-private-access')
        if os.name == 'nt':
            self.assertNotIn(b'test-private-access', self.auth.path.read_bytes())
        else:
            self.assertFalse(self.auth.path.exists())
        self.assertNotIn('test-private-access', json.dumps(self.auth.status()))
    def test_explicit_consent_only_on_existing_registration(self):
        first = parse_qs(urlparse(self.auth.start_auth(enable_plan=True)['url']).query)
        self.assertNotIn('prompt', first)
        self.connect()
        returning = parse_qs(urlparse(self.auth.start_auth(enable_plan=True)['url']).query)
        self.assertEqual(returning['client_id'], ['oaiapp_test'])
        self.assertEqual(returning['prompt'], ['consent'])
    def test_region_error_callback_is_visible_and_nonretryable(self):
        values = parse_qs(urlparse(self.auth.start_auth()['url']).query)
        with self.assertRaises(OAuthError):
            self.auth.finish_auth({'state': values['state'], 'error': ['unsupported_country_region_territory']})
        status = self.auth.status()
        self.assertEqual(status['auth_state'], 'AUTH_REQUIRED')
        self.assertIn('已停止自动重试', status['error_message'])
        self.assertFalse(status['ready'])
    def test_state_single_use_and_identity_client_mismatch(self):
        values = self.connect()
        with self.assertRaises(OAuthError): self.auth.finish_auth({'state': values['state'], 'code': ['replay']})
        self.auth.start_auth()
        with self.assertRaises(OAuthError): self.auth.finish_auth({'state': ['wrong'], 'code': ['code']})
        self.assertIsNotNone(self.auth.pending)
        state = self.auth.pending['state']
        with self.assertRaises(OAuthError): self.auth.finish_auth({'state': [state], 'code': ['code'], 'client_id': ['different']})
    def test_rotating_refresh_and_scope_revocation(self):
        self.connect(); self.auth.confirm_limit(90)
        account = self.auth._account(); account['expires_at'] = 0; self.auth._save()
        self.tokens.update(access_token='new-access', refresh_token='new-refresh', scope='openid')
        with self.assertRaises(AuthRequired): self.auth.access_token()
        self.assertEqual(self.auth._account()['refresh_token'], 'new-refresh')
        self.assertNotIn('scope', self.calls[-1][1]['form'])
    def test_terminal_refresh_clears_but_transient_keeps(self):
        self.connect(); self.auth.confirm_limit(90)
        self.auth._account()['expires_at'] = 0
        self.auth._save()
        def bad(*args, **kwargs): raise OAuthError('temporary', 503)
        self.auth.fetch = bad
        with self.assertRaises(OAuthError): self.auth.access_token()
        self.assertIn('refresh_token', self.auth._account())
        def revoked(*args, **kwargs): raise OAuthError('invalid_grant', 400)
        self.auth.fetch = revoked
        with self.assertRaises(AuthRequired): self.auth.access_token()
        self.assertNotIn('refresh_token', self.auth._account())
        self.assertEqual(self.auth._account()['client_id'], 'oaiapp_test')
    def test_other_instance_immediately_observes_auth_and_confirmation(self):
        other = Subscription(self.temp.name)
        self.assertFalse(other.status()['connected'])
        self.connect()
        self.assertTrue(other.status()['connected'])
        other.confirm_limit(80)
        self.assertEqual(self.auth.status()['weekly_limit_percent'], 80)
    def test_two_instances_refresh_once_under_file_lock(self):
        self.connect(); self.auth.confirm_limit(80)
        self.auth._account()['expires_at'] = 0; self.auth._save()
        count = []
        def refresh(url, **kwargs):
            count.append(1); time.sleep(0.1)
            return {**self.tokens, 'access_token': 'rotated', 'refresh_token': 'rotated-refresh'}
        first = Subscription(self.temp.name, fetch=refresh, validate=self.auth.validate)
        second = Subscription(self.temp.name, fetch=refresh, validate=self.auth.validate)
        results = []
        workers = [threading.Thread(target=lambda auth=auth: results.append(auth.access_token())) for auth in (first, second)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(5)
        self.assertEqual(results, ['rotated', 'rotated'])
        self.assertEqual(len(count), 1)
    def test_verified_jwt_rejects_nonce_audience_signature(self):
        from cryptography.hazmat.primitives.asymmetric import rsa, padding
        from cryptography.hazmat.primitives import hashes
        key = rsa.generate_private_key(65537, 2048)
        public = key.public_key().public_numbers()
        jwk = {'kid': 'test', 'kty': 'RSA', 'e': b64(public.e.to_bytes(3, 'big')), 'n': b64(public.n.to_bytes(256, 'big'))}
        def fetch(url):
            return {'keys': [jwk]} if url.endswith('jwks.json') else {'issuer': 'https://auth.openai.com', 'jwks_uri': 'https://auth.openai.com/.well-known/jwks.json'}
        body = b64(json.dumps({'alg': 'RS256', 'kid': 'test'}).encode()) + '.' + b64(json.dumps({'iss': 'https://auth.openai.com', 'aud': 'client', 'sub': 'user', 'exp': time.time()+600, 'nonce': 'nonce'}).encode())
        token = body + '.' + b64(key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256()))
        self.assertEqual(validate_id_token(token, 'client', 'nonce', fetch)['sub'], 'user')
        with self.assertRaises(OAuthError): validate_id_token(token, 'other', 'nonce', fetch)
        with self.assertRaises(OAuthError): validate_id_token(token, 'client', 'wrong', fetch)

class PortableSecureStoreTests(unittest.TestCase):
    def test_posix_os_keyrings_keep_tokens_out_of_files(self):
      for platform, module in [('linux', 'SecretService'), ('darwin', 'macOS')]:
        with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
            vault = {}
            backend = type('Keyring', (), {'__module__': 'keyring.backends.' + module,
                'get_password': lambda self, service, user: vault.get((service, user)),
                'set_password': lambda self, service, user, value: vault.__setitem__((service, user), value)})
            service_module = types.ModuleType('keyring.backends.' + module)
            service_module.Keyring = backend
            package = types.ModuleType('keyring')
            package.__path__ = []
            package.get_keyring = lambda: self.fail('generic backend selection is forbidden')
            backends = types.ModuleType('keyring.backends')
            backends.__path__ = []
            posix = types.SimpleNamespace(name='posix')
            fcntl = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *args: None)
            with patch.object(subscription_module, 'os', posix), patch.object(subscription_module.sys, 'platform', platform), \
                    patch.dict('sys.modules', {'keyring': package, 'keyring.backends': backends,
                                               'keyring.backends.' + module: service_module, 'fcntl': fcntl}):
                auth = Subscription(directory)
                auth.record['accounts'] = {'test': {'access_token': 'private-token'}}
                auth._save()
                self.assertEqual(subscription_module.read_secure_record(directory)['accounts']['test']['access_token'], 'private-token')
                self.assertFalse((Path(directory) / 'subscription.dpapi').exists())
                self.assertNotIn('private-token', (Path(directory) / 'subscription.lock').read_text())

    def test_unsupported_or_unavailable_keyring_keeps_offline_status_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            package = types.ModuleType('keyring')
            package.__path__ = []
            package.get_keyring = lambda: self.fail('generic backend selection is forbidden')
            backends = types.ModuleType('keyring.backends')
            backends.__path__ = []
            posix = types.SimpleNamespace(name='posix')
            with patch.object(subscription_module, 'os', posix), patch.object(subscription_module.sys, 'platform', 'linux'), \
                    patch.dict('sys.modules', {'keyring': package, 'keyring.backends': backends,
                                               'keyring.backends.SecretService': None}):
                auth = Subscription(directory)
                self.assertFalse(auth.status()['ready'])
                self.assertIn('系统钥匙串', auth.status()['error_message'])
                with self.assertRaises(AuthRequired):
                    auth.start_auth()
                self.assertFalse((Path(directory) / 'subscription.dpapi').exists())

    def test_secret_service_without_session_still_allows_offline_state(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = type('Keyring', (), {'get_password': lambda *args: (_ for _ in ()).throw(ValueError('no session'))})
            service_module = types.ModuleType('keyring.backends.SecretService')
            service_module.Keyring = backend
            package = types.ModuleType('keyring'); package.__path__ = []
            backends = types.ModuleType('keyring.backends'); backends.__path__ = []
            with patch.object(subscription_module, 'os', types.SimpleNamespace(name='posix')), \
                    patch.object(subscription_module.sys, 'platform', 'linux'), \
                    patch.dict('sys.modules', {'keyring': package, 'keyring.backends': backends,
                                               'keyring.backends.SecretService': service_module}):
                auth = Subscription(directory)
                self.assertFalse(auth.status()['ready'])
                self.assertIn('系统钥匙串', auth.status()['error_message'])

if __name__ == '__main__': unittest.main()
