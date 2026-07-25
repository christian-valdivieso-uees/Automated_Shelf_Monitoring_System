"""
Pruebas de integración de src/backend.py (rutas Flask).

Usa el test_client de Flask contra una base de datos SQLite temporal
(no ':memory:', porque cada request de Flask abre su propia conexión
con db.get_connection() por diseño — necesitamos un archivo real
compartido entre requests, no una conexión única en memoria).

No requiere cámara, picamera2 ni ultralytics: solo se prueban las rutas
HTTP (login, ROI, eventos), no camera_loop().
"""

import sys
import os
import importlib.util

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from werkzeug.security import generate_password_hash

from app import db as db_module


def _load_backend_module():
    """Carga src/backend.py como módulo, sin depender de src/ como paquete."""
    backend_path = os.path.join(os.path.dirname(__file__), "..", "src", "backend.py")
    spec = importlib.util.spec_from_file_location("backend", backend_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backend(tmp_path, monkeypatch):
    """
    Carga backend.py apuntando su base de datos a un archivo temporal,
    con el esquema aplicado y un usuario de prueba ya creado.
    """
    db_path = str(tmp_path / "test_shelf_monitoring.db")
    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", db_path)

    module = _load_backend_module()
    # El módulo backend importó `db` como referencia al mismo objeto módulo,
    # así que el monkeypatch de arriba también aplica dentro de backend.py.

    conn = db_module.get_connection(db_path)
    db_module.init_schema(conn)
    conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        ("admin", generate_password_hash("admin123")),
    )
    conn.commit()
    conn.close()

    module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return module


@pytest.fixture
def client(backend):
    return backend.app.test_client()


def login(client, username="admin", password="admin123"):
    return client.post("/login", json={"username": username, "password": password})


class TestLogin:
    def test_login_con_credenciales_correctas(self, client):
        resp = login(client)
        assert resp.status_code == 200
        assert resp.get_json()["username"] == "admin"

    def test_login_con_password_incorrecto(self, client):
        resp = login(client, password="incorrecta")
        assert resp.status_code == 401
        assert "error" in resp.get_json()

    def test_login_con_usuario_inexistente(self, client):
        resp = login(client, username="no_existe")
        assert resp.status_code == 401

    def test_mensaje_de_error_es_generico_sin_revelar_cual_campo_fallo(self, client):
        """RF-01: no debe distinguir 'usuario no existe' de 'password incorrecto'."""
        resp_usuario_malo = login(client, username="no_existe")
        resp_password_malo = login(client, password="incorrecta")
        assert resp_usuario_malo.get_json()["error"] == resp_password_malo.get_json()["error"]

    def test_logout_requiere_estar_autenticado(self, client):
        resp = client.post("/logout")
        assert resp.status_code in (401, 302)  # Flask-Login redirige o rechaza


class TestRutasProtegidas:
    def test_video_feed_sin_login_es_rechazado(self, client):
        resp = client.get("/video_feed")
        assert resp.status_code in (401, 302)

    def test_roi_zones_sin_login_es_rechazado(self, client):
        resp = client.get("/api/roi_zones")
        assert resp.status_code in (401, 302)

    def test_events_sin_login_es_rechazado(self, client):
        resp = client.get("/api/events")
        assert resp.status_code in (401, 302)


class TestPaginasHtml:
    def test_login_get_sirve_la_pagina_sin_autenticacion(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Iniciar sesi" in resp.data or b"login" in resp.data.lower()

    def test_paginas_protegidas_redirigen_a_login_sin_sesion(self, client):
        for path in ["/", "/roi-config", "/history"]:
            resp = client.get(path)
            assert resp.status_code in (302, 401)

    def test_live_view_renderiza_tras_login(self, client):
        login(client)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Vista en Vivo" in resp.data

    def test_roi_config_page_renderiza_tras_login(self, client):
        login(client)
        resp = client.get("/roi-config")
        assert resp.status_code == 200

    def test_history_page_renderiza_tras_login(self, client):
        login(client)
        resp = client.get("/history")
        assert resp.status_code == 200

    def test_login_get_ya_autenticado_redirige_a_inicio(self, client):
        login(client)
        resp = client.get("/login")
        assert resp.status_code == 302


class TestSettingsPage:
    def test_settings_page_renderiza_tras_login(self, client):
        login(client)
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert b"Configuraci" in resp.data

    def test_settings_page_sin_login_redirige(self, client):
        resp = client.get("/settings")
        assert resp.status_code in (302, 401)


class TestGeneralParametersRoutes:
    def test_listar_parametros_generales(self, client):
        login(client)
        resp = client.get("/api/general_parameters")
        assert resp.status_code == 200
        claves = [p["key"] for p in resp.get_json()]
        assert "image_retention_days" in claves

    def test_actualizar_parametro(self, client):
        login(client)
        resp = client.put("/api/general_parameters/image_retention_days",
                           json={"value": "45"})
        assert resp.status_code == 200

        params = client.get("/api/general_parameters").get_json()
        actualizado = next(p for p in params if p["key"] == "image_retention_days")
        assert actualizado["value"] == "45"

    def test_actualizar_parametro_sin_value_devuelve_400(self, client):
        login(client)
        resp = client.put("/api/general_parameters/image_retention_days", json={})
        assert resp.status_code == 400

    def test_general_parameters_sin_login_es_rechazado(self, client):
        resp = client.get("/api/general_parameters")
        assert resp.status_code in (302, 401)


class TestSmtpConfigRoutes:
    def test_get_smtp_config_sin_configuracion_devuelve_null(self, client):
        login(client)
        resp = client.get("/api/smtp_config")
        assert resp.status_code == 200
        assert resp.get_json() is None

    def test_guardar_smtp_config_requiere_env_var(self, client, monkeypatch):
        monkeypatch.delenv("SMTP_ENCRYPTION_KEY", raising=False)
        login(client)
        resp = client.post("/api/smtp_config", json={
            "server": "smtp.ejemplo.com", "port": 587,
            "username": "alertas@ejemplo.com", "password": "secreto123",
        })
        assert resp.status_code == 500

    def test_guardar_y_leer_smtp_config_nunca_expone_password(self, client, monkeypatch):
        from cryptography.fernet import Fernet
        monkeypatch.setenv("SMTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
        login(client)

        resp = client.post("/api/smtp_config", json={
            "server": "smtp.ejemplo.com", "port": 587,
            "username": "alertas@ejemplo.com", "password": "secreto123",
        })
        assert resp.status_code == 201

        resp_get = client.get("/api/smtp_config")
        config = resp_get.get_json()
        assert config["server"] == "smtp.ejemplo.com"
        assert "encrypted_password" not in config
        assert "password" not in config

    def test_guardar_smtp_config_campos_faltantes_devuelve_400(self, client):
        login(client)
        resp = client.post("/api/smtp_config", json={"server": "smtp.ejemplo.com"})
        assert resp.status_code == 400

    def test_probar_conexion_sin_configuracion_devuelve_400(self, client):
        login(client)
        resp = client.post("/api/smtp_config/test", json={"test_recipient": "x@x.com"})
        assert resp.status_code == 400

    def test_probar_conexion_sin_destinatario_devuelve_400(self, client):
        login(client)
        resp = client.post("/api/smtp_config/test", json={})
        assert resp.status_code == 400

    def test_probar_conexion_con_configuracion_real_intenta_enviar(self, client, monkeypatch):
        from cryptography.fernet import Fernet
        from unittest.mock import patch, MagicMock
        monkeypatch.setenv("SMTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
        login(client)
        client.post("/api/smtp_config", json={
            "server": "smtp.ejemplo.com", "port": 587,
            "username": "alertas@ejemplo.com", "password": "secreto123",
        })

        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server
            resp = client.post("/api/smtp_config/test", json={"test_recipient": "yo@x.com"})

        assert resp.status_code == 200
        mock_server.send_message.assert_called_once()


class TestAlertRecipientsRoutes:
    def test_listar_vacio_al_inicio(self, client):
        login(client)
        resp = client.get("/api/alert_recipients")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_agregar_y_listar_destinatario(self, client):
        login(client)
        resp = client.post("/api/alert_recipients", json={"email": "gerente@tienda.com"})
        assert resp.status_code == 201
        assert "gerente@tienda.com" in resp.get_json()

    def test_agregar_sin_email_devuelve_400(self, client):
        login(client)
        resp = client.post("/api/alert_recipients", json={})
        assert resp.status_code == 400

    def test_eliminar_destinatario(self, client):
        login(client)
        client.post("/api/alert_recipients", json={"email": "temporal@tienda.com"})
        resp = client.delete("/api/alert_recipients/temporal@tienda.com")
        assert resp.status_code == 200
        assert "temporal@tienda.com" not in resp.get_json()

    def test_alert_recipients_sin_login_es_rechazado(self, client):
        resp = client.get("/api/alert_recipients")
        assert resp.status_code in (302, 401)


class TestUsersRoutes:
    def test_listar_usuarios_nunca_expone_password_hash(self, client):
        login(client)
        resp = client.get("/api/users")
        assert resp.status_code == 200
        users = resp.get_json()
        assert len(users) == 1  # solo el admin del fixture
        assert "password_hash" not in users[0]

    def test_crear_usuario_nuevo(self, client):
        login(client)
        resp = client.post("/api/users", json={"username": "operador", "password": "clave12345"})
        assert resp.status_code == 201

        users = client.get("/api/users").get_json()
        assert any(u["username"] == "operador" for u in users)

    def test_crear_usuario_con_username_duplicado_devuelve_400(self, client):
        login(client)
        client.post("/api/users", json={"username": "operador", "password": "clave12345"})
        resp = client.post("/api/users", json={"username": "operador", "password": "otraclave123"})
        assert resp.status_code == 400

    def test_crear_usuario_con_password_corta_devuelve_400(self, client):
        login(client)
        resp = client.post("/api/users", json={"username": "operador2", "password": "123"})
        assert resp.status_code == 400

    def test_crear_usuario_sin_datos_devuelve_400(self, client):
        login(client)
        resp = client.post("/api/users", json={})
        assert resp.status_code == 400

    def test_cambiar_mi_password_con_password_actual_correcta(self, client):
        login(client)
        resp = client.put("/api/users/me/password", json={
            "current_password": "admin123", "new_password": "nuevaClave123",
        })
        assert resp.status_code == 200

        # Login viejo ya no funciona, el nuevo sí
        resp_login_viejo = login(client, password="admin123")
        assert resp_login_viejo.status_code == 401

    def test_cambiar_mi_password_con_password_actual_incorrecta(self, client):
        login(client)
        resp = client.put("/api/users/me/password", json={
            "current_password": "incorrecta", "new_password": "nuevaClave123",
        })
        assert resp.status_code == 401

    def test_cambiar_mi_password_nueva_muy_corta_devuelve_400(self, client):
        login(client)
        resp = client.put("/api/users/me/password", json={
            "current_password": "admin123", "new_password": "123",
        })
        assert resp.status_code == 400

    def test_resetear_password_de_otro_usuario_sin_pedir_actual(self, client):
        login(client)
        creado = client.post("/api/users", json={"username": "operador", "password": "clave12345"}).get_json()

        resp = client.put(f"/api/users/{creado['id']}/password", json={"new_password": "otraClave456"})
        assert resp.status_code == 200

    def test_resetear_password_usuario_inexistente_devuelve_404(self, client):
        login(client)
        resp = client.put("/api/users/9999/password", json={"new_password": "otraClave456"})
        assert resp.status_code == 404

    def test_eliminar_usuario(self, client):
        login(client)
        creado = client.post("/api/users", json={"username": "temporal", "password": "clave12345"}).get_json()

        resp = client.delete(f"/api/users/{creado['id']}")
        assert resp.status_code == 200
        users = resp.get_json()
        assert not any(u["id"] == creado["id"] for u in users)

    def test_no_se_puede_eliminar_al_unico_usuario(self, client, backend):
        login(client)
        conn = backend.db.get_connection()
        admin = backend.db.get_user_by_username(conn, "admin")
        conn.close()

        resp = client.delete(f"/api/users/{admin['id']}")
        assert resp.status_code == 400

    def test_no_se_puede_eliminar_la_propia_cuenta_conectada(self, client, backend):
        login(client)
        creado = client.post("/api/users", json={"username": "operador", "password": "clave12345"}).get_json()
        conn = backend.db.get_connection()
        admin = backend.db.get_user_by_username(conn, "admin")
        conn.close()

        # Con 2 usuarios ya existentes, "no eliminar el único" ya no aplica,
        # pero sí debe bloquear eliminar la cuenta con la que se inició sesión.
        resp = client.delete(f"/api/users/{admin['id']}")
        assert resp.status_code == 400

    def test_users_routes_sin_login_son_rechazadas(self, client):
        assert client.get("/api/users").status_code in (302, 401)
        assert client.post("/api/users", json={}).status_code in (302, 401)


class TestRoiZonesRoutes:
    def test_crear_y_listar_zona(self, client):
        login(client)
        resp = client.post("/api/roi_zones", json={
            "name": "Góndola A", "x1": 0.1, "y1": 0.1, "x2": 0.9, "y2": 0.9,
        })
        assert resp.status_code == 201
        new_id = resp.get_json()["id"]

        resp_list = client.get("/api/roi_zones")
        assert resp_list.status_code == 200
        nombres = [z["name"] for z in resp_list.get_json()]
        assert "Góndola A" in nombres

    def test_crear_zona_sin_campos_requeridos_devuelve_400(self, client):
        login(client)
        resp = client.post("/api/roi_zones", json={"name": "Incompleta"})
        assert resp.status_code == 400

    def test_crear_zona_con_umbral_invalido_devuelve_400(self, client):
        login(client)
        resp = client.post("/api/roi_zones", json={
            "name": "Mala", "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0,
            "low_stock_threshold": 5, "restocked_threshold": 3,
        })
        assert resp.status_code == 400

    def test_eliminar_zona(self, client):
        login(client)
        creada = client.post("/api/roi_zones", json={
            "name": "Temporal", "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0,
        }).get_json()

        resp = client.delete(f"/api/roi_zones/{creada['id']}")
        assert resp.status_code == 200

        zonas = client.get("/api/roi_zones").get_json()
        assert creada["id"] not in [z["id"] for z in zonas]


class TestEventsRoutes:
    def test_listar_eventos_vacio_al_inicio(self, client):
        login(client)
        resp = client.get("/api/events")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_filtro_por_roi_id_en_la_ruta(self, client, backend):
        login(client)
        zona = client.post("/api/roi_zones", json={
            "name": "Zona X", "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0,
        }).get_json()

        conn = backend.db.get_connection()
        backend.db.insert_stock_event(conn, zona["id"], "Zona X", "in_stock", "out_of_stock")
        conn.close()

        resp = client.get(f"/api/events?roi_id={zona['id']}")
        assert resp.status_code == 200
        eventos = resp.get_json()
        assert len(eventos) == 1
        assert eventos[0]["roi_id"] == zona["id"]

    def test_imagen_de_evento_inexistente_devuelve_404(self, client):
        login(client)
        resp = client.get("/api/events/9999/image")
        assert resp.status_code == 404
