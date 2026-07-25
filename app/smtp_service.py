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
_BADGE_STYLE = {
    "in_stock": "background:#E1F5EE;color:#085041;",
    "out_of_stock": "background:#FCEBEB;color:#791F1F;",
}


def _badge_html(state: str) -> str:
    style = _BADGE_STYLE.get(state, "background:#F1EFE8;color:#444441;")
    label = _ESTADO_LEGIBLE.get(state, state).upper()
    return (
        f'<td style="{style}font-size:12px;font-weight:bold;'
        f'padding:6px 14px;border-radius:100px;">{label}</td>'
    )


def build_alert_message(event: dict) -> tuple:
    """
    Construye asunto, cuerpo en texto plano y cuerpo en HTML a partir de
    una fila de stock_events. Devuelve (subject, text_body, html_body).

    El texto plano se mantiene como respaldo (multipart/alternative): los
    clientes de correo que no rendericen HTML, o que el usuario configure
    para mostrar solo texto plano, siguen recibiendo la información completa.
    """
    previous = _ESTADO_LEGIBLE.get(event["previous_state"], event["previous_state"])
    new = _ESTADO_LEGIBLE.get(event["new_state"], event["new_state"])
    avg = event.get("avg_total_objects")
    timestamp = event.get("timestamp")
    zona = event["roi_name_snapshot"]

    subject = f"Alerta de stock: {zona} ahora está {new}"

    text_body = (
        f"Zona: {zona}\n"
        f"Cambio de estado: {previous} -> {new}\n"
        f"Promedio de conteo que confirmó el cambio: {avg}\n"
        f"Momento del evento: {timestamp}\n"
    )

    html_body = f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-family:Arial,Helvetica,sans-serif;">
<tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #e2e2df;border-radius:8px;overflow:hidden;">
<tr><td style="background:#7A1532;padding:16px 24px;">
<span style="color:#ffffff;font-size:15px;font-weight:bold;">SAMS &middot; Alerta de Stock</span>
</td></tr>
<tr><td style="padding:24px 24px 8px 24px;">
<div style="font-size:11px;color:#999999;text-transform:uppercase;letter-spacing:0.04em;">Zona</div>
<div style="font-size:20px;font-weight:bold;color:#222222;margin-top:2px;">{zona}</div>
</td></tr>
<tr><td style="padding:14px 24px 20px 24px;">
<table cellpadding="0" cellspacing="0"><tr>
{_badge_html(event["previous_state"])}
<td style="color:#aaaaaa;font-size:14px;padding:0 10px;">&rarr;</td>
{_badge_html(event["new_state"])}
</tr></table>
</td></tr>
<tr><td style="padding:0 24px 20px 24px;">
<table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;color:#444444;">
<tr><td style="padding:8px 0;color:#888888;border-top:1px solid #eeeeee;">Promedio de conteo</td>
<td style="padding:8px 0;text-align:right;font-family:monospace;border-top:1px solid #eeeeee;">{avg}</td></tr>
<tr><td style="padding:8px 0;color:#888888;border-top:1px solid #eeeeee;">Fecha y hora</td>
<td style="padding:8px 0;text-align:right;font-family:monospace;border-top:1px solid #eeeeee;">{timestamp}</td></tr>
</table>
</td></tr>
<tr><td style="background:#f7f7f5;padding:12px 24px;font-size:11px;color:#999999;">
Sistema de Monitoreo Automatizado de G&oacute;ndolas &mdash; UEES &middot; imagen de respaldo adjunta
</td></tr>
</table>
</td></tr>
</table>
"""

    return subject, text_body, html_body


# ----------------------------------------------------------------------------
# Envío
# ----------------------------------------------------------------------------

def _build_message(recipients: list, subject: str, body: str, from_addr: str,
                    attachment_path: Optional[str] = None,
                    html_body: Optional[str] = None) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    if html_body:
        # multipart/alternative: el cliente de correo elige la versión que
        # sabe renderizar. El texto plano SIEMPRE va primero — es el
        # respaldo que se muestra si el cliente no soporta HTML.
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "plain"))
        alt.attach(MIMEText(html_body, "html"))
        msg.attach(alt)
    else:
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
               attachment_path: Optional[str] = None,
               html_body: Optional[str] = None) -> None:
    """
    Envía un correo de forma SÍNCRONA. Lanza la excepción original de
    smtplib/OSError si falla — el llamador decide cómo manejarla (ver
    send_alert_email_in_background para la variante no bloqueante).

    Si se pasa `html_body`, el correo se arma como multipart/alternative
    (texto plano + HTML) — el cliente de correo elige cuál mostrar. Si se
    omite, el correo es solo texto plano (ej. send_test_email).

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

    msg = _build_message(recipients, subject, body, smtp_config["username"],
                         attachment_path, html_body)
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
                                    html_body: Optional[str] = None,
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
            send_email(smtp_config, recipients, subject, body, attachment_path, html_body)
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
