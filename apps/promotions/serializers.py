import re
from decimal import Decimal

from rest_framework import serializers

from .models import APPLICABLE_PLANS, Promotion

CODE_PATTERN = re.compile(r'^[A-Z0-9]{3,20}$')


class PromotionSerializer(serializers.ModelSerializer):
    """
    Serializer de lectura. Contrato exacto de la UI del Admin
    (frontend_admin/src/features/promotions/types.ts): la UI espera números,
    no strings, en los campos decimales.
    Las métricas provienen de annotations del queryset (ver admin_views).
    """
    value               = serializers.DecimalField(
        max_digits=10, decimal_places=2, coerce_to_string=False
    )
    max_discount        = serializers.DecimalField(
        max_digits=10, decimal_places=2, coerce_to_string=False,
        required=False, allow_null=True,
    )
    status              = serializers.CharField(read_only=True)
    conversion_rate     = serializers.SerializerMethodField()
    total_revenue       = serializers.SerializerMethodField()
    avg_discount_amount = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = [
            'id',
            'code',
            'name',
            'description',
            'type',
            'value',
            'max_discount',
            'applicable_plans',
            'new_customers_only',
            'starts_at',
            'expires_at',
            'max_uses',
            'max_uses_per_customer',
            'status',
            'current_uses',
            'last_used_at',
            'conversion_rate',
            'total_revenue',
            'avg_discount_amount',
            'created_at',
        ]
        read_only_fields = ['id', 'status', 'current_uses', 'last_used_at', 'created_at']

    def get_conversion_rate(self, obj: Promotion) -> float:
        confirmed = getattr(obj, 'confirmed_count', 0) or 0
        released = getattr(obj, 'released_count', 0) or 0
        resolved = confirmed + released
        if not resolved:
            return 0.0
        return round(confirmed / resolved * 100, 1)

    def get_total_revenue(self, obj: Promotion) -> float:
        return float(getattr(obj, 'revenue_sum', None) or 0)

    def get_avg_discount_amount(self, obj: Promotion) -> float:
        return round(float(getattr(obj, 'discount_avg', None) or 0), 2)


class PromotionWriteSerializer(PromotionSerializer):
    """
    Serializer de escritura (POST/PATCH). Acepta además un campo virtual
    `status` ("active" | "paused") que mapea a `is_paused` — los estados
    `expired`/`depleted` son computados y no seteables.
    """
    status = serializers.ChoiceField(choices=['active', 'paused'], required=False)

    def validate_code(self, value: str) -> str:
        code = value.strip().upper()
        if not CODE_PATTERN.match(code):
            raise serializers.ValidationError(
                'El código debe ser alfanumérico en mayúsculas (3-20 caracteres).'
            )
        if self.instance and code != self.instance.code:
            raise serializers.ValidationError('El código es inmutable tras la creación.')
        return code

    def validate_applicable_plans(self, value: list) -> list:
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError('Debe incluir al menos un plan.')
        invalid = [plan for plan in value if plan not in APPLICABLE_PLANS]
        if invalid:
            raise serializers.ValidationError(
                f'Planes inválidos: {", ".join(invalid)}. '
                f'Permitidos: {", ".join(APPLICABLE_PLANS)}.'
            )
        return value

    def validate_max_uses_per_customer(self, value: int) -> int:
        if value < 1:
            raise serializers.ValidationError('Debe ser al menos 1.')
        return value

    def validate(self, attrs: dict) -> dict:
        def current(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None) if self.instance else None

        promo_type = current('type')
        value = current('value')
        if value is not None:
            if promo_type == 'percentage' and not (1 <= value <= 100):
                raise serializers.ValidationError(
                    {'value': 'Para porcentaje, el valor debe estar entre 1 y 100.'}
                )
            if promo_type == 'fixed_amount' and value <= 0:
                raise serializers.ValidationError(
                    {'value': 'Para monto fijo, el valor debe ser mayor a 0.'}
                )

        max_discount = current('max_discount')
        if max_discount is not None:
            if promo_type != 'percentage':
                raise serializers.ValidationError(
                    {'max_discount': 'Solo aplica a promociones de porcentaje.'}
                )
            if max_discount <= Decimal('0'):
                raise serializers.ValidationError(
                    {'max_discount': 'Debe ser mayor a 0.'}
                )

        starts_at = current('starts_at')
        expires_at = current('expires_at')
        if starts_at and expires_at and starts_at >= expires_at:
            raise serializers.ValidationError(
                {'expires_at': 'La fecha de fin debe ser posterior a la de inicio.'}
            )

        max_uses = current('max_uses')
        if max_uses is not None:
            if max_uses < 1:
                raise serializers.ValidationError({'max_uses': 'Debe ser al menos 1.'})
            if self.instance and max_uses < self.instance.current_uses:
                raise serializers.ValidationError(
                    {'max_uses': (
                        f'No puede ser menor que los usos actuales '
                        f'({self.instance.current_uses}).'
                    )}
                )

        return attrs

    @staticmethod
    def _pop_status(validated_data: dict) -> bool | None:
        """`status` no es columna: se traduce a is_paused antes de tocar el modelo."""
        status = validated_data.pop('status', None)
        if status is None:
            return None
        return status == 'paused'

    def create(self, validated_data: dict) -> Promotion:
        is_paused = self._pop_status(validated_data)
        if is_paused is not None:
            validated_data['is_paused'] = is_paused
        return super().create(validated_data)

    def update(self, instance: Promotion, validated_data: dict) -> Promotion:
        is_paused = self._pop_status(validated_data)
        if is_paused is not None:
            validated_data['is_paused'] = is_paused
        return super().update(instance, validated_data)
