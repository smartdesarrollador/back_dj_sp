"""
Acceso único a la configuración de los métodos de pago manual.

Nadie consulta `PaymentMethodConfig` directo: todo pasa por estos helpers. Es la misma
regla que ya aplica `utils/currency.py` con el tipo de cambio, y por el mismo motivo —
cuando el Admin y el Hub empiecen a leer estos datos, un acceso suelto al modelo se
convierte en un lector huérfano que se salta las reglas (LL-049).

Regla de negocio central: **un método solo se ofrece si está habilitado Y tiene su dato
identificador**. Un método publicado sin el dato al que pagar es peor que no ofrecerlo:
el cliente llega hasta el final del flujo y se queda sin poder pagar.
"""
from apps.subscriptions.models import PAYMENT_METHOD_CHOICES, PaymentMethodConfig

PAYMENT_METHODS = [code for code, _ in PAYMENT_METHOD_CHOICES]

# Dato sin el cual el método no se puede ofrecer. PayPal admite dos formas de cobrar
# (enlace de pago o correo), así que le basta con una de las dos.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    'yape':   ('phone',),
    'paypal': ('checkout_url', 'account_email'),
}

# En qué moneda mueve dinero cada método. Yape mueve soles; PayPal, dólares. Decide dos
# cosas: qué importe se le pide al cliente en el Hub, y si tiene sentido guardar el
# testigo del cobro en soles junto al comprobante. Anotarle «S/ 71.25» a un pago hecho
# en dólares sería fabricar un dato histórico — justo lo que ese testigo existe para
# impedir (ver ADR-009).
BASE_CURRENCY = 'USD'
CHARGE_CURRENCY: dict[str, str] = {
    'yape':   'PEN',
    'paypal': 'USD',
}

# Métodos que emiten una referencia verificable (el ID de transacción de PayPal) y que,
# por tanto, deben exigirla: es lo que permite confirmar el pago contra el panel del
# proveedor en vez de fiarse solo de una captura. Yape no da ninguna.
REQUIRES_REFERENCE: tuple[str, ...] = ('paypal',)


def is_configured(config: PaymentMethodConfig) -> bool:
    """¿Tiene el método el dato mínimo para que alguien pueda pagarle?"""
    required = REQUIRED_FIELDS.get(config.method, ())
    if not required:
        return True
    return any(getattr(config, field, '') for field in required)


def get_method_config(method: str) -> PaymentMethodConfig | None:
    """Configuración de un método concreto, o `None` si no existe la fila."""
    return PaymentMethodConfig.objects.filter(method=method).first()


def get_enabled_methods() -> list[PaymentMethodConfig]:
    """
    Métodos ofrecibles al cliente, en su orden de presentación.

    Filtra por `is_enabled` **y** por configuración completa: si un admin habilita un
    método y luego le borra el teléfono, deja de ofrecerse en vez de mostrar un destino
    de pago vacío.
    """
    return [
        config
        for config in PaymentMethodConfig.objects.filter(is_enabled=True)
        if is_configured(config)
    ]


def accepts_proofs(method: str) -> bool:
    """
    Si se admite un comprobante de este método.

    Comprueba `is_enabled` pero **no** `is_configured`, a diferencia de
    `get_enabled_methods()`. Ofrecer un método y aceptar un pago ya hecho son cosas
    distintas: si un admin borra el teléfono de Yape, deja de ofrecerse a nuevos
    clientes, pero rechazar el comprobante de alguien que ya transfirió —porque vio
    el dato cuando sí estaba— sería castigarle por un cambio de configuración ajeno.

    El guardarraíl que importa aquí es el otro: un método deshabilitado no admite
    comprobantes, y eso es lo que impide que llegue un recibo de un método que el
    resto del sistema (verificación automática, panel) aún no sabe tratar.
    """
    config = get_method_config(method)
    return config is not None and config.is_enabled


def charge_currency(method: str) -> str:
    """
    Moneda en la que cobra el método. Los métodos desconocidos caen en la moneda base,
    que es la única en la que siempre se puede expresar un precio del catálogo.
    """
    return CHARGE_CURRENCY.get(method, BASE_CURRENCY)


def charges_in_pen(method: str) -> bool:
    """Si el cliente transfiere soles — y por tanto hay conversión que registrar."""
    return charge_currency(method) == 'PEN'


def requires_reference(method: str) -> bool:
    """Si el comprobante de este método debe traer su referencia de transacción."""
    return method in REQUIRES_REFERENCE


def serialize_public(config: PaymentMethodConfig) -> dict:
    """
    Lo que el cliente necesita para pagar. Los campos que no aplican al método van
    vacíos en vez de omitirse, para que el contrato sea estable entre métodos.

    `charge_currency` y `requires_reference` viajan al Hub en vez de duplicar allí las
    tablas: un método nuevo se añade aquí y el frontend lo trata bien sin desplegarse.
    """
    return {
        'method':            config.method,
        'display_name':      config.display_name,
        'charge_currency':   charge_currency(config.method),
        'requires_reference': requires_reference(config.method),
        'holder_name':       config.holder_name,
        'phone':             config.phone,
        'checkout_url':      config.checkout_url,
        'account_email':     config.account_email,
        'instructions_note': config.instructions_note,
    }
