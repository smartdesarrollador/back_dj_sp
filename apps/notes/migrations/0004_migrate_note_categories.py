from django.db import migrations

_LABELS = {
    'work': ('Trabajo', '#3b82f6'),
    'personal': ('Personal', '#10b981'),
    'ideas': ('Ideas', '#f59e0b'),
    'archive': ('Archivo', '#6b7280'),
}


def migrate_categories_forward(apps, schema_editor):
    """Convert the legacy fixed-choice `category` string into real, per-user
    NoteCategory rows, and repoint each note's new `category` FK at them."""
    Note = apps.get_model('notes', 'Note')
    NoteCategory = apps.get_model('notes', 'NoteCategory')

    category_cache: dict[tuple, object] = {}
    notes = Note.objects.exclude(category_legacy='').exclude(category_legacy__isnull=True)
    for note in notes.iterator():
        legacy = note.category_legacy
        label, color = _LABELS.get(legacy, (legacy, 'gray'))
        cache_key = (note.tenant_id, note.user_id, label)
        category = category_cache.get(cache_key)
        if category is None:
            category, _ = NoteCategory.objects.get_or_create(
                tenant_id=note.tenant_id,
                user_id=note.user_id,
                name=label,
                defaults={'color': color},
            )
            category_cache[cache_key] = category
        note.category_id = category.id
        note.save(update_fields=['category'])


def migrate_categories_backward(apps, schema_editor):
    """Best-effort reverse: copy the category name back into category_legacy."""
    Note = apps.get_model('notes', 'Note')
    for note in Note.objects.select_related('category').iterator():
        note.category_legacy = note.category.name if note.category else ''
        note.save(update_fields=['category_legacy'])


class Migration(migrations.Migration):

    dependencies = [
        ('notes', '0003_notecategory'),
    ]

    operations = [
        migrations.RunPython(migrate_categories_forward, migrate_categories_backward),
    ]
