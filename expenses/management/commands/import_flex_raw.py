"""Load HP Flex raw exports into FlexRawRecord.

    python manage.py import_flex_raw "ASPS01461_AUG 2026.xlsx" [more.xlsx ...]

Each export is cumulative and the import upserts on Ticket No, so loading the
newest file is enough; older ones can be loaded first and are simply overwritten.
"""
import os

from django.core.management.base import BaseCommand, CommandError

from expenses import raw_rates


class Command(BaseCommand):
    help = 'Import HP Flex raw export workbook(s) used to price Engineer P&L calls.'

    def add_arguments(self, parser):
        parser.add_argument('files', nargs='+')

    def handle(self, *args, **opts):
        for path in opts['files']:
            if not os.path.isfile(path):
                raise CommandError(f'No such file: {path}')
            with open(path, 'rb') as fh:
                try:
                    res = raw_rates.import_workbook(fh, os.path.basename(path))
                except ValueError as e:
                    raise CommandError(f'{path}: {e}')
            self.stdout.write(f"{os.path.basename(path)}: {res['rows']} calls "
                              f"({res['created']} new, {res['updated']} updated, {res['skipped']} skipped)")
        s = raw_rates.status()
        self.stdout.write(f"Raw data now holds {s['count']} calls closed {s['closed_from']} to {s['closed_to']}.")
