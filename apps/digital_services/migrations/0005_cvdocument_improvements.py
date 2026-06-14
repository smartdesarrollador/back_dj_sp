from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('digital_services', '0004_portfolioitem_improvements'),
    ]

    operations = [
        migrations.AddField(
            model_name='cvdocument',
            name='headline',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='cvdocument',
            name='location',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='cvdocument',
            name='website_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='cvdocument',
            name='linkedin_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='cvdocument',
            name='github_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='cvdocument',
            name='accent_color',
            field=models.CharField(blank=True, max_length=7),
        ),
        migrations.AddField(
            model_name='cvdocument',
            name='is_published',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='cvdocument',
            name='projects',
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name='cvdocument',
            name='volunteer',
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name='cvdocument',
            name='awards',
            field=models.JSONField(default=list),
        ),
    ]
