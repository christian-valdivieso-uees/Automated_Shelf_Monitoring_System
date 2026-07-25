"""
configure_smtp.py — Guarda la configuración SMTP real en la base de datos,
cifrando la contraseña antes de persistirla (nunca en texto plano).

Requiere que SMTP_ENCRYPTION_KEY ya esté exportada en el entorno (la misma
clave generada por init_db.py en .smtp_key):

    export SMTP_ENCRYPTION_KEY="$(cat .smtp_key)"
    python3 configure_smtp.py

El script pide los datos de forma interactiva — la contraseña no se
imprime en pantalla ni queda en el historial de la shell.
"""

import os
import sys
import getpass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import db, smtp_service


def main():
    if not os.environ.get("SMTP_ENCRYPTION_KEY"):
        print("ERROR: la variable de entorno SMTP_ENCRYPTION_KEY no está definida.")
        print('Ejecuta primero: export SMTP_ENCRYPTION_KEY="$(cat .smtp_key)"')
        sys.exit(1)

    print("=== Configuración de cuenta SMTP ===\n")
    server = input("Servidor SMTP [onlycontrol-com.correoseguro.dinaserver.com]: ").strip() \
        or "onlycontrol-com.correoseguro.dinaserver.com"
    port = input("Puerto (465 = SSL implícito, 587 = STARTTLS) [587]: ").strip()
    port = int(port) if port else 587
    username = input("Usuario [sams_uees@onlycontrol.com]: ").strip() \
        or "sams_uees@onlycontrol.com"
    password = getpass.getpass("Contraseña (no se muestra en pantalla): ")

    use_tls = port != 465  # 465 usa SSL implícito, no STARTTLS (ver smtp_service.py)

    encrypted = smtp_service.encrypt_password(password)

    conn = db.get_connection()
    try:
        db.save_smtp_config(
            conn, server=server, port=port, username=username,
            encrypted_password=encrypted, use_tls=use_tls,
        )
    finally:
        conn.close()

    print(f"\nConfiguración guardada: {username} @ {server}:{port} "
          f"({'STARTTLS' if use_tls else 'SSL implícito'})")

    respuesta = input("\n¿Enviar un correo de prueba ahora? (s/n): ").strip().lower()
    if respuesta == "s":
        destino = input("Correo de destino para la prueba: ").strip()
        conn = db.get_connection()
        try:
            smtp_config = db.get_active_smtp_config(conn)
        finally:
            conn.close()
        try:
            smtp_service.send_test_email(smtp_config, destino)
            print(f"Correo de prueba enviado a {destino}. Revisa la bandeja de entrada.")
        except Exception as exc:
            print(f"No se pudo enviar el correo de prueba: {exc}")


if __name__ == "__main__":
    main()
