from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('digital_services', '0003_landingtemplate_social_links_accent_color'),
    ]

    operations = [
        migrations.AddField(
            model_name='portfolioitem',
            name='is_published',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='portfolioitem',
            name='category',
            field=models.CharField(
                blank=True,
                max_length=30,
                choices=[
                    ('web', 'Web'), ('mobile', 'Mobile'), ('design', 'Diseño'),
                    ('branding', 'Branding'), ('data', 'Datos'),
                    ('consulting', 'Consultoría'), ('other', 'Otro'),
                ],
            ),
        ),
        migrations.AddField(
            model_name='portfolioitem',
            name='client_name',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='portfolioitem',
            name='technologies',
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name='portfolioitem',
            name='duration',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='portfolioitem',
            name='status',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('completed', 'Completado'),
                    ('in_progress', 'En progreso'),
                    ('archived', 'Archivado'),
                ],
                default='completed',
            ),
        ),
        migrations.AddField(
            model_name='portfolioitem',
            name='accent_color',
            field=models.CharField(blank=True, max_length=7),
        ),
    ]
