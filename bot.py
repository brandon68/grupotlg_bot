import datetime
import html
import logging
import os
import re
import time
from collections import defaultdict, deque
from typing import Any

import httpx
import pytz
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# CONFIGURACIÓN
# ============================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# Ejemplo: https://mi-bot-production.up.railway.app
RAILWAY_PUBLIC_DOMAIN = os.environ["RAILWAY_PUBLIC_DOMAIN"]

PORT = int(os.getenv("PORT", "8080"))

WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
WEBHOOK_PATH = f"telegram/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"https://{RAILWAY_PUBLIC_DOMAIN}/{WEBHOOK_PATH}"

BINLIST_URL = "https://lookup.binlist.net/{bin_number}"

# Restringir el bot a un grupo específico (Obligatorio para la publicidad)
ALLOWED_CHAT_ID_RAW = os.getenv("ALLOWED_CHAT_ID", "").strip()
ALLOWED_CHAT_ID = (
    int(ALLOWED_CHAT_ID_RAW)
    if ALLOWED_CHAT_ID_RAW
    else None
)

# Configura tu zona horaria
ZONA_HORARIA = pytz.timezone("America/Mexico_City")

# ============================================================
# CATÁLOGO DE PUBLICIDADES DIARIAS CON TUS IMÁGENES SUBIDAS
# ============================================================
CATALOGO_PUBLICIDAD = [
    {
        "hora": (18, 30),  # Se envía diariamente a las 10:00 AM
        "imagen": "ANUNCIOSPAM.jpg",  # Tu primera imagen
        "texto": (
            "🔥 <b>¡OFERTA ESPECIAL DEL DÍA!</b> 🔥\n\n"
            "Servicios disponibles las 24 horas del día con total garantía.\n\n"
            "📩 Contáctanos directamente con admin @juanper33z"
        )
    },
    {
        "hora": (18, 40),  # Se envía diariamente a las 06:00 PM (18:00 hrs)
        "imagen": "ANUNCIOIAREJAS.jpg",  # Tu segunda imagen
        "texto": (
            "⚡ <b>¡NO TE LO PIERDAS!</b> ⚡\n\n"
            "Aprovecha nuestras promociones exclusivas para la comunidad.\n\n"
            "📩 Para más detalles consulta con @juanper33z"
        )
    }
]

# Límite de consultas por usuario
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


def bandera_pais(codigo: Any) -> str:
    codigo = limpiar(codigo, "").upper()

    if len(codigo) != 2 or not codigo.isalpha():
        return "🌎"

    return "".join(
        chr(ord(letra) + 127397)
        for letra in codigo
    )


def formatear_resultado(datos: dict[str, Any]) -> str:
    bin_number = html.escape(limpiar(datos.get("bin")))
    banco = html.escape(mayusculas(datos.get("bank_name")))
    marca = html.escape(mayusculas(datos.get("scheme")))
    tipo = html.escape(mayusculas(datos.get("card_type")))
    nivel = html.escape(mayusculas(datos.get("card_level")))
    prepago = html.escape(mayusculas(datos.get("prepaid")))

    pais = html.escape(mayusculas(datos.get("country_name")))
    codigo_pais = mayusculas(datos.get("country_alpha2"))
    numero_pais = html.escape(mayusculas(datos.get("country_numeric")))
    moneda = html.escape(mayusculas(datos.get("currency")))

    ciudad = html.escape(mayusculas(datos.get("bank_city")))
    telefono = html.escape(mayusculas(datos.get("bank_phone")))

    web_banco = limpiar(datos.get("bank_url"))
    bandera = bandera_pais(datos.get("country_alpha2"))

    if web_banco != "No disponible":
        if not web_banco.startswith(("http://", "https://")):
            web_banco = f"https://{web_banco}"

        sitio_web = (
            f'<a href="{html.escape(web_banco, quote=True)}">'
            "Abrir sitio del banco</a>"
        )
    else:
        sitio_web = "NO DISPONIBLE"

    return (
        "💳 <b>BIN CHECKER</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"🔢 <b>BIN:</b> <code>{bin_number}</code>\n\n"

        "🏦 <b>INFORMACIÓN BANCARIA</b>\n"
        f"├ <b>Banco:</b> {banco}\n"
        f"├ <b>Marca:</b> {marca}\n"
        f"├ <b>Tipo:</b> {tipo}\n"
        f"├ <b>Nivel:</b> {nivel}\n"
        f"└ <b>Prepago:</b> {prepago}\n\n"

        f"{bandera} <b>INFORMACIÓN DEL PAÍS</b>\n"
        f"├ <b>País:</b> {pais}\n"
        f"├ <b>Código:</b> <code>{html.escape(codigo_pais)}</code>\n"
        f"├ <b>ISO numérico:</b> <code>{numero_pais}</code>\n"
        f"└ <b>Moneda:</b> <code>{moneda}</code>\n\n"

        "📞 <b>CONTACTO DEL EMISOR</b>\n"
        f"├ <b>Ciudad:</b> {ciudad}\n"
        f"├ <b>Sitio web:</b> {sitio_web}\n"
        f"└ <b>Teléfono:</b> {telefono}\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        '🤖 Bot de <a href="https://t.me/juanper33z">'
        "@juanper33z</a>"
    )


# ============================================================
# ENVIAR ANUNCIO PROGRAMADO
# ============================================================

async def enviar_anuncio(context: ContextTypes.DEFAULT_TYPE) -> None:
    if ALLOWED_CHAT_ID is None:
        logger.warning("No se definió ALLOWED_CHAT_ID. No se puede enviar la publicidad.")
        return

    anuncio = context.job.data
    imagen = anuncio["imagen"]
    texto = anuncio["texto"]

    try:
        if imagen.startswith(("http://", "https://")):
            await context.bot.send_photo(
                chat_id=ALLOWED_CHAT_ID,
                photo=imagen,
                caption=texto,
                parse_mode=ParseMode.HTML,
            )
        else:
            with open(imagen, "rb") as foto:
                await context.bot.send_photo(
                    chat_id=ALLOWED_CHAT_ID,
                    photo=foto,
                    caption=texto,
                    parse_mode=ParseMode.HTML,
                )
        logger.info("Publicidad enviada con éxito al grupo %s", ALLOWED_CHAT_ID)

    except FileNotFoundError:
        logger.error("No se encontró la imagen local: %s", imagen)
    except Exception as e:
        logger.exception("Error al enviar la publicidad: %s", e)


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


async def bienvenida(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    mensaje = update.effective_message
    chat = update.effective_chat

    if mensaje is None or chat is None:
        return

    if ALLOWED_CHAT_ID is not None and chat.id != ALLOWED_CHAT_ID:
        return

    for usuario in mensaje.new_chat_members:
        if usuario.id == context.bot.id:
            continue

        nombre = html.escape(usuario.full_name)

        if usuario.username:
            usuario_texto = (
                f'<a href="https://t.me/{html.escape(usuario.username)}">'
                f"@{html.escape(usuario.username)}</a>"
            )
        else:
            usuario_texto = (
                f'<a href="tg://user?id={usuario.id}">{nombre}</a>'
            )

        texto_bienvenida = (
            "👋 <b>¡BIENVENIDO AL GRUPO!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"Hola, {usuario_texto} 🎉\n\n"
            "Esperamos que disfrutes tu estancia y participes "
            "con respeto en la comunidad.\n\n"
            "📌 Revisa las reglas del grupo.\n"
            "🔴 Evita estafas y realiza tratos con admin @juanper33z.\n"
            "🔴 Todos pueden realizar ventas de sus productos a excepción de: \n"
            "- accesos, bot spam y cuentas steming \n"
            "💬 Convive respetuosamente con los demás miembros.\n\n"
            f"👥 Ahora somos <b>{await context.bot.get_chat_member_count(chat.id)}</b> miembros.\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            '🤖 Bot de <a href="https://t.me/juanper33z">'
            "@juanper33z</a>"
        )

        await mensaje.reply_text(
            texto_bienvenida,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
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

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("chatid", chatid))
    application.add_handler(CommandHandler("bin", comando_bin))
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS,
            bienvenida,
        )
    )

    # Programar todos los anuncios del catálogo
    if application.job_queue:
        for index, anuncio in enumerate(CATALOGO_PUBLICIDAD):
            hora, minuto = anuncio["hora"]

            tiempo_programado = datetime.time(
                hour=hora,
                minute=minuto,
                tzinfo=ZONA_HORARIA
            )

            application.job_queue.run_daily(
                enviar_anuncio,
                time=tiempo_programado,
                data=anuncio,
                name=f"publicidad_{index}"
            )
            logger.info("Publicidad %d programada a las %02d:%02d (%s)", index + 1, hora, minuto, ZONA_HORARIA)

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
