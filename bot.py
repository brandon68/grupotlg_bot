import html
import logging
import os
import re
import time
from collections import defaultdict, deque
from typing import Any

import httpx
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

TOKEN = os.environ["8793668537:AAFWNp-dDrPWIoB3hco3dvj3NPVRBAlAt9I"]

# Ejemplo:
# https://mi-bot-production.up.railway.app
RAILWAY_PUBLIC_DOMAIN = os.environ["grupotlgbot-production.up.railway.app"]

PORT = int(os.getenv("PORT", "8080"))

WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
WEBHOOK_PATH = f"telegram/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"https://{RAILWAY_PUBLIC_DOMAIN}/{WEBHOOK_PATH}"

BINLIST_URL = "https://lookup.binlist.net/{bin_number}"

# Opcional: restringir el bot a un grupo específico.
# Déjalo vacío inicialmente para obtener el ID con /chatid.
ALLOWED_CHAT_ID_RAW = os.getenv("ALLOWED_CHAT_ID", "").strip()
ALLOWED_CHAT_ID = (
    int(ALLOWED_CHAT_ID_RAW)
    if ALLOWED_CHAT_ID_RAW
    else None
)

# Límite por usuario.
MAX_REQUESTS = 5
RATE_LIMIT_SECONDS = 60

solicitudes: dict[int, deque[float]] = defaultdict(deque)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# SEGURIDAD Y VALIDACIONES
# ============================================================

def grupo_autorizado(update: Update) -> bool:
    chat = update.effective_chat

    if chat is None:
        return False

    if ALLOWED_CHAT_ID is None:
        return True

    return chat.id == ALLOWED_CHAT_ID


def excedio_limite(user_id: int) -> bool:
    ahora = time.monotonic()
    historial = solicitudes[user_id]

    while historial and ahora - historial[0] > RATE_LIMIT_SECONDS:
        historial.popleft()

    if len(historial) >= MAX_REQUESTS:
        return True

    historial.append(ahora)
    return False


def limpiar(valor: Any, defecto: str = "No disponible") -> str:
    if valor is None:
        return defecto

    resultado = str(valor).strip()

    return resultado if resultado else defecto


def mayusculas(valor: Any) -> str:
    resultado = limpiar(valor)

    if resultado == "No disponible":
        return resultado

    return resultado.upper()


# ============================================================
# CONSULTA DE BIN
# ============================================================

async def consultar_bin(bin_number: str) -> dict[str, Any]:
    headers = {
        "Accept-Version": "3",
        "User-Agent": "TelegramBINBot/1.0",
    }

    async with httpx.AsyncClient(timeout=15) as cliente:
        respuesta = await cliente.get(
            BINLIST_URL.format(bin_number=bin_number),
            headers=headers,
        )

    if respuesta.status_code == 404:
        raise ValueError("BIN no encontrado")

    if respuesta.status_code == 429:
        raise RuntimeError("Límite de la API alcanzado")

    respuesta.raise_for_status()

    datos = respuesta.json()

    banco = datos.get("bank") or {}
    pais = datos.get("country") or {}

    prepaid = datos.get("prepaid")

    if prepaid is True:
        prepaid_texto = "SÍ"
    elif prepaid is False:
        prepaid_texto = "NO"
    else:
        prepaid_texto = "NO DISPONIBLE"

    return {
        "bin": bin_number,
        "bank_name": banco.get("name"),
        "scheme": datos.get("scheme"),
        "card_type": datos.get("type"),
        "card_level": datos.get("brand"),
        "prepaid": prepaid_texto,
        "country_name": pais.get("name"),
        "country_alpha2": pais.get("alpha2"),
        "country_numeric": pais.get("numeric"),
        "currency": pais.get("currency"),
        "bank_url": banco.get("url"),
        "bank_phone": banco.get("phone"),
        "bank_city": banco.get("city"),
    }


def formatear_resultado(datos: dict[str, Any]) -> str:
    campos = [
        ("BIN", datos.get("bin")),
        ("Issuing Bank", datos.get("bank_name")),
        ("Card Brand", datos.get("scheme")),
        ("Card Type", datos.get("card_type")),
        ("Card Level", datos.get("card_level")),
        ("Prepaid", datos.get("prepaid")),
        ("ISO Country Name", datos.get("country_name")),
        ("ISO Country A2", datos.get("country_alpha2")),
        ("ISO Country Number", datos.get("country_numeric")),
        ("Currency", datos.get("currency")),
        ("Bank City", datos.get("bank_city")),
        ("Issuers Website", datos.get("bank_url")),
        ("Issuers Contact", datos.get("bank_phone")),
    ]

    lineas = ["💳 <b>BIN CHECKER</b>", ""]

    for nombre, valor in campos:
        if nombre == "Issuers Website":
            valor_limpio = limpiar(valor)
        else:
            valor_limpio = mayusculas(valor)

        lineas.append(
            f"<b>{html.escape(nombre)}:</b> "
            f"<code>{html.escape(valor_limpio)}</code>"
        )

    return "\n".join(lineas)


# ============================================================
# COMANDOS
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.effective_message.reply_text(
        "🤖 <b>BIN Checker</b>\n\n"
        "Utiliza:\n"
        "<code>/bin 421216</code>\n\n"
        "Solo se aceptan BIN de 6 u 8 dígitos.",
        parse_mode=ParseMode.HTML,
    )


async def chatid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat = update.effective_chat

    if chat is None:
        return

    await update.effective_message.reply_text(
        f"ID de este chat:\n<code>{chat.id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def comando_bin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    mensaje = update.effective_message
    usuario = update.effective_user
    chat = update.effective_chat

    if mensaje is None or usuario is None or chat is None:
        return

    if chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
        ChatType.PRIVATE,
    }:
        return

    if not grupo_autorizado(update):
        await mensaje.reply_text(
            "❌ Este bot no está autorizado en este grupo."
        )
        return

    if excedio_limite(usuario.id):
        await mensaje.reply_text(
            "⏳ Alcanzaste el límite de consultas.\n"
            "Espera un minuto antes de intentarlo nuevamente."
        )
        return

    if not context.args:
        await mensaje.reply_text(
            "Uso correcto:\n"
            "<code>/bin 421216</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    entrada = "".join(context.args)
    bin_number = re.sub(r"\D", "", entrada)

    if len(bin_number) not in {6, 8}:
        await mensaje.reply_text(
            "❌ El BIN debe contener exactamente 6 u 8 dígitos."
        )
        return

    estado = await mensaje.reply_text(
        f"🔎 Consultando <code>{bin_number}</code>...",
        parse_mode=ParseMode.HTML,
    )

    try:
        datos = await consultar_bin(bin_number)
        resultado = formatear_resultado(datos)

        await estado.edit_text(
            resultado,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    except ValueError:
        await estado.edit_text(
            f"❌ No se encontró información para el BIN {bin_number}."
        )

    except RuntimeError:
        await estado.edit_text(
            "⚠️ Se alcanzó el límite de la API de BIN."
        )

    except httpx.TimeoutException:
        await estado.edit_text(
            "⚠️ La consulta tardó demasiado."
        )

    except Exception:
        logger.exception("Error consultando el BIN")

        await estado.edit_text(
            "⚠️ Ocurrió un error al consultar el BIN."
        )


# ============================================================
# INICIO
# ============================================================

def main() -> None:
    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("chatid", chatid))
    application.add_handler(CommandHandler("bin", comando_bin))

    logger.info("Iniciando webhook en %s", WEBHOOK_URL)

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
