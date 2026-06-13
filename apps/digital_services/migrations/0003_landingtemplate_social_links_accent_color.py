from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('digital_services', '0002_digitalcard_specialties_digitalcard_years_experience'),
    ]

    operations = [
        migrations.AddField(
            model_name='landingtemplate',
            name='social_links',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='{"linkedin":"","github":"","twitter":"","instagram":"","website":"","tiktok":""}',
            ),
        ),
        migrations.AddField(
            model_name='landingtemplate',
            name='accent_color',
            field=models.CharField(blank=True, default='', max_length=7),
        ),
    ]
