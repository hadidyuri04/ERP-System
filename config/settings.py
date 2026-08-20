from pathlib import Path
import os
from dotenv import load_dotenv
from django.utils.translation import gettext_lazy as _

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Quick-start development settings - unsuitable for production
SECRET_KEY = 'django-insecure-7_j2*&*p(z$%4kus1u3n8qkv=0*d%oy6okf0y$75utejd345#r'

DEBUG = True

ALLOWED_HOSTS = []

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    "accounts",
    "customers",
    "suppliers",
    "inventory",
    "purchasing",
    "finance",
    "pos",
    "quotations",
    "notifications",
    "core",
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware', # Required for dynamic language switching
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
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.i18n', # Added for language handling in templates
                "django.template.context_processors.static",
                # Supplies unread_count and recent_notifications to the bell
                # in the header on every page.
                "notifications.context_processors.notifications",
                # Supplies company settings, notably the configured currency,
                # so templates stop hardcoding a symbol.
                "core.context_processors.company",
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization & RTL Configuration
LANGUAGE_CODE = 'en'

LANGUAGES = [
    ('en', _('English')),
    ('ar', _('العربية')),
]

LOCALE_PATHS = [
    BASE_DIR / 'locale',
]

STATICFILES_DIRS = [BASE_DIR / "static"]
TIME_ZONE = 'Asia/Amman'

USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded files: product images and the company logo.
# Without MEDIA_ROOT, upload_to='products/' writes into the project root and the
# files can never be served, so uploads appear to work but nothing displays.
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "login"
# Finance control accounts are configurable so the chart codes can differ
# between deployments without changing posting service code.
FINANCE_POSTING_ACCOUNTS = {
    "cash": "1100",
    "bank": "1200",
    "card_clearing": "1210",
    "accounts_receivable": "1300",
    "inventory": "1400",
    "purchase_tax": "1500",
    "accounts_payable": "2100",
    "sales_tax_payable": "2200",
    "sales_revenue": "4100",
    "inventory_adjustment_gain": "4300",
    "cost_of_goods_sold": "5100",
    "waste_loss": "6300",
    "inventory_adjustment_loss": "6310",
}
