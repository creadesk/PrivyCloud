import os
import pytest
from django.contrib.auth import get_user_model

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

User = get_user_model()


@pytest.fixture
def superuser(db):
    """Erstellt einen Superuser für E2E-Tests."""
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="SuperSecure123!"
    )
    return user