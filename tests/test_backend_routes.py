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
