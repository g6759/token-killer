import io
import json
import os
import tempfile
import time
import types
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import subscription_worker as worker


def token(**overrides):
    claims = {'iss': worker.auth.ISSUER, 'aud': worker.auth.RESOURCE,
              'client_id': 'oaiapp_games', 'scope': 'chatgpt.tokens.use.direct',
              'sub': 'user', 'exp': time.time() + 3600}
    claims.update(overrides)
    return 'header.' + worker.auth.b64(json.dumps(claims).encode()) + '.signature'


class FakeSubscription:
    def __init__(self):
        self.record = {'app_name': worker.APP_NAME, 'active': 'oaiapp_games'}
        self.directory = worker.ROOT
        self.value = token()
        self.state = {'ready': True, 'connected': True, 'plan_permission': True,
                      'limit_confirmed': True, 'weekly_limit_percent': 90,
                      'email': 'private@example.invalid', 'accounts': [{'id': 'private'}]}
        self.calls = []

    def status(self):
        return self.state

    def access_token(self):
        return self.value

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        return {'models': [{'slug': 'gpt-6.2-sol', 'visibility': 'list'},
                           {'slug': 'gpt-6.2-luna', 'visibility': 'list'}]}


def stream(*events):
    return io.BytesIO(b''.join(('data: ' + json.dumps(item) + '\n\n').encode() for item in events))


def completed(text='{}'):
    return {'type': 'response.completed', 'response': {'status': 'completed',
            'model': 'gpt-6.2-luna', 'usage': {'input_tokens': 11, 'output_tokens': 6},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.subscription = FakeSubscription()
        self.sub_patch = patch.object(worker, 'get_subscription', return_value=self.subscription)
        self.disk_patch = patch.object(worker, '_check_disk')
        self.quota_patch = patch.object(worker, '_require_ordinary_plan')
        self.sub_patch.start()
        self.disk_patch.start()
        self.quota_patch.start()

    def tearDown(self):
        self.sub_patch.stop()
        self.disk_patch.stop()
        self.quota_patch.stop()

    def test_status_is_local_and_excludes_account_data(self):
        status = worker.get_setup_status()
        self.assertTrue(status['ready'])
        self.assertNotIn('private', json.dumps(status))
        self.assertEqual(self.subscription.calls, [])

    def test_confirmation_requires_below_100_and_real_number(self):
        for percent in (True, float('nan'), float('inf'), 0, 100, '90'):
            with self.subTest(percent=percent), self.assertRaises((ValueError, worker.PreflightError)):
                worker.confirm_setup(percent)

    def test_no_inference_until_ready_and_known_application(self):
        for field, value in [('ready', False), ('weekly_limit_percent', 100)]:
            with self.subTest(field=field):
                old = self.subscription.state[field]
                self.subscription.state[field] = value
                with patch.object(worker, '_open_response') as post:
                    with self.assertRaises(worker.PreflightError):
                        worker.SubscriptionDriver().infer('puzzle')
                    post.assert_not_called()
                self.subscription.state[field] = old
        self.subscription.record['app_name'] = 'micro_life_sea'
        with self.assertRaises(worker.PreflightError):
            worker.SubscriptionDriver().infer('puzzle')

    def test_api_key_and_wrong_client_or_scope_never_reach_network(self):
        for value in ('sk-private', token(client_id='different'), token(scope='openid'),
                      token(aud='different'), token(exp=0)):
            self.subscription.value = value
            with self.subTest(value=value[:8]), patch.object(worker, '_open_response') as post:
                with self.assertRaises(worker.PreflightError):
                    worker.SubscriptionDriver().infer('puzzle')
                post.assert_not_called()
        self.assertEqual(self.subscription.calls, [])

    def test_success_uses_same_token_catalog_stream_and_no_tools(self):
        order = []
        def open_response(request, timeout):
            order.append('post')
            self.assertEqual(request.full_url, worker.auth.RESOURCE + '/responses')
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + self.subscription.value)
            body = json.loads(request.data)
            self.assertFalse(body['store'])
            self.assertTrue(body['stream'])
            self.assertEqual(body['tools'], [])
            self.assertEqual(body['reasoning']['effort'], 'high')
            self.assertEqual(body['model'], 'gpt-6.2-luna')
            self.assertEqual(body['text']['format']['type'], 'json_schema')
            event = completed()
            event["response"].update(id="resp_measured", reasoning={"effort": "high"})
            event["response"]["usage"].update(input_tokens_details={"cached_tokens": 4},
                                               output_tokens_details={"reasoning_tokens": 3})
            return stream(event)
        with patch.object(worker, '_open_response', side_effect=open_response):
            result = worker.SubscriptionDriver().infer('puzzle', on_ready=lambda: order.append('ready'))
        self.assertEqual(order, ['ready', 'post'])
        self.assertEqual(result['text'], '{}')
        self.assertEqual(result['usage']['input_tokens'], 11)
        self.assertEqual(result['usage']['cached_input_tokens'], 4)
        self.assertEqual(result['usage']['reasoning_output_tokens'], 3)
        self.assertEqual(result['response_id'], 'resp_measured')
        self.assertEqual(result['reasoning_effort'], 'high')
        self.assertEqual(self.subscription.calls, [worker.auth.RESOURCE + '/models'])

    def test_unknown_model_no_fallback(self):
        with patch.object(worker, '_open_response') as post:
            with self.assertRaises(worker.PreflightError):
                worker.SubscriptionDriver(model='unknown').infer('puzzle')
            post.assert_not_called()

    def test_prepared_model_reuses_catalog_within_same_account_only(self):
        driver = worker.SubscriptionDriver()
        prepared = driver.prepare_model()
        with patch.object(worker, "_open_response", side_effect=lambda *args, **kwargs: stream(completed())) as post:
            driver.infer("puzzle 1", prepared_model=prepared)
            driver.infer("puzzle 2", prepared_model=prepared)
            self.assertEqual(post.call_count, 2)
            self.assertEqual(len(self.subscription.calls), 1)
            self.subscription.record["active"] = "oaiapp_other"
            self.subscription.value = token(client_id="oaiapp_other")
            with self.assertRaises(worker.PreflightError):
                driver.infer("changed account", prepared_model=prepared)
            self.assertEqual(post.call_count, 2)

    def test_cancel_before_post_and_after_ready(self):
        with patch.object(worker, '_open_response') as post:
            with self.assertRaises(worker.PreflightError):
                worker.SubscriptionDriver().infer('puzzle', cancelled=lambda: True)
            post.assert_not_called()
        state = [False]
        with patch.object(worker, '_open_response') as post:
            with self.assertRaises(worker.PreflightError):
                worker.SubscriptionDriver().infer('puzzle', cancelled=lambda: state[0],
                                                  on_ready=lambda: state.__setitem__(0, True))
            post.assert_not_called()

    def test_partial_or_failed_response_is_not_success_and_not_retried(self):
        for events in ([{'type': 'response.output_text.delta', 'delta': '{}'}],
                       [{'type': 'response.failed', 'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}}],
                       [{'type': 'response.incomplete'}]):
            with self.subTest(events=events), patch.object(worker, '_open_response', return_value=stream(*events)) as post:
                with self.assertRaises(worker.DriverError):
                    worker.SubscriptionDriver().infer('puzzle')
                self.assertEqual(post.call_count, 1)

    def test_transport_eof_is_distinguished_from_a_platform_limit(self):
        delta = {'type': 'response.output_text.delta', 'delta': '{}'}
        with patch.object(worker, '_open_response', return_value=stream(delta)) as post:
            with self.assertRaises(worker.ResponseStreamEnded):
                worker.SubscriptionDriver().infer('puzzle')
            post.assert_called_once()
        quota = {'type': 'response.failed', 'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}}
        with patch.object(worker, '_open_response', return_value=stream(quota)):
            with self.assertRaises(worker.DriverError) as caught:
                worker.SubscriptionDriver().infer('puzzle')
        self.assertNotIsInstance(caught.exception, worker.ResponseStreamEnded)

    def test_clean_eof_preserves_a_complete_terminal_frame_without_a_blank_line(self):
        final_frame = ('data: ' + json.dumps(completed()) + '\n').encode()
        with patch.object(worker, '_open_response', return_value=io.BytesIO(final_frame)):
            result = worker.SubscriptionDriver().infer('puzzle')
        self.assertEqual(result['text'], '{}')
        self.assertEqual(result['usage']['input_tokens'], 11)

    def test_refuses_tool_output_and_model_substitution(self):
        wrong = completed()
        wrong['response']['model'] = 'unrequested'
        tool = completed()
        tool['response']['output'] = [{'type': 'function_call'}]
        for event in (wrong, tool):
            with patch.object(worker, '_open_response', return_value=stream(event)):
                with self.assertRaises(worker.DriverError):
                    worker.SubscriptionDriver().infer('puzzle')

    def test_network_exception_does_not_expose_token(self):
        with patch.object(worker, '_open_response', side_effect=OSError(self.subscription.value)):
            with self.assertRaises(worker.ResponseConnectionError) as caught:
                worker.SubscriptionDriver().infer('puzzle')
        self.assertNotIn(self.subscription.value, str(caught.exception))

    def test_http_5xx_keeps_only_bounded_safe_request_metadata(self):
        def http_error(status, headers):
            body = io.BytesIO(json.dumps({'error': {'code': 'server_error',
                'message': 'private-payload-message'}}).encode())
            return worker.urllib.error.HTTPError('https://example.invalid/responses', status,
                                                  'private-reason', headers, body)

        cases = [
            (503, {'x-request-id': 'req_ABC-123', 'retry-after': '23',
                   'authorization': self.subscription.value}, True),
            (502, {'x-request-id': 'req_bad\nsecret', 'retry-after': '9\rsecret'}, False),
            (503, {'x-request-id': 'x' * 129, 'retry-after': '9' * 65}, False),
            (429, {'x-request-id': 'req_ABC-123', 'retry-after': '23'}, False),
        ]
        for status, headers, expected in cases:
            with self.subTest(status=status, expected=expected), patch.object(
                    worker, '_open_response', side_effect=http_error(status, headers)) as post:
                with self.assertRaises(worker.DriverError) as caught:
                    worker.SubscriptionDriver().infer('puzzle')
                post.assert_called_once()
                message = str(caught.exception)
                self.assertEqual('x-request-id=req_ABC-123' in message, expected)
                self.assertEqual('retry-after=23' in message, expected)
                self.assertNotIn(self.subscription.value, message)
                self.assertNotIn('private-payload-message', message)
                self.assertNotIn('private-reason', message)
                self.assertNotIn('secret', message)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch.object(worker, '_peek_cache', None)
        self.cache.start()
        self.store_patches = []
        if os.name != 'nt':
            self.vault = {}
            def read(directory):
                value = self.vault.get(str(Path(directory).resolve()))
                return json.loads(json.dumps(value)) if value is not None else None
            def write(directory, record):
                self.vault[str(Path(directory).resolve())] = json.loads(json.dumps(record))
            self.store_patches = [patch.object(worker.auth, 'secure_store_available', return_value=(True, None)),
                                  patch.object(worker.auth, 'read_secure_record', side_effect=read),
                                  patch.object(worker.auth, 'write_secure_record', side_effect=write)]
            for item in self.store_patches: item.start()

    def tearDown(self):
        for item in reversed(self.store_patches): item.stop()
        self.cache.stop()

    def test_posix_status_poll_uses_one_keyring_read_until_pending_changes(self):
        reads = []
        statuses = [worker._safe_status({'connected': False, 'pending': False}),
                    worker._safe_status({'connected': True, 'pending': True})]
        def read():
            reads.append(1)
            return statuses[min(len(reads) - 1, 1)]
        fake_subscription = types.SimpleNamespace(pending=False)
        with patch.object(worker, 'os', types.SimpleNamespace(name='posix')), \
                patch.object(worker, '_subscription', fake_subscription), \
                patch.object(worker, '_peek_cache', None), \
                patch.object(worker, '_read_setup_status', side_effect=read):
            self.assertFalse(worker.peek_setup_status()['connected'])
            self.assertFalse(worker.peek_setup_status()['connected'])
            self.assertEqual(len(reads), 1)
            fake_subscription.pending = True
            self.assertTrue(worker.peek_setup_status()['connected'])
            self.assertEqual(len(reads), 2)

    def test_posix_peek_reads_secure_store_once_per_cold_state(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch) / 'private'
            posix = types.SimpleNamespace(name='posix')
            with patch.object(worker, 'os', posix), \
                    patch.object(worker, '_data_dir', return_value=directory), \
                    patch.object(worker.auth, 'read_secure_record', return_value=None) as read, \
                    patch.object(worker.auth, 'secure_store_available') as probe:
                self.assertFalse(worker.peek_setup_status()['connected'])
                self.assertFalse(worker.peek_setup_status()['connected'])
                read.assert_called_once_with(directory)
                probe.assert_not_called()
            self.assertFalse(directory.exists())

    def test_unconnected_probe_does_not_create_the_settings_directory(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch) / "private"
            with patch.object(worker, "_data_dir", return_value=directory):
                self.assertFalse(worker.peek_setup_status()["connected"])
            self.assertFalse(directory.exists())

    def test_game_registration_is_independent_and_dpapi_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = worker.auth.Subscription(directory, app_name=worker.APP_NAME,
                callback_message='research games connected',
                fetch=lambda *args, **kwargs: {'access_token': 'private-app-token',
                    'id_token': 'fake-id', 'token_type': 'Bearer', 'expires_in': 3600,
                    'scope': 'openid chatgpt.tokens.use.direct'},
                validate=lambda *args: {'sub': 'fake-user'})
            try:
                values = parse_qs(urlparse(auth.start_auth(enable_plan=True)['url']).query)
                self.assertEqual(values['agent_name_hint'], [worker.APP_NAME])
                self.assertEqual(values['client_id'], ['dynamic_agent_client'])
                self.assertNotIn('micro_life_sea', json.dumps(values))
                auth.finish_auth({'state': values['state'], 'code': ['fake'], 'client_id': ['oaiapp_games']})
                self.assertFalse(auth.status()['ready'])
                auth.confirm_limit(90)
                self.assertTrue(auth.status()['ready'])
                if os.name == 'nt':
                    self.assertNotIn(b'private-app-token', auth.path.read_bytes())
                else:
                    self.assertFalse(auth.path.exists())
                self.assertEqual(auth.record['app_name'], worker.APP_NAME)
                self.assertEqual(auth.record['accounts']['oaiapp_games']['limit_confirmation']['source'],
                                 'user_attestation_not_api_read')
            finally:
                auth.close_auth()

    def test_private_dir_is_outside_project_and_unknown_app_is_rejected(self):
        self.assertFalse(worker._data_dir().is_relative_to(worker.ROOT))
        with tempfile.TemporaryDirectory() as directory:
            other = worker.auth.Subscription(directory)
            try:
                with patch.object(worker, '_subscription', None), patch.object(worker, '_data_dir', return_value=Path(directory)):
                    with self.assertRaises(worker.PreflightError):
                        worker.get_subscription()
            finally:
                other.close_auth()


if __name__ == '__main__':
    unittest.main()
