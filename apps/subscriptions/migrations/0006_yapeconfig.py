"""Add YapeConfig singleton model for managing Yape payment settings from the Admin Panel."""
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0005_yapepaymentproof'),
    ]

    operations = [
        migrations.CreateModel(
            name='YapeConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone', models.CharField(default='', max_length=30)),
                ('holder_name', models.CharField(default='', max_length=255)),
                ('is_enabled', models.BooleanField(default=True)),
                ('exchange_rate', models.DecimalField(decimal_places=2, default=Decimal('3.75'), max_digits=5)),
                ('instructions_note', models.TextField(blank=True, default='')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'yape_config',
            },
        ),
    ]
