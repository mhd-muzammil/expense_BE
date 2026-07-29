from django.db import migrations


def hdfc_to_idfc(apps, schema_editor):
    """Rename the old 'hdfc' bank/section key to 'idfc' in any existing data:
    imported statement rows and per-user allowed_sections lists."""
    BankStatementEntry = apps.get_model('expenses', 'BankStatementEntry')
    BankStatementEntry.objects.filter(bank='hdfc').update(bank='idfc')

    UserProfile = apps.get_model('expenses', 'UserProfile')
    for profile in UserProfile.objects.all():
        sections = profile.allowed_sections or []
        if 'hdfc' in sections:
            profile.allowed_sections = ['idfc' if s == 'hdfc' else s for s in sections]
            profile.save(update_fields=['allowed_sections'])


def idfc_to_hdfc(apps, schema_editor):
    BankStatementEntry = apps.get_model('expenses', 'BankStatementEntry')
    BankStatementEntry.objects.filter(bank='idfc').update(bank='hdfc')

    UserProfile = apps.get_model('expenses', 'UserProfile')
    for profile in UserProfile.objects.all():
        sections = profile.allowed_sections or []
        if 'idfc' in sections:
            profile.allowed_sections = ['hdfc' if s == 'idfc' else s for s in sections]
            profile.save(update_fields=['allowed_sections'])


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0020_alter_bankstatemententry_bank'),
    ]

    operations = [
        migrations.RunPython(hdfc_to_idfc, idfc_to_hdfc),
    ]
