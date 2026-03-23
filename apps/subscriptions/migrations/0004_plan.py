from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0003_paymentmethod_latam_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='Plan',
            fields=[
                ('id', models.CharField(choices=[('free', 'Free'), ('starter', 'Starter'), ('professional', 'Professional'), ('enterprise', 'Enterprise')], max_length=20, primary_key=True, serialize=False)),
                ('display_name', models.CharField(max_length=100)),
                ('description', models.CharField(blank=True, max_length=300)),
                ('price_monthly', models.IntegerField(default=0)),
                ('price_annual', models.IntegerField(default=0)),
                ('popular', models.BooleanField(default=False)),
                ('highlights', models.JSONField(default=list)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['price_monthly'],
            },
        ),
    ]
