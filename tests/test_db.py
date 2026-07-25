"""
Pruebas unitarias de app.db.

Usa una base de datos SQLite en memoria (":memory:") con el schema.sql
real del proyecto aplicado en cada prueba — así se detecta cualquier
desalineación entre el esquema y el código de acceso a datos, sin tocar
el archivo real en disco ni depender de rutas del Raspberry Pi.
"""

import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app import db


SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")


@pytest.fixture
def conn():
    """Conexión en memoria con el esquema real ya aplicado."""
    connection = db.get_connection(":memory:")
    db.init_schema(connection, schema_path=SCHEMA_PATH)
    yield connection
    connection.close()


class TestGetConnectionConArchivoReal:
    """La mayoría de pruebas usa ':memory:'; estas cubren el path real en disco."""

    def test_crea_carpeta_y_archivo_si_no_existen(self, tmp_path):
        db_path = tmp_path / "subcarpeta" / "shelf_monitoring.db"
        assert not db_path.parent.exists()

        connection = db.get_connection(str(db_path))
        db.init_schema(connection, schema_path=SCHEMA_PATH)

        assert db_path.exists()
        # La conexión debe quedar utilizable de inmediato
        db.create_roi_zone(connection, "Zona Real", 0.0, 0.0, 1.0, 1.0)
        assert len(db.get_active_roi_zones(connection)) == 1
        connection.close()


@pytest.fixture
def roi_id(conn):
    """Zona ROI de prueba ya creada, lista para usar en otras pruebas."""
    return db.create_roi_zone(
        conn, name="Góndola Test", x1=0.1, y1=0.1, x2=0.9, y2=0.9,
        low_stock_threshold=2, restocked_threshold=4, confirmation_readings=3,
    )


# ----------------------------------------------------------------------------
# roi_zones
# ----------------------------------------------------------------------------

class TestSmtpConfigYRecipients:
    def test_get_active_smtp_config_sin_configuracion_devuelve_none(self, conn):
        assert db.get_active_smtp_config(conn) is None

    def test_save_smtp_config_y_recuperar(self, conn):
        db.save_smtp_config(
            conn, server="smtp.gmail.com", port=587, username="alertas@ejemplo.com",
            encrypted_password="cifrado_falso_de_prueba", use_tls=True,
        )
        config = db.get_active_smtp_config(conn)
        assert config is not None
        assert config["server"] == "smtp.gmail.com"
        assert config["port"] == 587
        assert config["active"] == 1

    def test_save_smtp_config_desactiva_la_configuracion_anterior(self, conn):
        db.save_smtp_config(conn, "smtp.viejo.com", 587, "old@x.com", "cifrado1")
        db.save_smtp_config(conn, "smtp.nuevo.com", 465, "new@x.com", "cifrado2")

        config = db.get_active_smtp_config(conn)
        assert config["server"] == "smtp.nuevo.com"

        activos = conn.execute("SELECT COUNT(*) as c FROM smtp_config WHERE active = 1").fetchone()
        assert activos["c"] == 1

    def test_add_y_listar_destinatarios_activos(self, conn):
        db.add_alert_recipient(conn, "gerente@tienda.com")
        db.add_alert_recipient(conn, "supervisor@tienda.com")

        correos = db.get_active_alert_recipient_emails(conn)
        assert set(correos) == {"gerente@tienda.com", "supervisor@tienda.com"}

    def test_add_alert_recipient_duplicado_no_falla_ni_duplica(self, conn):
        db.add_alert_recipient(conn, "repetido@tienda.com")
        db.add_alert_recipient(conn, "repetido@tienda.com")
        correos = db.get_active_alert_recipient_emails(conn)
        assert correos.count("repetido@tienda.com") == 1

    def test_remove_alert_recipient(self, conn):
        db.add_alert_recipient(conn, "quitar@tienda.com")
        db.remove_alert_recipient(conn, "quitar@tienda.com")
        assert db.get_active_alert_recipient_emails(conn) == []


class TestUsers:
    def test_create_user_devuelve_id_valido(self, conn):
        new_id = db.create_user(conn, "nuevo_admin", "hash_de_prueba")
        assert isinstance(new_id, int)
        assert new_id > 0

    def test_create_user_username_duplicado_lanza_integrity_error(self, conn):
        db.create_user(conn, "duplicado", "hash1")
        with pytest.raises(sqlite3.IntegrityError):
            db.create_user(conn, "duplicado", "hash2")

    def test_get_all_users_nunca_incluye_password_hash(self, conn):
        db.create_user(conn, "usuario1", "hash_secreto")
        usuarios = db.get_all_users(conn)
        assert len(usuarios) == 1
        assert "password_hash" not in usuarios[0]
        assert usuarios[0]["username"] == "usuario1"

    def test_count_users(self, conn):
        assert db.count_users(conn) == 0
        db.create_user(conn, "u1", "h1")
        db.create_user(conn, "u2", "h2")
        assert db.count_users(conn) == 2

    def test_update_user_password(self, conn):
        user_id = db.create_user(conn, "usuario1", "hash_viejo")
        db.update_user_password(conn, user_id, "hash_nuevo")
        user = db.get_user_by_id(conn, user_id)
        assert user["password_hash"] == "hash_nuevo"

    def test_delete_user_elimina_la_fila(self, conn):
        user_id = db.create_user(conn, "usuario1", "hash1")
        db.delete_user(conn, user_id)
        assert db.get_user_by_id(conn, user_id) is None

    def test_get_user_by_username_encuentra_al_admin_creado_en_el_seed(self, conn):
        # El schema.sql no crea usuarios (eso lo hace init_db.py con hash),
        # así que insertamos uno manualmente para la prueba.
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("admin", "hash_de_prueba"),
        )
        conn.commit()

        user = db.get_user_by_username(conn, "admin")
        assert user is not None
        assert user["username"] == "admin"
        assert user["password_hash"] == "hash_de_prueba"

    def test_get_user_by_username_inexistente_devuelve_none(self, conn):
        assert db.get_user_by_username(conn, "no_existe") is None

    def test_get_user_by_id_recupera_el_usuario_correcto(self, conn):
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("operador", "otro_hash"),
        )
        conn.commit()
        user_id = cursor.lastrowid

        user = db.get_user_by_id(conn, user_id)
        assert user is not None
        assert user["username"] == "operador"

    def test_get_user_by_id_inexistente_devuelve_none(self, conn):
        assert db.get_user_by_id(conn, 9999) is None


class TestRoiZones:
    def test_create_roi_zone_devuelve_id_valido(self, conn):
        new_id = db.create_roi_zone(conn, "Zona 1", 0.0, 0.0, 1.0, 1.0)
        assert isinstance(new_id, int)
        assert new_id > 0

    def test_get_roi_zone_recupera_los_datos_guardados(self, conn, roi_id):
        zona = db.get_roi_zone(conn, roi_id)
        assert zona is not None
        assert zona["name"] == "Góndola Test"
        assert zona["low_stock_threshold"] == 2
        assert zona["restocked_threshold"] == 4
        assert zona["current_state"] == "in_stock"  # valor por defecto del schema

    def test_get_roi_zone_inexistente_devuelve_none(self, conn):
        assert db.get_roi_zone(conn, 9999) is None

    def test_get_active_roi_zones_excluye_inactivas(self, conn, roi_id):
        otra_id = db.create_roi_zone(conn, "Zona Inactiva", 0.0, 0.0, 0.5, 0.5)
        conn.execute("UPDATE roi_zones SET active = 0 WHERE id = ?", (otra_id,))
        conn.commit()

        activas = db.get_active_roi_zones(conn)
        ids_activas = [z["id"] for z in activas]

        assert roi_id in ids_activas
        assert otra_id not in ids_activas

    def test_umbral_invalido_lanza_integrity_error(self, conn):
        """El CHECK constraint del esquema debe rechazar restocked <= low_stock."""
        with pytest.raises(Exception):
            db.create_roi_zone(
                conn, "Zona Mala", 0.0, 0.0, 1.0, 1.0,
                low_stock_threshold=5, restocked_threshold=3,
            )

    def test_update_roi_state_persiste_estado_de_histeresis(self, conn, roi_id):
        db.update_roi_state(conn, roi_id, current_state="out_of_stock",
                             candidate_state=None, candidate_consecutive_readings=0)
        zona = db.get_roi_zone(conn, roi_id)
        assert zona["current_state"] == "out_of_stock"
        assert zona["candidate_state"] is None
        assert zona["candidate_consecutive_readings"] == 0

    def test_delete_roi_zone_elimina_la_fila(self, conn, roi_id):
        db.delete_roi_zone(conn, roi_id)
        assert db.get_roi_zone(conn, roi_id) is None

    def test_update_roi_zone_actualiza_solo_los_campos_dados(self, conn, roi_id):
        db.update_roi_zone(conn, roi_id, low_stock_threshold=1)
        zona = db.get_roi_zone(conn, roi_id)
        assert zona["low_stock_threshold"] == 1
        assert zona["restocked_threshold"] == 4  # sin cambios
        assert zona["name"] == "Góndola Test"     # sin cambios

    def test_update_roi_zone_sin_campos_no_falla(self, conn, roi_id):
        db.update_roi_zone(conn, roi_id)
        zona = db.get_roi_zone(conn, roi_id)
        assert zona["name"] == "Góndola Test"


# ----------------------------------------------------------------------------
# roi_readings
# ----------------------------------------------------------------------------

class TestRoiReadings:
    def test_insert_reading_y_recuperar_recientes(self, conn, roi_id):
        db.insert_reading(conn, roi_id, 5)
        db.insert_reading(conn, roi_id, 6)
        db.insert_reading(conn, roi_id, 7)

        recientes = db.get_recent_total_objects(conn, roi_id, window=10)
        # Orden: más reciente primero
        assert recientes == [7, 6, 5]

    def test_get_recent_respeta_el_limite_de_ventana(self, conn, roi_id):
        for valor in [1, 2, 3, 4, 5]:
            db.insert_reading(conn, roi_id, valor)

        recientes = db.get_recent_total_objects(conn, roi_id, window=3)
        assert len(recientes) == 3
        assert recientes == [5, 4, 3]

    def test_cleanup_old_readings_mantiene_solo_las_mas_recientes(self, conn, roi_id):
        for valor in range(10):
            db.insert_reading(conn, roi_id, valor)

        eliminadas = db.cleanup_old_readings(conn, roi_id, keep_n=5)
        assert eliminadas == 5

        restantes = db.get_recent_total_objects(conn, roi_id, window=100)
        assert len(restantes) == 5
        assert restantes == [9, 8, 7, 6, 5]

    def test_cleanup_no_afecta_lecturas_de_otra_zona(self, conn, roi_id):
        otra_id = db.create_roi_zone(conn, "Otra Zona", 0.0, 0.0, 0.5, 0.5)
        db.insert_reading(conn, roi_id, 1)
        db.insert_reading(conn, otra_id, 99)

        db.cleanup_old_readings(conn, roi_id, keep_n=0)

        assert db.get_recent_total_objects(conn, roi_id, window=10) == []
        assert db.get_recent_total_objects(conn, otra_id, window=10) == [99]

    def test_reading_de_zona_eliminada_se_borra_en_cascada(self, conn, roi_id):
        db.insert_reading(conn, roi_id, 5)
        db.delete_roi_zone(conn, roi_id)
        assert db.get_recent_total_objects(conn, roi_id, window=10) == []


# ----------------------------------------------------------------------------
# stock_events
# ----------------------------------------------------------------------------

class TestStockEvents:
    def test_insert_stock_event_y_consultar(self, conn, roi_id):
        db.insert_stock_event(
            conn, roi_id, "Góndola Test", "in_stock", "out_of_stock",
            avg_total_objects=1.3, image_path="eventos/e1.jpg",
        )
        eventos = db.get_stock_events(conn)
        assert len(eventos) == 1
        assert eventos[0]["previous_state"] == "in_stock"
        assert eventos[0]["new_state"] == "out_of_stock"
        assert eventos[0]["email_sent"] == 0

    def test_filtro_por_roi_id(self, conn, roi_id):
        otra_id = db.create_roi_zone(conn, "Otra Zona", 0.0, 0.0, 0.5, 0.5)
        db.insert_stock_event(conn, roi_id, "Góndola Test", "in_stock", "out_of_stock")
        db.insert_stock_event(conn, otra_id, "Otra Zona", "in_stock", "out_of_stock")

        eventos = db.get_stock_events(conn, roi_id=roi_id)
        assert len(eventos) == 1
        assert eventos[0]["roi_id"] == roi_id

    def test_filtro_por_tipo_de_evento(self, conn, roi_id):
        db.insert_stock_event(conn, roi_id, "Góndola Test", "in_stock", "out_of_stock")
        db.insert_stock_event(conn, roi_id, "Góndola Test", "out_of_stock", "in_stock")

        solo_sin_stock = db.get_stock_events(conn, event_type="out_of_stock")
        assert len(solo_sin_stock) == 1
        assert solo_sin_stock[0]["new_state"] == "out_of_stock"

    def test_evento_sobrevive_a_eliminacion_de_la_zona(self, conn, roi_id):
        """roi_id queda NULL (ON DELETE SET NULL) pero el evento no desaparece."""
        db.insert_stock_event(conn, roi_id, "Góndola Test", "in_stock", "out_of_stock")
        db.delete_roi_zone(conn, roi_id)

        eventos = db.get_stock_events(conn)
        assert len(eventos) == 1
        assert eventos[0]["roi_id"] is None
        assert eventos[0]["roi_name_snapshot"] == "Góndola Test"

    def test_mark_event_email_sent(self, conn, roi_id):
        event_id = db.insert_stock_event(conn, roi_id, "Góndola Test", "in_stock", "out_of_stock")
        db.mark_event_email_sent(conn, event_id)

        eventos = db.get_stock_events(conn)
        assert eventos[0]["email_sent"] == 1

    def test_limit_restringe_cantidad_de_resultados(self, conn, roi_id):
        for _ in range(5):
            db.insert_stock_event(conn, roi_id, "Góndola Test", "in_stock", "out_of_stock")

        eventos = db.get_stock_events(conn, limit=2)
        assert len(eventos) == 2

    def test_filtro_date_from_excluye_eventos_anteriores(self, conn, roi_id):
        viejo_id = db.insert_stock_event(conn, roi_id, "Góndola Test", "in_stock", "out_of_stock")
        conn.execute(
            "UPDATE stock_events SET timestamp = '2026-01-01 00:00:00' WHERE id = ?", (viejo_id,)
        )
        conn.commit()
        db.insert_stock_event(conn, roi_id, "Góndola Test", "out_of_stock", "in_stock")

        recientes = db.get_stock_events(conn, date_from="2026-06-01")
        assert len(recientes) == 1
        assert recientes[0]["id"] != viejo_id

    def test_filtro_date_to_excluye_eventos_posteriores(self, conn, roi_id):
        db.insert_stock_event(conn, roi_id, "Góndola Test", "in_stock", "out_of_stock")
        futuro_id = db.insert_stock_event(conn, roi_id, "Góndola Test", "out_of_stock", "in_stock")
        conn.execute(
            "UPDATE stock_events SET timestamp = '2099-01-01 00:00:00' WHERE id = ?", (futuro_id,)
        )
        conn.commit()

        pasados = db.get_stock_events(conn, date_to="2098-12-31")
        assert len(pasados) == 1
        assert pasados[0]["id"] != futuro_id

    def test_filtros_de_fecha_combinados_definen_un_rango(self, conn, roi_id):
        dentro_id = db.insert_stock_event(conn, roi_id, "Góndola Test", "in_stock", "out_of_stock")
        conn.execute(
            "UPDATE stock_events SET timestamp = '2026-03-15 00:00:00' WHERE id = ?", (dentro_id,)
        )
        fuera_id = db.insert_stock_event(conn, roi_id, "Góndola Test", "out_of_stock", "in_stock")
        conn.execute(
            "UPDATE stock_events SET timestamp = '2027-01-01 00:00:00' WHERE id = ?", (fuera_id,)
        )
        conn.commit()

        en_rango = db.get_stock_events(conn, date_from="2026-01-01", date_to="2026-12-31")
        assert len(en_rango) == 1
        assert en_rango[0]["id"] == dentro_id


# ----------------------------------------------------------------------------
# general_parameters
# ----------------------------------------------------------------------------

class TestGeneralParameters:
    def test_lee_parametro_seed_del_schema(self, conn):
        valor = db.get_general_parameter(conn, "image_retention_days", cast=int)
        assert valor == 30

    def test_get_all_general_parameters_devuelve_los_seeds(self, conn):
        todos = db.get_all_general_parameters(conn)
        claves = [p["key"] for p in todos]
        assert "image_retention_days" in claves
        assert "reading_average_window" in claves
        assert "default_confirmation_readings" in claves

    def test_get_all_general_parameters_refleja_actualizaciones(self, conn):
        db.set_general_parameter(conn, "image_retention_days", 60)
        todos = db.get_all_general_parameters(conn)
        param = next(p for p in todos if p["key"] == "image_retention_days")
        assert param["value"] == "60"

    def test_parametro_inexistente_devuelve_default(self, conn):
        valor = db.get_general_parameter(conn, "no_existe", default=42, cast=int)
        assert valor == 42

    def test_set_general_parameter_inserta_nuevo(self, conn):
        db.set_general_parameter(conn, "nueva_clave", 123, "descripción de prueba")
        assert db.get_general_parameter(conn, "nueva_clave", cast=int) == 123

    def test_set_general_parameter_actualiza_existente(self, conn):
        db.set_general_parameter(conn, "image_retention_days", 60)
        assert db.get_general_parameter(conn, "image_retention_days", cast=int) == 60


# ----------------------------------------------------------------------------
# Retención de imágenes (RNF-04)
# ----------------------------------------------------------------------------

class TestRetencionDeImagenes:
    def test_get_events_older_than_filtra_por_antiguedad(self, conn, roi_id):
        event_id = db.insert_stock_event(
            conn, roi_id, "Góndola Test", "in_stock", "out_of_stock",
            image_path="eventos/viejo.jpg",
        )
        # Forzamos timestamp antiguo directamente para simular el paso del tiempo
        conn.execute(
            "UPDATE stock_events SET timestamp = datetime('now', '-40 days') WHERE id = ?",
            (event_id,),
        )
        conn.commit()

        viejos = db.get_events_older_than(conn, days=30)
        assert len(viejos) == 1
        assert viejos[0]["id"] == event_id

    def test_evento_reciente_no_aparece_como_viejo(self, conn, roi_id):
        db.insert_stock_event(
            conn, roi_id, "Góndola Test", "in_stock", "out_of_stock",
            image_path="eventos/reciente.jpg",
        )
        viejos = db.get_events_older_than(conn, days=30)
        assert viejos == []

    def test_clear_event_image_path_conserva_el_evento(self, conn, roi_id):
        event_id = db.insert_stock_event(
            conn, roi_id, "Góndola Test", "in_stock", "out_of_stock",
            image_path="eventos/borrar.jpg",
        )
        db.clear_event_image_path(conn, event_id)

        eventos = db.get_stock_events(conn)
        assert len(eventos) == 1
        assert eventos[0]["image_path"] is None
