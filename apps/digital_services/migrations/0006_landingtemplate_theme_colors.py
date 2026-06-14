from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('digital_services', '0005_cvdocument_improvements'),
    ]

    operations = [
        migrations.AddField(
            model_name='landingtemplate',
            name='theme_colors',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='{"hero_bg":"","hero_text":"","button_bg":"","nav_bg":""}',
            ),
        ),
    ]
