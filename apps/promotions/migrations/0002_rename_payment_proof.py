"""
El canje apunta al comprobante del pago manual, ya no al «comprobante Yape».

`RenameField` conserva la columna y sus datos; depende del renombrado del modelo en
`subscriptions`, porque la FK apunta a él.

El `AlterField` no es redundante: la referencia se declaró como string perezoso
(`'subscriptions.YapePaymentProof'`) desde otra app, y el `RenameModel` no la reescribe.
Sin él, el estado de migraciones queda apuntando a un modelo que ya no existe y
**cualquier** test que construya ese estado revienta antes de empezar.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('promotions', '0001_initial'),
        ('subscriptions', '0013_rename_payment_proof'),
    ]

    operations = [
        migrations.RenameField(
            model_name='promotionredemption',
            old_name='yape_proof',
            new_name='payment_proof',
        ),
        migrations.AlterField(
            model_name='promotionredemption',
            name='payment_proof',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='redemption',
                to='subscriptions.paymentproof',
            ),
        ),
    ]
