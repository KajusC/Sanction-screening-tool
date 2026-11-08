import django
from django.conf import settings

from sanction_check.settings import get_settings

_settings = get_settings()

settings.configure(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _settings.postgres_db,
            "USER": _settings.postgres_user,
            "PASSWORD": _settings.postgres_password.get_secret_value(),
            "HOST": _settings.postgres_host,
        }
    },
    INSTALLED_APPS=["django.contrib.postgres", "sanction_check.db"],
    USE_TZ=True,
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
)
django.setup()
