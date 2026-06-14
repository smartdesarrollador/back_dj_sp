"""Add YapePaymentProof model for manual Yape payment verification flow."""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0004_plan'),
    ]

    operations = [
        migrations.CreateModel(
            name='YapePaymentProof',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('screenshot', models.ImageField(upload_to='yape_proofs/')),
                ('plan', models.CharField(
                    choices=[('free', 'Free'), ('starter', 'Starter'), ('professional', 'Professional'), ('enterprise', 'Enterprise')],
                    max_length=20,
                )),
                ('amount', models.DecimalField(decimal_places=2, max_digits=8)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending Review'), ('approved', 'Approved'), ('rejected', 'Rejected')],
                    default='pending',
                    max_length=10,
                )),
                ('admin_token', models.CharField(db_index=True, max_length=64, unique=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('subscription', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='yape_proofs',
                    to='subscriptions.subscription',
                )),
            ],
            options={
                'db_table': 'yape_payment_proofs',
            },
        ),
        migrations.AddIndex(
            model_name='yapepaymentproof',
            index=models.Index(fields=['status'], name='yape_proof_status_idx'),
        ),
    ]
