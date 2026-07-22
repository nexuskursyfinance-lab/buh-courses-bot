"""
Модуль інтеграції з LiqPay
Генерація посилань для оплати та перевірка підпису webhook
"""
import base64
import hashlib
import json
import os

LIQPAY_PUBLIC  = os.getenv("LIQPAY_PUBLIC", "sandbox_public_key")
LIQPAY_PRIVATE = os.getenv("LIQPAY_SECRET", "sandbox_private_key")
PRICE          = 99.00
CURRENCY       = "UAH"


def _sign(data: str) -> str:
    """Генерує підпис SHA1 для LiqPay"""
    raw = LIQPAY_PRIVATE + data + LIQPAY_PRIVATE
    return base64.b64encode(hashlib.sha1(raw.encode()).digest()).decode()


def generate_payment_url(order_id: str, telegram_id: int, description: str = "Бухгалтерські питання 100+") -> str:
    """
    Повертає URL форми оплати LiqPay.
    Параметри відповідають документації LiqPay API v3.
    """
    # Базова URL для переходу після оплати
    result_url  = os.getenv("RESULT_URL", "https://t.me/your_bot")
    server_url  = os.getenv("SERVER_URL", "https://your-app.herokuapp.com/liqpay/webhook")

    params = {
        "action":      "pay",
        "amount":      str(PRICE),
        "currency":    CURRENCY,
        "description": description,
        "order_id":    order_id,
        "version":     "3",
        "public_key":  LIQPAY_PUBLIC,
        "result_url":  result_url,
        "server_url":  server_url,
        # Передаємо telegram_id щоб знати кому надати доступ після webhook
        "info":        str(telegram_id),
    }

    # Кодуємо параметри у base64
    data_encoded = base64.b64encode(json.dumps(params).encode()).decode()
    signature    = _sign(data_encoded)

    # Готове посилання для оплати
    url = (
        f"https://www.liqpay.ua/api/3/checkout"
        f"?data={data_encoded}&signature={signature}"
    )
    return url


def verify_webhook(data: str, signature: str) -> bool:
    """
    Перевіряє підпис webhook від LiqPay.
    Повертає True якщо підпис валідний.
    """
    expected = _sign(data)
    return expected == signature


def decode_webhook_data(data: str) -> dict:
    """Декодує base64 дані з webhook та повертає словник"""
    try:
        decoded = base64.b64decode(data).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}
