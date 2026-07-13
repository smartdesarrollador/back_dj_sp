from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0002_rename_tenants_slug_idx_tenants_slug_3181c2_idx_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('notes', '0002_note_tags'),
    ]

    operations = [
        migrations.CreateModel(
            name='NoteCategory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100)),
                ('color', models.CharField(default='blue', max_length=20)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='note_categories', to='tenants.tenant')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='note_categories', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'note_categories',
                'unique_together': {('user', 'name')},
            },
        ),
        migrations.RenameField(
            model_name='note',
            old_name='category',
            new_name='category_legacy',
        ),
        migrations.AddField(
            model_name='note',
            name='category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='notes',
                to='notes.notecategory',
            ),
        ),
    ]
