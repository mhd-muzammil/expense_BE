from decimal import Decimal

from django.db import migrations, models


OLD_RATE = Decimal('350')
NEW_RATE = Decimal('420')


def raise_rate(apps, schema_editor):
    """Move every engineer still on the old rate onto the new one.

    Only rows holding exactly the previous default are touched. An engineer
    deliberately set to some other rate was set that way for a reason, and a
    blanket update would erase that without anyone noticing.
    """
    EngineerPnl = apps.get_model('expenses', 'EngineerPnl')
    EngineerPnl.objects.filter(per_call_rate=OLD_RATE).update(per_call_rate=NEW_RATE)


def lower_rate(apps, schema_editor):
    """Reverse: put back exactly the rows this migration changed."""
    EngineerPnl = apps.get_model('expenses', 'EngineerPnl')
    EngineerPnl.objects.filter(per_call_rate=NEW_RATE).update(per_call_rate=OLD_RATE)


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0026_subscription'),
    ]

    operations = [
        migrations.AlterField(
            model_name='engineerpnl',
            name='per_call_rate',
            field=models.DecimalField(decimal_places=2, default=Decimal('420'), max_digits=10),
        ),
        migrations.RunPython(raise_rate, lower_rate),
    ]
