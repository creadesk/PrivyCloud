from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = "Prints True/False whether a superuser exists"

    def handle(self, *args, **options):
        User = get_user_model()
        self.stdout.write(str(User.objects.filter(is_superuser=True).exists()))