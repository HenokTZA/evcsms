"""
Django settings for evcsms project.

Generated manually to keep things minimal yet admin-ready.
"""

from pathlib import Path
import os

# ──────────────────────────
#  Core paths & secrets
# ──────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "⚑_replace_this_with_a_real_secret_key_for_production",
)

DEBUG = True                      # ⚑ switch to False in prod
ALLOWED_HOSTS: list[str] = []     # ⚑ add domain/IPs when DEBUG=False

AUTH_USER_MODEL = "csms.User"


# settings.py
DATABASES = {
  "default": {
    "ENGINE": "djongo",
    "NAME":   "evcsms",
    "ENFORCE_SCHEMA": True,
    "CLIENT": {
        "host": "mongodb+srv://<user>:<pwd>@cluster0.mongodb.net/evcsms?retryWrites=true&w=majority"
    }
  }
}



# ──────────────────────────
#  Applications
# ──────────────────────────
INSTALLED_APPS = [
    # Django core
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    # Local apps
    "csms",
]

# ──────────────────────────
#  Middleware (admin needs the three marked lines)
# ──────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",   # ← required
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",  # ← required
    "django.contrib.messages.middleware.MessageMiddleware",     # ← required
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "evcsms.urls"

# ──────────────────────────
#  Templates
# ──────────────────────────
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],                        # BASE_DIR / "templates" if you add custom HTML
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

# ──────────────────────────
#  Database  (SQLite for dev)
# ──────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# ──────────────────────────
#  Password validation
# ──────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ──────────────────────────
#  Internationalisation / time
# ──────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"      # keep CPs happy
USE_I18N = True
USE_TZ = True

# ──────────────────────────
#  Static files
# ──────────────────────────
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"   # for collectstatic in prod

# ──────────────────────────
#  Default PK type
# ──────────────────────────
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ──────────────────────────
#  Django REST framework defaults (optional)
# ──────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    # Enable browsable API in DEBUG only
    **(
        {}
        if not DEBUG
        else {
            "DEFAULT_RENDERER_CLASSES": [
                "rest_framework.renderers.JSONRenderer",
                "rest_framework.renderers.BrowsableAPIRenderer",
            ]
        }
    ),
}
