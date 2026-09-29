"""
Webhook-сервер для отримання підтверджень оплати від LiqPay
Запускається поруч із ботом або як окремий процес

LiqPay надсилає POST на /liqpay/webhook з полями:
  data      — base64-encoded JSON з параметрами платежу
  signature — підпис SHA1 для перевірки автентичності
"""
import logging
import os
import sys
import asyncio
import json

from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from database import init_db, confirm_payment, get_payment_by_order, get_question_by_id
from liqpay_helper import verify_webhook, decode_webhook_data

log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# =====================================================================
# WEBHOOK HANDLER
# =====================================================================

async def liqpay_webhook(request: web.Request) -> web.Response:
    """
    Приймає POST від LiqPay, перевіряє підпис,
    та якщо статус «success» — підтверджує платіж у БД
    і відправляє повідомлення користувачу в Telegram
    """
    try:
        form      = await request.post()
        data_b64  = form.get("data", "")
        signature = form.get("signature", "")

        # 1. Перевіряємо автентичність підпису
        if not verify_webhook(data_b64, signature):
            log.warning("❌ Невалідний підпис від LiqPay!")
            return web.Response(text="invalid signature", status=400)

        # 2. Декодуємо дані
        payload  = decode_webhook_data(data_b64)
        status   = payload.get("status")
        order_id = payload.get("order_id")

        log.info(f"LiqPay webhook: order={order_id}, status={status}")

        # 3. Обробляємо тільки успішні платежі
        if status == "success" and order_id:
            raw_json    = json.dumps(payload, ensure_ascii=False)
            telegram_id = confirm_payment(order_id, raw_json, paid_amount=payload.get("amount"))

            if telegram_id:
                log.info(f"✅ Платіж підтверджено: {order_id} → user {telegram_id}")
                await notify_user(telegram_id, order_id)
            else:
                log.warning(f"Платіж {order_id} не знайдено, вже оброблено або сума не збігається")

        return web.Response(text="ok", status=200)

    except Exception as e:
        log.exception(f"Помилка обробки webhook: {e}")
        return web.Response(text="error", status=500)


async def notify_user(telegram_id: int, order_id: str = None):
    """Після оплати: повідомлення з кнопкою «Отримати PDF» (PDF генерує бот)."""
    if not BOT_TOKEN:
        return

    import aiohttp as _aiohttp

    title, qid = "", None
    try:
        pay = get_payment_by_order(order_id) if order_id else None
        if pay and pay.get("item_type") == "question":
            qid = pay["item_id"]
            q = get_question_by_id(qid)
            if q:
                import html as _h
                title = f"\n\n<b>{_h.escape(q.get('code') or '')}</b> · {_h.escape(q['title'])}"
    except Exception as e:
        log.warning(f"Не вдалося знайти питання для {order_id}: {e}")

    text = "🎉 <b>Оплату отримано, дякуємо!</b>" + title + "\n\nНатисніть кнопку — бот надішле ваш PDF 👇"
    params = {"chat_id": telegram_id, "text": text, "parse_mode": "HTML"}
    if qid:
        params["reply_markup"] = {"inline_keyboard": [[{"text": "📄 Отримати PDF", "callback_data": f"showq:{qid}"}]]}
    else:
        params["text"] = "🎉 <b>Оплату отримано, дякуємо!</b>\n\nВідкрийте /mystatus, щоб отримати PDF."

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with _aiohttp.ClientSession() as session:
        async with session.post(url, json=params) as resp:
            if resp.status != 200:
                log.warning(f"Не вдалось надіслати повідомлення: {await resp.text()}")


# =====================================================================
# ЗАПУСК
# =====================================================================

def create_app():
    app = web.Application()
    app.router.add_post("/liqpay/webhook", liqpay_webhook)
    # health-check для Heroku
    app.router.add_get("/health", lambda r: web.Response(text="OK"))
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    port = int(os.getenv("PORT", 8080))
    log.info(f"🚀 Webhook-сервер запущено на порту {port}")
    web.run_app(create_app(), host="0.0.0.0", port=port)
