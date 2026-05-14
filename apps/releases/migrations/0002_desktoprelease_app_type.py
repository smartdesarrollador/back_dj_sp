from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('releases', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='desktoprelease',
            name='app_type',
            field=models.CharField(
                choices=[('tauri', 'Tauri Desktop'), ('sidebar', 'Sidebar Offline')],
                default='tauri',
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name='desktoprelease',
            name='unique_release_version_platform',
        ),
        migrations.AddConstraint(
            model_name='desktoprelease',
            constraint=models.UniqueConstraint(
                fields=['version', 'platform', 'app_type'],
                name='unique_release_version_platform_apptype',
            ),
        ),
    ]
