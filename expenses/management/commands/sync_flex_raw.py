"""Pull the Flex raw data from the raw-data dashboard (RAW_DATA_URL,
default https://rawdata.systimus.in/work_orders.json).

    python manage.py sync_flex_raw

The board already does this by itself every 30 minutes; this is for a cron job
or for loading it by hand.
"""
from django.core.management.base import BaseCommand, CommandError

from expenses import raw_rates


class Command(BaseCommand):
    help = 'Sync Engineer P&L raw data from the raw-data dashboard.'

    def handle(self, *args, **opts):
        res = raw_rates.sync_from_dashboard(force=True)
        if not res.get('ok'):
            raise CommandError(res.get('error') or 'sync failed')
        if res.get('busy'):
            self.stdout.write('A sync is already running.')
            return
        self.stdout.write(f"{res.get('created', 0)} new, {res.get('updated', 0)} updated, "
                          f"{res.get('unchanged', 0)} unchanged ({raw_rates.raw_data_url()})")
        s = raw_rates.status()
        self.stdout.write(f"Raw data now holds {s['count']} calls closed {s['closed_from']} to {s['closed_to']}.")
