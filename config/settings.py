"""
Django settings for the RehoBothTek project.

This file works two ways:

  - Locally, with nothing configured, it runs exactly like before —
    DEBUG on, SQLite, no environment setup needed. Good for development.
  - In production, set the environment variables below (a real SECRET_KEY,
    DEBUG=False, your real domain in ALLOWED_HOSTS, your live Paystack
    keys, etc.) and this file automatically switches on the security
    settings a real deployment needs (HTTPS redirects, secure cookies,
    HSTS) and will use PostgreSQL if DATABASE_URL is set.

Production does not just *prefer* those variables, it requires the two that
would otherwise leave the site quietly insecure (SECRET_KEY and
ALLOWED_HOSTS) — see the "Production configuration checks" section further
down. Anything that degrades the site without making it unsafe (notifications
printing instead of emailing, Paystack still on test keys) is reported as a
warning at startup instead of a hard failure, so a half-configured deploy
still serves customers while telling you loudly what's missing.

See .env.example for every variable this file reads, and the README for
step-by-step deployment instructions.
"""
import os
import sys
import warnings
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a local .env file if present (never required — just a convenience
# so you don't have to export environment variables by hand while
# developing). This does nothing in production if you set real environment
# variables through your hosting platform instead, which is what you should
# do there rather than shipping a .env file.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except ImportError:
    pass


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def whitenoise_autorefresh(debug, argv):
    """Whether WhiteNoise should read static files on demand rather than index
    them once at startup.

    In indexing mode WhiteNoise builds a file map from STATIC_ROOT the moment
    the middleware is constructed, and warns loudly if that directory does not
    exist. That warning is exactly right in production — it means the deploy
    never ran `collectstatic`, so every stylesheet and script would 404 — but
    it is pure noise in the two situations where STATIC_ROOT is legitimately
    absent: local development, and the test suite (which runs with DEBUG=False
    but never serves a static file).

    So: on in development, on under the test runner, off otherwise. Production
    keeps the indexed path, which is the fast one.
    """
    return bool(debug) or 'test' in argv


# --- Values that are only ever OK in development ------------------------------------
# These are the fallbacks used when nothing is configured. They are deliberately
# named and kept in one place so the production checks below can recognise them
# and refuse (or warn) rather than let a live site inherit a development value
# by accident.
DEV_SECRET_KEY = 'django-insecure-REPLACE-THIS-BEFORE-DEPLOYING-abc123xyz'
DEV_PAYSTACK_PUBLIC_KEY = 'pk_test_049d27605613a3cce87b29eb01f71c53c7627290'
DEV_PAYSTACK_SECRET_KEY = 'sk_test_c8cf3587e6dbd5679a97c35eec06e029543f1893'

# Backends that accept mail and drop it on the floor (or print it). Fine
# locally, useless in production — the shop owner would never learn an order
# came in.
NON_DELIVERING_EMAIL_BACKENDS = frozenset({
    'django.core.mail.backends.console.EmailBackend',
    'django.core.mail.backends.locmem.EmailBackend',
    'django.core.mail.backends.dummy.EmailBackend',
    'django.core.mail.backends.filebased.EmailBackend',
})


def paystack_key_mode(public_key, secret_key):
    """Which Paystack environment these keys belong to: 'live', 'test', 'unknown'.

    Paystack prefixes its keys with the environment they work in — `pk_test_` /
    `sk_test_` move no real money, `pk_live_` / `sk_live_` do. Reading that
    prefix is the only detection available without calling Paystack, which we
    deliberately never do here: this runs at startup, offline, and must never
    be able to fail a deployment because an API was unreachable.

    Returns 'live' only when BOTH keys are live keys, so a half-switched pair
    (one live, one test) reports 'test' — the safe direction to be wrong in.
    """
    public_key = (public_key or '').strip()
    secret_key = (secret_key or '').strip()
    if public_key.startswith('pk_test_') or secret_key.startswith('sk_test_'):
        return 'test'
    if public_key.startswith('pk_live_') and secret_key.startswith('sk_live_'):
        return 'live'
    return 'unknown'


def validate_production_config(*, debug, secret_key, allowed_hosts, env=os.environ):
    """Refuse to start in production on development defaults.

    Does nothing at all when DEBUG is on, which is the point: local development
    stays zero-setup. In production a missing SECRET_KEY or a wildcard
    ALLOWED_HOSTS is a configuration mistake that is far cheaper to surface at
    startup than to discover after launch — a site running on the development
    SECRET_KEY can have its session cookies and password-reset tokens forged by
    anyone who has read the source, and ALLOWED_HOSTS='*' turns off Django's
    Host-header validation entirely.

    Deliberately narrow: it only fails on things that are unambiguously wrong.
    Judgment calls (which email backend, which Paystack keys) are warnings, so
    a working-but-imperfect deploy is never blocked from serving customers.
    """
    if debug:
        return

    problems = []

    if not (env.get('SECRET_KEY') or '').strip():
        problems.append(
            'SECRET_KEY is not set. Generate one with:\n'
            '    python -c "import secrets; print(secrets.token_urlsafe(50))"\n'
            '  and set it as the SECRET_KEY environment variable.'
        )
    elif secret_key == DEV_SECRET_KEY:
        problems.append(
            'SECRET_KEY is still the development placeholder committed in '
            'config/settings.py. Anyone who can read the source can forge '
            'session cookies and password-reset tokens with it.'
        )

    if not allowed_hosts:
        problems.append('ALLOWED_HOSTS is empty, so Django will reject every request.')
    elif '*' in allowed_hosts:
        problems.append(
            "ALLOWED_HOSTS is '*' (the development default), which switches off "
            "Django's Host-header validation. Set it to your real domain(s), e.g. "
            '"example.com,www.example.com".'
        )

    if problems:
        raise ImproperlyConfigured(
            'Refusing to start with DEBUG=False — production configuration is '
            'incomplete:\n\n  - ' + '\n  - '.join(problems) +
            '\n\nSee .env.example for the full list of variables and the README '
            'for deployment steps.'
        )


def production_warnings(*, email_backend, email_host, default_from_email, paystack_mode):
    """Non-fatal production configuration problems, as human-readable strings.

    These are things that leave the site up and safe but quietly not doing what
    the owner expects — so they are reported loudly at startup, not hidden.
    """
    messages = []

    if email_backend in NON_DELIVERING_EMAIL_BACKENDS:
        messages.append(
            f'EMAIL_BACKEND is {email_backend!r}: order, repair and contact '
            'notifications will be printed or discarded, not emailed. Set '
            'EMAIL_BACKEND and EMAIL_HOST_* to a real SMTP server — see the '
            'README section "Turning on real email".'
        )
    elif 'smtp' in email_backend and not (email_host or '').strip():
        messages.append(
            f'EMAIL_BACKEND is {email_backend!r} but EMAIL_HOST is empty, so no '
            'notification email can be delivered.'
        )

    if '.local' in (default_from_email or ''):
        messages.append(
            f'DEFAULT_FROM_EMAIL is {default_from_email!r} — the .local domain is '
            'not deliverable and most providers will reject or spam-file it. Set '
            'DEFAULT_FROM_EMAIL to a real address on your own domain.'
        )

    if paystack_mode == 'test':
        messages.append(
            'Paystack is configured with TEST keys (pk_test_/sk_test_). No real '
            'money will move — swap in your live keys from the Paystack dashboard '
            'when you are ready to take payments.'
        )
    elif paystack_mode == 'unknown':
        messages.append(
            'Paystack keys are missing or unrecognised (they should start with '
            'pk_live_/sk_live_, or pk_test_/sk_test_ while testing), so online '
            'payment is not usable.'
        )

    return messages


# --- Security ---------------------------------------------------------------
# In development, this insecure fallback key is fine. In production, set a
# real SECRET_KEY environment variable — generate one with:
#   python -c "import secrets; print(secrets.token_urlsafe(50))"
# Production refuses to start on this placeholder; see the production
# configuration checks at the bottom of this file.
SECRET_KEY = os.environ.get('SECRET_KEY', DEV_SECRET_KEY)

# DEBUG defaults to True (development). Set the DEBUG environment variable
# to "False" in production — this one flag also controls whether the HTTPS/
# cookie security settings further down switch on.
DEBUG = env_bool('DEBUG', default=True)

# In development, '*' is fine. In production, set ALLOWED_HOSTS to a
# comma-separated list of your real domain(s), e.g.
# "rehobothtek.com,www.rehobothtek.com" — Django refuses to serve requests
# for any host not in this list, which stops a class of attacks that rely
# on spoofed Host headers.
_allowed_hosts = os.environ.get('ALLOWED_HOSTS', '*')
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts.split(',') if h.strip()]

# --- Apps --------------------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'shop',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves static files without needing nginx
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'shop.context_processors.site_info',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# --- Database -----------------------------------------------------------------
# Uses SQLite by default (zero setup, good for development and small
# stores). If you set a DATABASE_URL environment variable — the standard
# connection string most hosts (Render, Railway, Heroku, etc.) give you for
# a managed PostgreSQL database — this switches to that automatically.
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    import dj_database_url
    DATABASES = {'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# --- Customer accounts -------------------------------------------------------------
# Django's built-in auth views are mounted at /accounts/ in config/urls.py.
# These three settings say where to send people who aren't logged in yet, where
# to put them after a successful login, and where to land them after logout.
# 'login' and 'shop:account' are URL names, so the paths can change freely.
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'shop:account'
LOGOUT_REDIRECT_URL = 'shop:home'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Lagos'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'  # populated by `collectstatic` for production
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        # The compressed *manifest* storage (production) requires
        # `collectstatic` to have been run, so it has a manifest file to
        # look up hashed filenames in — that won't exist yet in local
        # development, and every {% static %} tag would break. So: plain
        # WhiteNoise storage locally (no manifest needed), the full
        # manifest+compression version once DEBUG=False in production.
        'BACKEND': (
            'whitenoise.storage.CompressedManifestStaticFilesStorage'
            if not DEBUG else
            'whitenoise.storage.CompressedStaticFilesStorage'
        ),
    },
}

# WhiteNoise indexes STATIC_ROOT once at startup unless this is on — see
# whitenoise_autorefresh() above. Left off in production deliberately: the
# indexed path is the fast one, and the startup warning it can emit there is
# a genuine "you forgot collectstatic" signal.
WHITENOISE_AUTOREFRESH = whitenoise_autorefresh(DEBUG, sys.argv)

MEDIA_URL = 'media/'
# Product photos, uploaded through /admin/. Django only serves this directory
# itself while DEBUG is on (see config/urls.py) — WhiteNoise serves STATIC_ROOT
# and deliberately does NOT serve media, so in production MEDIA_ROOT has to be
# served separately by the web server or object storage. See "Serving uploaded
# product photos in production" in the README.
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Email (automatic order & repair-booking notifications) ------------------------
# Entirely environment-driven. The default prints emails to your terminal,
# which is what you want in development and is *not* what you want in
# production — with the console backend running live, an order comes in and
# nobody is told. Production logs a startup warning if this is still the case.
#
# The three common shapes:
#   - Development:            leave everything unset (prints to terminal)
#   - Gmail / most providers: EMAIL_HOST=smtp.example.com, EMAIL_PORT=587,
#                             EMAIL_USE_TLS=True (STARTTLS)
#   - Implicit-TLS providers: EMAIL_PORT=465, EMAIL_USE_SSL=True
# See the README for a Gmail app-password walkthrough.
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', default=True)
EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', default=False)
# STARTTLS and implicit TLS are mutually exclusive — Django raises at send time
# if both are on. Turning SSL on implies port 465 and no STARTTLS, so setting
# EMAIL_USE_SSL=True is enough; you don't have to remember to unset TLS as well.
if EMAIL_USE_SSL:
    EMAIL_USE_TLS = False
# Seconds to wait on the SMTP server. Without a timeout a dead mail server can
# hang a checkout request indefinitely; notifications are sent inline.
EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT', '15'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'RehoBothTek Website <no-reply@rehobothtek.local>')
# Where Django sends its own error mail (ADMINS / broken-link notifications),
# as opposed to customer-facing notifications. Defaults to the same address.
SERVER_EMAIL = os.environ.get('SERVER_EMAIL', DEFAULT_FROM_EMAIL)

# --- Paystack (online payments) --------------------------------------------------
# Get your real keys from https://dashboard.paystack.com -> Settings -> API Keys & Webhooks
# Set these as environment variables — never hardcode real keys here, and
# never commit them to a public repository. Start with TEST keys
# (pk_test_.../sk_test_...) until the whole flow works, then switch to LIVE
# keys (pk_live_.../sk_live_...).
#
# The fallbacks below are Paystack's *published test* keys, so a fresh clone
# runs the payment flow end-to-end without a dashboard account. They are
# detected at startup so that a production deploy cannot quietly go live on
# them — see PAYSTACK_KEY_MODE and the production checks at the bottom.
PAYSTACK_PUBLIC_KEY = os.environ.get('PAYSTACK_PUBLIC_KEY', DEV_PAYSTACK_PUBLIC_KEY)
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', DEV_PAYSTACK_SECRET_KEY)
# 'live', 'test' or 'unknown', derived from the key prefixes above. Purely
# informational: it reports which environment the keys belong to and is never
# consulted when verifying a payment (shop/paystack.py does that, against
# Paystack itself).
PAYSTACK_KEY_MODE = paystack_key_mode(PAYSTACK_PUBLIC_KEY, PAYSTACK_SECRET_KEY)

# --- RehoBothTek-specific settings -------------------------------------------------
SITE_NAME = 'RehoBothTek'
SITE_TAGLINE = 'Something Different'
# The site's public origin, e.g. "https://rehobothtek.com" — no trailing slash,
# no path. Used for <link rel="canonical"> and Open Graph URLs. Left empty in
# development, where those tags fall back to whatever host the request came in
# on, so local browsing and the test suite need no configuration.
SITE_URL = os.environ.get('SITE_URL', '').strip().rstrip('/')
WHATSAPP_NUMBER = os.environ.get('WHATSAPP_NUMBER', '2348113956538')
SITE_EMAIL = os.environ.get('SITE_EMAIL', 'observernathan124@gmail.com')
TELEGRAM_USERNAME = os.environ.get('TELEGRAM_USERNAME', 'rehobothtek')
ADMIN_NOTIFY_EMAIL = os.environ.get('ADMIN_NOTIFY_EMAIL', SITE_EMAIL)
SHOP_ADDRESS = os.environ.get('SHOP_ADDRESS', 'No3 Charismatic Crescent, Ugbokolo, Benue State, Nigeria')

# --- Logging ------------------------------------------------------------------------
# Without this, Python's "no handler configured" fallback sends WARNING and
# above to stderr and throws everything below it away — so the ERROR lines that
# shop/notifications.py logs when a notification email fails, and the ones
# shop/paystack.py logs when verification can't reach Paystack, had no
# configured destination of their own and no level control.
#
# Everything goes to stderr, which is what hosting platforms capture: no path
# to create, no permissions to get wrong, nothing to rotate. LOG_LEVEL raises
# or lowers the noise for the app's own loggers; ERROR-level failures are
# always kept.
#
# Nothing here logs request bodies, POST data, settings, or exception
# arguments — the messages in shop/ name an order or a reference, never a
# password, key, or card detail.
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').strip().upper()

LOG_FORMATTERS = {
    'standard': {
        'format': '[{asctime}] {levelname} {name}: {message}',
        'style': '{',
        'datefmt': '%Y-%m-%d %H:%M:%S',
    },
}

LOG_HANDLERS = {
    'console': {
        'class': 'logging.StreamHandler',
        'formatter': 'standard',
    },
}

# Optional: set LOG_DIR to also keep a rotating file of the same records. Opt-in
# rather than automatic, because a hard-coded path is one more thing that can be
# unwritable on a given host — and a logging handler that fails at startup takes
# the whole site down with it. delay=True means the file is only opened on the
# first record, so a bad path degrades instead of blocking boot.
LOG_DIR = os.environ.get('LOG_DIR', '').strip()
if LOG_DIR:
    LOG_HANDLERS['file'] = {
        'class': 'logging.handlers.RotatingFileHandler',
        'filename': str(Path(LOG_DIR) / 'bytebench.log'),
        'maxBytes': 5 * 1024 * 1024,
        'backupCount': 5,
        'formatter': 'standard',
        'delay': True,
        'encoding': 'utf-8',
    }

LOGGING = {
    'version': 1,
    # Keep Django's own loggers working rather than silently disabling them.
    'disable_existing_loggers': False,
    'formatters': LOG_FORMATTERS,
    'handlers': LOG_HANDLERS,
    'loggers': {
        # The app's own loggers (shop.notifications, shop.paystack, ...).
        'shop': {
            'handlers': list(LOG_HANDLERS),
            'level': LOG_LEVEL,
            'propagate': False,
        },
        # Failed requests that mattered — unhandled 500s and 4xx responses
        # raised as exceptions. Django logs these at ERROR already; naming them
        # here keeps them at ERROR regardless of LOG_LEVEL.
        'django.request': {
            'handlers': list(LOG_HANDLERS),
            'level': 'ERROR',
            'propagate': False,
        },
        # Suspicious Host headers, CSRF failures, broken password hashes.
        'django.security': {
            'handlers': list(LOG_HANDLERS),
            'level': 'WARNING',
            'propagate': False,
        },
    },
    'root': {
        'handlers': list(LOG_HANDLERS),
        'level': 'WARNING',
    },
}

# --- Production-only security settings ---------------------------------------------
# These only switch on when DEBUG=False (i.e. only in production). They'd
# break local development over plain http://127.0.0.1:8000/, which is why
# they're conditional rather than always on.
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    # If you're behind a proxy/load balancer that terminates HTTPS for you
    # (true on most hosting platforms — Render, Railway, Heroku, etc.),
    # Django needs to know to trust its X-Forwarded-Proto header, otherwise
    # it will think every request is plain HTTP and redirect-loop forever.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# --- Production configuration checks -------------------------------------------------
# Runs last, once every value above has been resolved, so it is checking what
# the site would actually run with rather than what was typed.
#
# Hard failures: things that would leave the site quietly insecure or unable to
# answer a request at all. Warnings: things that leave it up and safe but not
# doing what the owner expects. Both are printed in full — no partial messages,
# because a startup log is usually the only thing anyone reads.
if not DEBUG:
    validate_production_config(
        debug=DEBUG, secret_key=SECRET_KEY, allowed_hosts=ALLOWED_HOSTS,
    )
    for _warning in production_warnings(
        email_backend=EMAIL_BACKEND, email_host=EMAIL_HOST,
        default_from_email=DEFAULT_FROM_EMAIL, paystack_mode=PAYSTACK_KEY_MODE,
    ):
        warnings.warn(_warning, RuntimeWarning, stacklevel=2)
