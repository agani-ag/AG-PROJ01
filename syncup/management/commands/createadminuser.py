"""Create the admin — the only account AG-PROJ01 has — from environment variables.

    ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD   required
    ADMIN_NAME                                    optional display name (defaults to the username)
    ADMIN_PIN                                     optional 5-character login PIN (A-Z, 0-9)

Name, password and PIN can all be changed afterwards from Settings → Profile.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from ...models import AdminPin


class Command(BaseCommand):
    help = "Creates the admin superuser from environment variables"

    def handle(self, *args, **kwargs):
        User = get_user_model()
        username = os.environ.get("ADMIN_USERNAME")
        email = os.environ.get("ADMIN_EMAIL")
        password = os.environ.get("ADMIN_PASSWORD")
        name = (os.environ.get("ADMIN_NAME") or "").strip()
        pin = (os.environ.get("ADMIN_PIN") or "").strip().upper()

        if not username or not email or not password:
            self.stdout.write(self.style.ERROR("Environment variables not set!"))
            return
        if pin and not AdminPin.PATTERN.fullmatch(pin):
            self.stdout.write(self.style.ERROR("ADMIN_PIN must be exactly 5 letters or digits (A-Z, 0-9)."))
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING(f"User '{username}' already exists."))
            return

        user = User.objects.create_superuser(
            username=username, email=email, password=password, first_name=name or username.title(),
        )
        if pin:
            admin_pin = AdminPin(user=user)
            admin_pin.set_pin(pin)
            admin_pin.save()
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' created successfully!"))
