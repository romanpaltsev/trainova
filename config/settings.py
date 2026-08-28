"""Настройки проекта «Дневник тренировок».

Все значения, зависящие от окружения, читаются из .env (django-environ).
"""

import tomllib
from pathlib import Path

import environ
from django.contrib.messages import constants as messages_constants

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    # Прод-флаги: в разработке всё выключено, включает .env.prod.
    DJANGO_STATIC_MANIFEST=(bool, False),
    DJANGO_SECURE_SSL_REDIRECT=(bool, False),
    DJANGO_SECURE_HSTS_SECONDS=(int, 0),
    DJANGO_COOKIE_SECURE=(bool, False),
    DJANGO_DB_CONN_MAX_AGE=(int, 0),
    # Панель отладки включается отдельным флагом, а не по DEBUG: pytest-django
    # выставляет DEBUG=False уже после импорта настроек, и блок «если DEBUG»
    # оставался бы активным в тестах, искажая счётчики запросов.
    DJANGO_DEBUG_TOOLBAR=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

# Версия продукта для футера профиля. Единственный источник — pyproject.toml:
# importlib.metadata не годится, прод-образ ставит зависимости с --no-install-project.
try:
    APP_VERSION = tomllib.loads((BASE_DIR / "pyproject.toml").read_text("utf-8"))["project"][
        "version"
    ]
except (OSError, KeyError, tomllib.TOMLDecodeError):
    APP_VERSION = ""

# Дубль токенов --app-bg из static/css/tokens.css: манифест PWA и <meta name="theme-color">
# не понимают CSS-переменных, а цвет темы нужен ещё до первой отрисовки.
APP_THEME_COLORS = {"dark": "#0D1015", "light": "#F6F7F9"}

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")


# Приложения

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "accounts",
    "workouts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Сразу после SecurityMiddleware — так статику отдаёт сам web-контейнер.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

DEBUG_TOOLBAR = env("DJANGO_DEBUG_TOOLBAR")

if DEBUG_TOOLBAR:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    # INTERNAL_IPS бесполезен в Docker: запрос приходит с адреса шлюза бриджа,
    # а не с 127.0.0.1 — поэтому решаем по флагу.
    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda request: True}

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.honeypot",
                "accounts.context_processors.theme_colors",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# База данных

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
# Переиспользование соединений: на gunicorn-воркерах экономит подключение на запрос.
DATABASES["default"]["CONN_MAX_AGE"] = env("DJANGO_DB_CONN_MAX_AGE")


# Пользователи и аутентификация

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "account_login"

SITE_ID = 1

# Тег ошибки в Django — "error", а в наших классах — "danger".
MESSAGE_TAGS = {messages_constants.ERROR: "danger"}

# allauth: вход и регистрация только по email, подтверждение обязательно.
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_LOGOUT_ON_GET = False
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_PASSWORD_RESET_BY_CODE_ENABLED = False
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = False
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_LOGIN_ON_PASSWORD_RESET = False
ACCOUNT_EMAIL_SUBJECT_PREFIX = ""
# Скрытое поле-ловушка для ботов: заполнено — регистрация молча отбрасывается.
ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "phone_number"
# У нашей модели пользователя нет username — иначе формы allauth ищут это поле.
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_ADAPTER = "accounts.adapter.AccountAdapter"
ACCOUNT_FORMS = {
    "login": "accounts.forms.LoginForm",
    "signup": "accounts.forms.SignupForm",
    "reset_password": "accounts.forms.ResetPasswordForm",
    "reset_password_from_key": "accounts.forms.ResetPasswordKeyForm",
    "change_password": "accounts.forms.ChangePasswordForm",
    "set_password": "accounts.forms.SetPasswordForm",
}


# Почта

# EMAIL_URL описывает отправку одной строкой: smtp://user:pass@host:port или
# consolemail:// — код не зависит от конкретного способа отправки.
# Django 6.1+ настраивает почту через MAILERS, старые EMAIL_* объявлены устаревшими.
_email = env.email_url("EMAIL_URL")
_mailer = {"BACKEND": _email["EMAIL_BACKEND"]}
if _mailer["BACKEND"].endswith("smtp.EmailBackend"):
    _mailer["OPTIONS"] = {
        "host": _email.get("EMAIL_HOST") or "localhost",
        "port": _email.get("EMAIL_PORT") or 25,
        "username": _email.get("EMAIL_HOST_USER") or "",
        "password": _email.get("EMAIL_HOST_PASSWORD") or "",
        "use_tls": bool(_email.get("EMAIL_USE_TLS")),
        "use_ssl": bool(_email.get("EMAIL_USE_SSL")),
    }
MAILERS = {"default": _mailer}

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Дневник тренировок <no-reply@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL


# Локализация

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True


# Статика

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

if env("DJANGO_STATIC_MANIFEST"):
    # Хэши в именах файлов и заранее пожатые копии — включаем только там, где
    # выполнен collectstatic. Тесты идут с DEBUG=False, и без флага они падали бы
    # на отсутствующем манифесте.
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
    # Год кэша можно позволить только здесь: имена файлов содержат хэш содержимого.
    # Без манифеста (разработка) кэшировать нельзя — правки статики не доходили бы
    # до браузера.
    WHITENOISE_MAX_AGE = 31536000


# Безопасность за обратным прокси

# nginx передаёт схему в X-Forwarded-Proto, иначе Django считает запрос HTTP
# и ломает CSRF на HTTPS-домене.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env("DJANGO_SECURE_SSL_REDIRECT")
SECURE_HSTS_SECONDS = env("DJANGO_SECURE_HSTS_SECONDS")
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0
SESSION_COOKIE_SECURE = env("DJANGO_COOKIE_SECURE")
CSRF_COOKIE_SECURE = env("DJANGO_COOKIE_SECURE")
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"


# Логи забирает docker, поэтому пишем в stdout

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "app": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "app"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.db.backends": {"level": "WARNING"},
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
