"""Tests for the production configuration hardening.

Covers the pieces that stop a production deploy from silently running on
development defaults: the fail-fast checks, the startup warnings, the Paystack
key-mode detection, and the logging destination.

These exercise the helpers in config/settings.py directly rather than
re-importing Django with a different environment — re-importing settings
mid-suite would disturb every other test in the run. No network, no
subprocess, no real credentials.

Run with:  python manage.py test shop.tests_production_config
"""
import inspect
import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config import settings as project_settings

GOOD_SECRET = 'a-real-secret-key-long-enough-to-not-be-the-placeholder'
DEV_ENV = {'SECRET_KEY': project_settings.DEV_SECRET_KEY}


class PaystackKeyModeTests(SimpleTestCase):
    """Which environment a pair of Paystack keys belongs to."""

    def test_test_keys_are_detected(self):
        self.assertEqual(
            project_settings.paystack_key_mode('pk_test_abc123', 'sk_test_abc123'), 'test')

    def test_live_keys_are_detected(self):
        self.assertEqual(
            project_settings.paystack_key_mode('pk_live_abc123', 'sk_live_abc123'), 'live')

    def test_a_half_switched_pair_reports_test(self):
        # A live public key alongside a test secret key cannot move real money,
        # so 'test' is the safe direction to be wrong in.
        self.assertEqual(
            project_settings.paystack_key_mode('pk_live_abc123', 'sk_test_abc123'), 'test')

    def test_missing_or_unrecognised_keys_report_unknown(self):
        self.assertEqual(project_settings.paystack_key_mode('', ''), 'unknown')
        self.assertEqual(project_settings.paystack_key_mode(None, None), 'unknown')
        self.assertEqual(project_settings.paystack_key_mode('nonsense', 'nonsense'), 'unknown')

    def test_surrounding_whitespace_does_not_defeat_detection(self):
        self.assertEqual(
            project_settings.paystack_key_mode('  pk_live_x  ', '\nsk_live_x\n'), 'live')

    def test_detection_is_offline_and_never_raises(self):
        # Called at import time, so it must not be able to fail a deployment.
        for value in ('', ' ', 'pk_', 'sk_live_', 'PK_LIVE_X', 'x' * 500):
            self.assertIn(project_settings.paystack_key_mode(value, value),
                          {'live', 'test', 'unknown'})

    def test_the_configured_mode_is_exposed_as_a_setting(self):
        self.assertIn(settings.PAYSTACK_KEY_MODE, {'live', 'test', 'unknown'})


class ProductionValidationTests(SimpleTestCase):
    """DEBUG=False must not start on development defaults."""

    def _validate(self, **overrides):
        kwargs = dict(
            debug=False,
            secret_key=GOOD_SECRET,
            allowed_hosts=['rehobothtek.com', 'www.rehobothtek.com'],
            env={'SECRET_KEY': GOOD_SECRET},
        )
        kwargs.update(overrides)
        return project_settings.validate_production_config(**kwargs)

    def test_a_properly_configured_production_starts(self):
        self._validate()  # must not raise

    def test_development_is_never_checked_at_all(self):
        # DEBUG=True has to stay zero-setup: no SECRET_KEY, wildcard hosts,
        # empty environment — and it still returns quietly.
        self._validate(debug=True, secret_key=project_settings.DEV_SECRET_KEY,
                       allowed_hosts=['*'], env={})

    def test_a_missing_secret_key_refuses_to_start(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self._validate(secret_key='', env={})
        self.assertIn('SECRET_KEY', str(caught.exception))

    def test_the_committed_placeholder_secret_key_refuses_to_start(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self._validate(secret_key=project_settings.DEV_SECRET_KEY, env=DEV_ENV)
        self.assertIn('placeholder', str(caught.exception))

    def test_a_wildcard_allowed_hosts_refuses_to_start(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self._validate(allowed_hosts=['*'])
        self.assertIn('ALLOWED_HOSTS', str(caught.exception))

    def test_empty_allowed_hosts_refuses_to_start(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            self._validate(allowed_hosts=[])
        self.assertIn('ALLOWED_HOSTS', str(caught.exception))

    def test_every_problem_is_reported_in_one_message(self):
        # A startup log is usually read once, so a partial message would cost
        # a second deploy cycle to discover the next problem.
        with self.assertRaises(ImproperlyConfigured) as caught:
            self._validate(secret_key=project_settings.DEV_SECRET_KEY,
                           allowed_hosts=['*'], env=DEV_ENV)
        message = str(caught.exception)
        self.assertIn('SECRET_KEY', message)
        self.assertIn('ALLOWED_HOSTS', message)
        self.assertIn('DEBUG=False', message)


class ProductionWarningTests(SimpleTestCase):
    """Degraded-but-safe configuration is warned about, not fatal."""

    def _warnings(self, **overrides):
        kwargs = dict(
            email_backend='django.core.mail.backends.smtp.EmailBackend',
            email_host='smtp.example.com',
            default_from_email='RehoBothTek <no-reply@rehobothtek.com>',
            paystack_mode='live',
        )
        kwargs.update(overrides)
        return project_settings.production_warnings(**kwargs)

    def test_a_complete_production_configuration_warns_about_nothing(self):
        self.assertEqual(self._warnings(), [])

    def test_the_console_email_backend_is_flagged(self):
        messages = self._warnings(
            email_backend='django.core.mail.backends.console.EmailBackend')
        self.assertEqual(len(messages), 1)
        self.assertIn('EMAIL_BACKEND', messages[0])

    def test_every_non_delivering_backend_is_flagged(self):
        for backend in project_settings.NON_DELIVERING_EMAIL_BACKENDS:
            with self.subTest(backend=backend):
                messages = self._warnings(email_backend=backend)
                self.assertEqual(len(messages), 1)

    def test_smtp_with_no_host_is_flagged(self):
        messages = self._warnings(email_host='')
        self.assertTrue(any('EMAIL_HOST' in m for m in messages))

    def test_a_local_sender_address_is_flagged(self):
        messages = self._warnings(default_from_email='Shop <no-reply@shop.local>')
        self.assertTrue(any('.local' in m for m in messages))

    def test_paystack_test_keys_are_flagged(self):
        messages = self._warnings(paystack_mode='test')
        self.assertTrue(any('TEST keys' in m for m in messages))

    def test_unrecognised_paystack_keys_are_flagged(self):
        messages = self._warnings(paystack_mode='unknown')
        self.assertTrue(any('missing or unrecognised' in m for m in messages))

    def test_several_problems_are_all_reported(self):
        messages = self._warnings(
            email_backend='django.core.mail.backends.console.EmailBackend',
            default_from_email='Shop <no-reply@shop.local>',
            paystack_mode='test',
        )
        self.assertEqual(len(messages), 3)

    def test_the_warning_helper_never_receives_the_keys_themselves(self):
        # It takes the detected *mode*, never the key values, so a warning
        # printed into a startup log structurally cannot contain a credential.
        params = set(inspect.signature(project_settings.production_warnings).parameters)
        self.assertEqual(params, {'email_backend', 'email_host', 'default_from_email',
                                  'paystack_mode'})


class LoggingConfigurationTests(SimpleTestCase):
    """ERROR-level failures need somewhere to land."""

    def test_the_app_logger_has_a_handler(self):
        # Without one, the logger.exception() calls in shop/notifications.py
        # and shop/paystack.py are emitted and thrown away.
        self.assertTrue(settings.LOGGING['loggers']['shop']['handlers'])

    def test_the_app_logger_is_not_muted_by_the_root_logger(self):
        self.assertFalse(settings.LOGGING['loggers']['shop']['propagate'])

    def test_failed_requests_are_always_kept(self):
        # Independently of LOG_LEVEL — a raised 500 is never "too noisy".
        self.assertEqual(settings.LOGGING['loggers']['django.request']['level'], 'ERROR')

    def test_security_events_are_logged(self):
        self.assertIn('django.security', settings.LOGGING['loggers'])

    def test_log_output_goes_to_stderr_by_default(self):
        if os.environ.get('LOG_DIR'):
            self.skipTest('LOG_DIR is set in this environment, so a file handler is added')
        self.assertEqual(list(settings.LOGGING['handlers']), ['console'])
        self.assertEqual(settings.LOGGING['handlers']['console']['class'],
                         'logging.StreamHandler')

    def test_no_log_record_can_carry_request_bodies_or_settings(self):
        # How credentials normally end up in logs is a format string that
        # quietly pulls in more than the message.
        for formatter in settings.LOGGING['formatters'].values():
            rendered = formatter['format']
            for forbidden in ('request', 'settings', 'environ', 'META', 'POST', 'body'):
                self.assertNotIn(forbidden, rendered)

    def test_djangos_own_loggers_are_not_disabled(self):
        self.assertFalse(settings.LOGGING['disable_existing_loggers'])


class EmailConfigurationTests(SimpleTestCase):
    """Email is environment-driven and cannot hang a checkout."""

    def test_starttls_and_implicit_tls_are_never_both_on(self):
        # Django raises at send time if both are enabled — which would only
        # surface when a customer tried to place an order.
        self.assertFalse(settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL)

    def test_a_mail_timeout_is_configured(self):
        # Notifications are sent inline during checkout, so a dead SMTP host
        # without a timeout would hang the request.
        self.assertGreater(settings.EMAIL_TIMEOUT, 0)

    def test_the_port_is_an_integer(self):
        self.assertIsInstance(settings.EMAIL_PORT, int)

    def test_djangos_error_mail_has_a_sender(self):
        self.assertTrue(settings.SERVER_EMAIL)

    def test_the_development_default_still_prints_to_the_console(self):
        # Local development must stay zero-setup.
        #
        # Asserted against the settings *module* rather than django.conf.settings:
        # the test runner swaps settings.EMAIL_BACKEND for locmem in
        # setup_test_environment(), so the runtime value says nothing about what
        # an operator actually configured. The module global is what the
        # environment produced and is not touched by that swap.
        if os.environ.get('EMAIL_BACKEND'):
            self.skipTest('EMAIL_BACKEND is set in this environment')
        self.assertEqual(project_settings.EMAIL_BACKEND,
                         'django.core.mail.backends.console.EmailBackend')
