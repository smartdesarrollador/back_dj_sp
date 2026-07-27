"""
Retirada de la superficie «yape»: el comprobante deja de llamarse por el método que lo
originó, ahora que hay más de uno.

Escrita a mano a propósito. `makemigrations` no interactivo no sabe distinguir un
renombrado de un borrado+creación, y aquí esa diferencia es la de conservar o perder
todos los comprobantes: `RenameModel` mueve la tabla, `DeleteModel` la vacía.

El renombrado de `PromotionRedemption.yape_proof` vive en la migración hermana de
`promotions`, que depende de esta.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0012_payment_method_config'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='YapePaymentProof',
            new_name='PaymentProof',
        ),
        migrations.AlterModelTable(
            name='paymentproof',
            table='payment_proofs',
        ),
        migrations.RenameIndex(
            model_name='paymentproof',
            new_name='payment_proof_status_idx',
            old_name='yape_proof_status_idx',
        ),
        # `related_name`: `subscription.payment_proofs`. No toca el esquema, pero sin
        # esta operación el estado de migraciones y los modelos divergen.
        migrations.AlterField(
            model_name='paymentproof',
            name='subscription',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='payment_proofs',
                to='subscriptions.subscription',
            ),
        ),
        # Los archivos ya subidos conservan su ruta en la columna, así que siguen
        # sirviéndose desde media/yape_proofs/; solo los nuevos van a la carpeta nueva.
        migrations.AlterField(
            model_name='paymentproof',
            name='screenshot',
            field=models.ImageField(upload_to='payment_proofs/'),
        ),
        migrations.AlterField(
            model_name='paymentproof',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending Review'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                ],
                default='pending',
                max_length=10,
            ),
        ),
        # `YapeConfig` ya no guardaba datos de cobro —eso vive en PaymentMethodConfig
        # desde la Fase 1— y su último campo con uso, `exchange_rate`, quedó sustituido
        # por CurrencyConfig. Sin lectores en Python ni fuera de él, la tabla solo podía
        # confundir a quien mire la BD.
        migrations.DeleteModel(name='YapeConfig'),
    ]
