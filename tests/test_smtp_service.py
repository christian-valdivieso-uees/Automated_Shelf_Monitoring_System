"""
Pruebas unitarias de app.smtp_service.

El envío real de correo (smtplib.SMTP) se simula con unittest.mock —
estas pruebas nunca abren una conexión de red real. La clave de cifrado
se inyecta vía variable de entorno con monkeypatch, generada de forma
efímera para cada prueba (nunca se usa una clave real de producción).
"""

import sys
import os
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from cryptography.fernet import Fernet

from app import smtp_service


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    """Clave Fernet efímera para cada prueba, vía variable de entorno."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SMTP_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
def sample_config():
    encrypted = smtp_service.encrypt_password("password_real_de_prueba")
    return {
        "server": "smtp.ejemplo.com",
        "port": 587,
        "username": "alertas@ejemplo.com",
        "encrypted_password": encrypted,
        "use_tls": True,
    }


@pytest.fixture
def sample_event():
    return {
        "roi_name_snapshot": "Góndola Test",
        "previous_state": "in_stock",
        "new_state": "out_of_stock",
        "avg_total_objects": 1.3,
        "timestamp": "2026-07-24 12:00:00",
    }


class FakeDbModule:
    """Doble de app.db para probar send_alert_email_in_background sin BD real."""
    def __init__(self):
        self.marked_sent_event_ids = []
        self.connections_opened = 0

    def get_connection(self):
        self.connections_opened += 1
        return MagicMock()

    def mark_event_email_sent(self, conn, event_id):
        self.marked_sent_event_ids.append(event_id)


class TestCifrado:
    def test_encrypt_y_decrypt_recuperan_el_valor_original(self):
        cifrado = smtp_service.encrypt_password("mi_password_secreto")
        assert cifrado != "mi_password_secreto"
        assert smtp_service.decrypt_password(cifrado) == "mi_password_secreto"

    def test_falla_sin_clave_de_cifrado_en_el_entorno(self, monkeypatch):
        monkeypatch.delenv("SMTP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(smtp_service.SmtpEncryptionKeyMissing):
            smtp_service.encrypt_password("algo")

    def test_falla_al_descifrar_con_clave_distinta_a_la_usada_para_cifrar(self, monkeypatch):
        cifrado = smtp_service.encrypt_password("secreto")

        otra_clave = Fernet.generate_key().decode()
        monkeypatch.setenv("SMTP_ENCRYPTION_KEY", otra_clave)

        with pytest.raises(smtp_service.SmtpDecryptionError):
            smtp_service.decrypt_password(cifrado)


class TestBuildAlertMessage:
    def test_asunto_y_cuerpo_incluyen_los_datos_del_evento(self, sample_event):
        subject, body = smtp_service.build_alert_message(sample_event)
        assert "Góndola Test" in subject
        assert "sin stock" in subject
        assert "con stock -> sin stock" in body
        assert "1.3" in body

    def test_traduce_estados_a_texto_legible(self, sample_event):
        sample_event["previous_state"] = "out_of_stock"
        sample_event["new_state"] = "in_stock"
        subject, body = smtp_service.build_alert_message(sample_event)
        assert "con stock" in subject
        assert "sin stock -> con stock" in body


class TestSendEmail:
    def test_envia_correo_exitosamente(self, sample_config):
        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            smtp_service.send_email(sample_config, ["destino@x.com"], "Asunto", "Cuerpo")

            mock_smtp_class.assert_called_once_with(sample_config["server"], sample_config["port"], timeout=10)
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with(sample_config["username"], "password_real_de_prueba")
            mock_server.send_message.assert_called_once()

    def test_no_llama_starttls_si_use_tls_es_falso(self, sample_config):
        sample_config["use_tls"] = False
        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            smtp_service.send_email(sample_config, ["destino@x.com"], "Asunto", "Cuerpo")
            mock_server.starttls.assert_not_called()

    def test_sin_destinatarios_lanza_value_error_sin_llamar_a_smtp(self, sample_config):
        with patch("smtplib.SMTP") as mock_smtp_class:
            with pytest.raises(ValueError):
                smtp_service.send_email(sample_config, [], "Asunto", "Cuerpo")
            mock_smtp_class.assert_not_called()

    def test_adjunta_imagen_si_el_archivo_existe(self, sample_config, tmp_path):
        imagen = tmp_path / "evento.jpg"
        imagen.write_bytes(b"contenido_falso_de_imagen")

        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            smtp_service.send_email(sample_config, ["destino@x.com"], "Asunto", "Cuerpo",
                                     attachment_path=str(imagen))
            # No lanza excepción y sí llega a enviar
            mock_server.send_message.assert_called_once()

    def test_error_de_login_se_propaga_al_llamador(self, sample_config):
        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_server.login.side_effect = Exception("Credenciales rechazadas por el servidor")
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            with pytest.raises(Exception, match="Credenciales rechazadas"):
                smtp_service.send_email(sample_config, ["destino@x.com"], "Asunto", "Cuerpo")

    def test_puerto_465_usa_ssl_implicito_no_starttls(self, sample_config):
        sample_config["port"] = 465
        with patch("smtplib.SMTP_SSL") as mock_smtp_ssl_class, patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_ssl_class.return_value.__enter__.return_value = mock_server

            smtp_service.send_email(sample_config, ["destino@x.com"], "Asunto", "Cuerpo")

            mock_smtp_ssl_class.assert_called_once()
            args, kwargs = mock_smtp_ssl_class.call_args
            assert args[0] == sample_config["server"]
            assert args[1] == 465
            mock_server.login.assert_called_once_with(sample_config["username"], "password_real_de_prueba")
            mock_server.send_message.assert_called_once()
            # Jamás se usa smtplib.SMTP (STARTTLS) para el puerto 465
            mock_smtp_class.assert_not_called()
            mock_server.starttls.assert_not_called()

    def test_puerto_587_sigue_usando_starttls_no_ssl_implicito(self, sample_config):
        sample_config["port"] = 587
        with patch("smtplib.SMTP") as mock_smtp_class, patch("smtplib.SMTP_SSL") as mock_smtp_ssl_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            smtp_service.send_email(sample_config, ["destino@x.com"], "Asunto", "Cuerpo")

            mock_server.starttls.assert_called_once()
            mock_smtp_ssl_class.assert_not_called()


class TestSendAlertEmailInBackground:
    def test_envio_exitoso_marca_el_evento_como_notificado(self, sample_config):
        fake_db = FakeDbModule()

        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            thread = smtp_service.send_alert_email_in_background(
                event_id=42, smtp_config=sample_config, recipients=["destino@x.com"],
                subject="Asunto", body="Cuerpo", db_module=fake_db,
            )
            thread.join(timeout=2)

        assert fake_db.marked_sent_event_ids == [42]

    def test_envio_fallido_no_marca_el_evento_y_llama_on_error(self, sample_config):
        fake_db = FakeDbModule()
        errores_capturados = []

        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_server.login.side_effect = Exception("Fallo simulado de conexión")
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            thread = smtp_service.send_alert_email_in_background(
                event_id=99, smtp_config=sample_config, recipients=["destino@x.com"],
                subject="Asunto", body="Cuerpo",
                on_error=lambda exc: errores_capturados.append(exc),
                db_module=fake_db,
            )
            thread.join(timeout=2)

        assert fake_db.marked_sent_event_ids == []
        assert len(errores_capturados) == 1
        assert "Fallo simulado" in str(errores_capturados[0])

    def test_no_bloquea_el_hilo_principal(self, sample_config):
        """El envío debe correr en background: la llamada retorna casi de inmediato."""
        fake_db = FakeDbModule()

        def _smtp_lento(*args, **kwargs):
            time.sleep(0.3)
            mock_server = MagicMock()
            context = MagicMock()
            context.__enter__.return_value = mock_server
            return context

        with patch("smtplib.SMTP", side_effect=_smtp_lento):
            inicio = time.time()
            thread = smtp_service.send_alert_email_in_background(
                event_id=1, smtp_config=sample_config, recipients=["x@x.com"],
                subject="A", body="B", db_module=fake_db,
            )
            duracion_llamada = time.time() - inicio

            assert duracion_llamada < 0.1  # no esperó los 0.3s del "envío"
            thread.join(timeout=2)


class TestSendTestEmail:
    def test_send_test_email_usa_un_solo_destinatario(self, sample_config):
        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            smtp_service.send_test_email(sample_config, "prueba@destino.com")

            sent_msg = mock_server.send_message.call_args[0][0]
            assert sent_msg["To"] == "prueba@destino.com"
