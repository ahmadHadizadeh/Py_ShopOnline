from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.demo_security import DEMO_CUSTOMER_USERNAME_PREFIX
from orders.models import Order


User = get_user_model()


class Command(BaseCommand):
    help = "Remove expired visitor-owned records from an isolated sales demo."

    def add_arguments(self, parser):
        parser.add_argument("--retention-hours", type=int, default=48)

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("This command is available only when DEMO_MODE=1.")

        retention_hours = options["retention_hours"]
        if not 1 <= retention_hours <= 168:
            raise CommandError("--retention-hours must be between 1 and 168.")

        cutoff = timezone.now() - timedelta(hours=retention_hours)
        stale_users = User.objects.filter(
            username__startswith=DEMO_CUSTOMER_USERNAME_PREFIX,
            date_joined__lt=cutoff,
            is_staff=False,
            is_superuser=False,
        )
        user_count = stale_users.count()
        if not user_count:
            self.stdout.write("No expired Demo customers found.")
            return

        with transaction.atomic():
            # Order.user is PROTECT, so owned orders must be removed first.
            Order.objects.filter(user__in=stale_users).delete()
            deleted_count, _ = stale_users.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Removed {deleted_count} expired Demo customer records "
                f"({user_count} visitor accounts)."
            )
        )
