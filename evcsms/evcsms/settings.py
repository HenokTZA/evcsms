# evcsms/settings.py
from pathlib import Path
import os
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

# where you keep custom settings
PUBLIC_HOST = "147.93.127.215"     # ← your server’s public IP or domain

ALLOWED_HOSTS = [
    "147.93.127.215",   # ← your public IP, no port
    "127.0.0.1",
    "localhost",
]


# ────────────────
#  Core
# ────────────────
SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "⚑_replace_this_soon",
)
DEBUG = True


AUTH_USER_MODEL = "csms.User"

# ───────────── Database ─────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# ────────────────
#  Installed apps
# ────────────────
INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 3-rd party
    "rest_framework",
    "rest_framework_simplejwt",       # ← NEW
    # project
    "csms",
    "corsheaders",
]

# ────────────────
#  Middleware / templates (unchanged)
# ────────────────
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

CORS_ALLOW_ALL_ORIGINS = True

ROOT_URLCONF = "evcsms.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "evcsms.wsgi.application"
ASGI_APPLICATION = "evcsms.asgi.application"

# ────────────────
#  DRF & JWT
# ────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        *(
            []                          # Browsable API only in DEBUG
            if not DEBUG
            else ["rest_framework.renderers.BrowsableAPIRenderer"]
        ),
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME":  timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}

# ────────────────
#  Static / i18n / etc. (unchanged)
# ────────────────
STATIC_URL   = "static/"
STATIC_ROOT  = BASE_DIR / "staticfiles"
LANGUAGE_CODE = "en-us"
TIME_ZONE     = "UTC"
USE_I18N = True
USE_TZ   = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

EMAIL_BACKEND      = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST         = "mail.habm-lab.com"
EMAIL_PORT         = 465
EMAIL_HOST_USER    = "test@habm-lab.com"
EMAIL_HOST_PASSWORD= "#Procondev01"
EMAIL_USE_SSL      = True
# since you’re using implicit SSL on port 465, you don’t need STARTTLS
# so you can leave EMAIL_USE_TLS = False (or omit it entirely)
DEFAULT_FROM_EMAIL = "H-Craft <test@habm-lab.com>"

