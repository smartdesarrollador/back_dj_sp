"""
Fuente única del tipo de cambio y de la configuración de moneda de la plataforma.

USD es la moneda base: todo importe que se COBRA está y seguirá estando en USD
(Plan.price_*, PaymentProof.amount, PromotionRedemption.*, Invoice.amount_cents).

Excepción deliberada: `PaymentProof.amount_pen` e `Invoice.amount_pen_cents`
SÍ persisten un importe en soles, pero como **testigo histórico del cobro**, no
como fuente de verdad — quien paga por Yape transfiere soles y, si la tasa se
mueve entre el pago y la aprobación, sin esa foto el importe del screenshot deja
de cuadrar con el panel. Nunca se cobra, nunca se agrega, nunca se recalcula.
Ver capture_pen_snapshot().

Todo lector del tipo de cambio DEBE pasar por get_exchange_rate(). Acceder a
CurrencyConfig.usd_to_pen directo salta la caché y reintroduce el problema de
lectores huérfanos que ya costó una incidencia (LL-049).

Este módulo vive en utils/ y no en apps/subscriptions/ a propósito: así
apps/promotions/ puede importarlo a nivel de módulo sin invertir la dirección de
imports que declara apps/promotions/services.py ("subscriptions y auth_app
importan de aquí, nunca al revés"). El import del modelo se difiere aquí dentro,
igual que en utils/plans.py.
"""
from decimal import Decimal

BASE_CURRENCY = 'USD'
SUPPORTED_CURRENCIES = ('USD', 'PEN')

DEFAULT_USD_TO_PEN = Decimal('3.7500')

CURRENCY_CACHE_KEY = 'currency:config'
CURRENCY_CACHE_TTL = 300  # 5 min — mismo TTL que PLAN_LIMITS_CACHE_TTL (utils/plans.py)


def get_currency_config() -> dict:
    """
    Configuración de moneda vigente, cacheada 5 min.

    Devuelve primitivos serializables (str), no una instancia de modelo: así el
    valor cacheado sobrevive a un cambio de esquema entre despliegues sin tener
    que invalidar la caché a mano.

    Lectura pura: NO crea la fila si falta. El endpoint público es un GET y un
    GET no debe escribir; la fila la garantiza la data migration 0010, y si aun
    así faltara (BD restaurada de un dump viejo, migrate --fake) se degrada a los
    defaults de código en vez de reventar.
    """
    from django.core.cache import cache

    cached = cache.get(CURRENCY_CACHE_KEY)
    if cached is not None:
        return cached

    from apps.subscriptions.models import CurrencyConfig  # import diferido: orden de carga de apps

    cfg = CurrencyConfig.objects.filter(pk=1).first()
    data = {
        'usd_to_pen': str(cfg.usd_to_pen if cfg else DEFAULT_USD_TO_PEN),
        'default_display_currency': cfg.default_display_currency if cfg else BASE_CURRENCY,
        'source': cfg.source if cfg else 'manual',
        'updated_at': cfg.updated_at.isoformat() if cfg else None,
    }
    cache.set(CURRENCY_CACHE_KEY, data, timeout=CURRENCY_CACHE_TTL)
    return data


def get_exchange_rate(currency: str = 'PEN') -> Decimal:
    """
    Tipo de cambio desde USD hacia `currency`. USD→USD es 1 por definición.

    Raises:
        ValueError: moneda fuera de SUPPORTED_CURRENCIES.
    """
    if currency == BASE_CURRENCY:
        return Decimal('1')
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(f'Unsupported currency: {currency}')
    return Decimal(get_currency_config()['usd_to_pen'])


def capture_pen_snapshot(amount_usd: Decimal) -> tuple[Decimal, Decimal]:
    """
    Foto de la conversión a soles en ESTE instante: `(tasa, importe_pen)`.

    Único sitio que produce el testigo histórico del cobro — los dos endpoints que
    crean comprobantes lo llaman, para que no haya dos fórmulas divergiendo
    (LL-049).

    HALF_UP a 2 decimales a propósito: es el mismo redondeo que aplica el Hub al
    pintar el importe que el cliente teclea en Yape (`roundTo` en su
    `lib/currency.ts`). Con otro redondeo, un S/ 746.245 saldría 746.25 en pantalla
    y 746.24 en el panel, y el revisor vería un descuadre de un céntimo inventado
    por nosotros.

    Sobre el instante: en el flujo Yape el comprobante se crea DESPUÉS de la
    transferencia (el cliente ve el monto → abre Yape → paga → sube el screenshot).
    La foto es de minutos después del pago real, no del segundo exacto. Es una
    aproximación asumida: la alternativa —aceptar el importe en soles que mande el
    cliente— rompería la regla de que el monto siempre se calcula en servidor.
    """
    from decimal import ROUND_HALF_UP

    rate = get_exchange_rate('PEN')
    amount_pen = (amount_usd * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return rate, amount_pen


def invalidate_currency_cache() -> None:
    """Se llama desde CurrencyConfig.save(). Toda escritura debe pasar por ahí."""
    from django.core.cache import cache

    cache.delete(CURRENCY_CACHE_KEY)
