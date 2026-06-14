import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('digital_services', '0007_landingtemplate_theme_colors'),
    ]

    operations = [
        migrations.CreateModel(
            name='PortfolioSettings',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('theme_colors', models.JSONField(
                    blank=True,
                    default=dict,
                    help_text='{"header_bg":"","header_text":"","accent":"","nav_bg":""}',
                )),
                ('profile', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='portfolio_settings',
                    to='digital_services.publicprofile',
                )),
            ],
            options={
                'db_table': 'portfolio_settings',
            },
        ),
    ]
