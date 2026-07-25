"""
smtp_service.py

Cifrado reversible de la contraseña SMTP (ADR-07) y armado/envío del
correo de alerta (RF-19 a RF-21). Separado deliberadamente en dos
responsabilidades:

  1. Cifrado/descifrado (Fernet) — la clave vive en la variable de entorno
     SMTP_ENCRYPTION_KEY, nunca en la base de datos ni en el código.
  2. Envío del correo — recibe la configuración ya resuelta (dict) y la
     lista de destinatarios; no abre conexiones a la base de datos por sí
     mismo, excepto para marcar el evento como notificado tras un envío
     exitoso en background (ver send_alert_email_in_background).

El envío corre en un hilo aparte (fire-and-forget, sin reintento
automático) para no bloquear camera_loop() durante el handshake SMTP,
que puede tardar 1-2 segundos.
"""

import os
import ssl
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from typing import Optional, Callable

from cryptography.fernet import Fernet, InvalidToken

from app import db


class SmtpEncryptionKeyMissing(Exception):
    """La variable de entorno SMTP_ENCRYPTION_KEY no está definida."""


class SmtpDecryptionError(Exception):
    """La contraseña cifrada no pudo descifrarse con la clave actual."""


# ----------------------------------------------------------------------------
# Cifrado / descifrado (ADR-07)
# ----------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    key = os.environ.get("SMTP_ENCRYPTION_KEY")
    if not key:
        raise SmtpEncryptionKeyMissing(
            "La variable de entorno SMTP_ENCRYPTION_KEY no está definida. "
            "Generarla con init_db.py y exportarla antes de iniciar Flask."
        )
    key_bytes = key.encode() if isinstance(key, str) else key
    return Fernet(key_bytes)


def encrypt_password(plain_password: str) -> str:
    """Cifra la contraseña SMTP antes de guardarla (nunca en texto plano)."""
    return _get_fernet().encrypt(plain_password.encode()).decode()


def decrypt_password(encrypted_password: str) -> str:
    """Descifra la contraseña SMTP para poder autenticarse ante el servidor."""
    try:
        return _get_fernet().decrypt(encrypted_password.encode()).decode()
    except InvalidToken as exc:
        raise SmtpDecryptionError(
            "No se pudo descifrar la contraseña SMTP: la clave de cifrado "
            "actual no coincide con la usada al guardarla."
        ) from exc


# ----------------------------------------------------------------------------
# Armado del mensaje de alerta
# ----------------------------------------------------------------------------

_ESTADO_LEGIBLE = {"in_stock": "con stock", "out_of_stock": "sin stock"}


def build_alert_message(event: dict) -> tuple:
    """
    Construye asunto y cuerpo del correo a partir de una fila de
    stock_events. Devuelve (subject, body).
    """
    previous = _ESTADO_LEGIBLE.get(event["previous_state"], event["previous_state"])
    new = _ESTADO_LEGIBLE.get(event["new_state"], event["new_state"])

    subject = f"Alerta de stock: {event['roi_name_snapshot']} ahora está {new}"
    body = (
        f"Zona: {event['roi_name_snapshot']}\n"
        f"Cambio de estado: {previous} -> {new}\n"
        f"Promedio de conteo que confirmó el cambio: {event.get('avg_total_objects')}\n"
        f"Momento del evento: {event.get('timestamp')}\n"
    )
    return subject, body


# ----------------------------------------------------------------------------
# Envío
# ----------------------------------------------------------------------------

def _build_message(recipients: list, subject: str, body: str, from_addr: str,
                    attachment_path: Optional[str] = None) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            # _subtype="jpeg" explícito: todas las imágenes de evento se
            # guardan como .jpg (ver cv2.imwrite en backend.py), y dejar que
            # MIMEImage adivine el formato por contenido falla si los bytes
            # no traen una cabecera JPEG reconocible.
            img = MIMEImage(f.read(), _subtype="jpeg")
            img.add_header("Content-Disposition", "attachment",
                            filename=os.path.basename(attachment_path))
            msg.attach(img)
    return msg


def send_email(smtp_config: dict, recipients: list, subject: str, body: str,
               attachment_path: Optional[str] = None) -> None:
    """
    Envía un correo de forma SÍNCRONA. Lanza la excepción original de
    smtplib/OSError si falla — el llamador decide cómo manejarla (ver
    send_alert_email_in_background para la variante no bloqueante).

    Elige automáticamente el mecanismo de cifrado según el puerto:
    - Puerto 465: SSL implícito (smtplib.SMTP_SSL) — la conexión ya nace
      cifrada, no se llama starttls().
    - Cualquier otro puerto (ej. 587) con use_tls=True: STARTTLS — se
      conecta en texto plano y luego se sube a TLS con starttls().
    Mezclar ambos mecanismos con el puerto equivocado es el error más común
    al configurar SMTP (ej. usar starttls() contra el puerto 465 nunca
    conecta, porque ese puerto espera TLS desde el primer byte).
    """
    if not recipients:
        raise ValueError("No hay destinatarios activos configurados (alert_recipients).")

    msg = _build_message(recipients, subject, body, smtp_config["username"], attachment_path)
    plain_password = decrypt_password(smtp_config["encrypted_password"])
    port = int(smtp_config["port"])

    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_config["server"], port, timeout=10, context=context) as server:
            server.login(smtp_config["username"], plain_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_config["server"], port, timeout=10) as server:
            if smtp_config.get("use_tls"):
                server.starttls()
            server.login(smtp_config["username"], plain_password)
            server.send_message(msg)


def send_alert_email_in_background(event_id: int, smtp_config: dict, recipients: list,
                                    subject: str, body: str,
                                    attachment_path: Optional[str] = None,
                                    on_error: Optional[Callable[[Exception], None]] = None,
                                    db_module=db) -> threading.Thread:
    """
    Lanza el envío en un hilo aparte para no bloquear camera_loop() durante
    el handshake SMTP. Sin reintento automático: si falla, solo se invoca
    `on_error` (ej. para loguear) y el evento queda sin marcar como enviado.

    Al tener éxito, abre su PROPIA conexión SQLite para marcar el evento
    como notificado — una conexión sqlite3 no puede compartirse entre
    hilos, así que no se reutiliza ninguna conexión abierta por el hilo
    principal.

    `db_module` es inyectable (por defecto app.db) para poder probar esta
    función sin tocar la base de datos real.
    """
    def _worker():
        try:
            send_email(smtp_config, recipients, subject, body, attachment_path)
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return

        conn = db_module.get_connection()
        try:
            db_module.mark_event_email_sent(conn, event_id)
        finally:
            conn.close()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


def send_test_email(smtp_config: dict, test_recipient: str) -> None:
    """
    RF-20: botón 'Probar conexión' de la pantalla de Configuración.
    Envío SÍNCRONO deliberado (el usuario espera el resultado en pantalla).
    """
    send_email(
        smtp_config,
        recipients=[test_recipient],
        subject="Prueba de configuración SMTP — Monitoreo de Góndolas",
        body="Si recibes este correo, la configuración SMTP es correcta.",
    )
