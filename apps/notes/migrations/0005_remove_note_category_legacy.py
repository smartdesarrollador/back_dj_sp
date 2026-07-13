from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('notes', '0004_migrate_note_categories'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='note',
            new_name='notes_tenant__677d74_idx',
            old_name='notes_tenant__3e3ab1_idx',
        ),
        migrations.RemoveField(
            model_name='note',
            name='category_legacy',
        ),
    ]
