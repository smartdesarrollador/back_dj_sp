# PASO 24 — PaymentMethod LATAM fields + nullable stripe_payment_method_id

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0002_subscription_credit_balance'),
    ]

    operations = [
        # Make stripe_payment_method_id nullable to allow LATAM methods without a Stripe ID
        migrations.AlterField(
            model_name='paymentmethod',
            name='stripe_payment_method_id',
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='paymentmethod',
            name='external_type',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='paymentmethod',
            name='external_email',
            field=models.EmailField(blank=True),
        ),
        migrations.AddField(
            model_name='paymentmethod',
            name='external_phone',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='paymentmethod',
            name='external_account_id',
            field=models.TextField(blank=True),
        ),
    ]
