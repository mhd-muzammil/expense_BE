"""Create (or update) a Profit & Loss–only user.

Such a user can log in and see only the P&L report and the summary Dashboard —
never the raw expense ledger, imports/exports, or any write endpoint. That
restriction is enforced server-side via the 'pnl_only' Django group.

Usage:
    python manage.py create_pnl_user <username> <password>
    python manage.py create_pnl_user pnl Secret@123
    python manage.py create_pnl_user pnl Secret@123 --reset   # reset existing password
"""

from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand, CommandError

from expenses.views import PNL_ONLY_GROUP


class Command(BaseCommand):
    help = 'Create or update a P&L-only user (can only view the Profit & Loss report + Dashboard).'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Login username for the P&L user')
        parser.add_argument('password', type=str, help='Login password for the P&L user')
        parser.add_argument(
            '--reset',
            action='store_true',
            help='If the user already exists, reset their password to the one given.',
        )

    def handle(self, *args, **options):
        username = options['username'].strip()
        password = options['password']
        reset = options['reset']

        if not username or not password:
            raise CommandError('Both username and password are required.')

        group, _ = Group.objects.get_or_create(name=PNL_ONLY_GROUP)

        user, created = User.objects.get_or_create(username=username)
        if created:
            user.set_password(password)
            user.is_staff = False
            user.is_superuser = False
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Created P&L user '{username}'."))
        else:
            if reset:
                user.set_password(password)
                user.save()
                self.stdout.write(self.style.SUCCESS(f"Updated password for existing user '{username}'."))
            else:
                self.stdout.write(self.style.WARNING(
                    f"User '{username}' already exists. Password left unchanged "
                    f"(pass --reset to change it). Ensuring P&L-only access…"
                ))

        # Ensure the user is in the pnl_only group (idempotent).
        user.groups.add(group)

        # Safety: a superuser is never treated as P&L-only, so warn if they gave one.
        if user.is_superuser:
            self.stdout.write(self.style.WARNING(
                f"NOTE: '{username}' is a superuser, so the P&L-only restriction does NOT apply to them. "
                f"Use a non-superuser account for a restricted P&L login."
            ))

        self.stdout.write(self.style.SUCCESS(
            f"Done. '{username}' can now log in and will see only the P&L report + Dashboard."
        ))
