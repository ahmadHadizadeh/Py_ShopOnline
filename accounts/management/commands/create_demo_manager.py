import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.apps import STORE_MANAGER_GROUP_NAME
from core.demo_security import DEMO_CUSTOMER_USERNAME_PREFIX


User = get_user_model()


class Command(BaseCommand):
    help = "Create or rotate the restricted store-manager account for an isolated sales demo."

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("This command is available only when DEMO_MODE=1.")

        username = os.getenv("DEMO_ADMIN_USERNAME", "").strip()
        password = os.getenv("DEMO_ADMIN_PASSWORD", "")

        if not username or not password:
            raise CommandError(
                "DEMO_ADMIN_USERNAME and DEMO_ADMIN_PASSWORD must be configured."
            )
        if username.startswith(DEMO_CUSTOMER_USERNAME_PREFIX):
            raise CommandError("The demo manager username uses a reserved prefix.")
        if len(password) < 20:
            raise CommandError("DEMO_ADMIN_PASSWORD must contain at least 20 characters.")

        manager_group = Group.objects.filter(
            name=STORE_MANAGER_GROUP_NAME
        ).first()
        if manager_group is None:
            raise CommandError("The store-manager group is missing; run migrations first.")

        with transaction.atomic():
            manager_group = Group.objects.select_for_update().get(pk=manager_group.pk)
            user = User.objects.select_for_update().filter(username=username).first()

            if user is not None and user.is_superuser:
                raise CommandError(
                    "Refusing to convert an existing superuser into the demo manager."
                )

            other_manager_exists = User.objects.filter(
                groups=manager_group,
                is_superuser=False,
            ).exclude(pk=user.pk if user else None).exists()
            if other_manager_exists:
                raise CommandError(
                    "A different manager is already assigned in this Demo database."
                )

            if user is None:
                user = User.objects.create_user(username=username)
            elif (
                user.user_permissions.exists()
                or user.groups.exclude(pk=manager_group.pk).filter(
                    permissions__isnull=False
                ).exists()
            ):
                raise CommandError(
                    "The existing account has additional permissions; refusing to reuse it."
                )

            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                raise CommandError("; ".join(exc.messages)) from exc

            user.is_active = True
            user.is_staff = True
            user.is_superuser = False
            user.set_password(password)
            user.save(
                update_fields=[
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "password",
                ]
            )
            user.groups.add(manager_group)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo manager '{username}' is ready. The password was not displayed."
            )
        )
