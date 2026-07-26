-- ============================================================================
-- Database Schema — Automated Retail Shelf Monitoring System
-- Proyecto Integrador en IA — UEES — Grupo #2
-- Engine: SQLite
-- Naming convention: English identifiers (snake_case), per industry standard.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- 1. USERS
-- Administradores del sistema. RF-01, RF-02, RF-03.
-- La contraseña SIEMPRE se guarda con hash (werkzeug.security), nunca en
-- texto plano — corrige el defecto de la tabla `users` original del proyecto.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    created_at     DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- 2. PASSWORD_RESET_TOKENS
-- Soporta RF-02 (recuperar/resetear contraseña). El token se envía por
-- correo usando la configuración de smtp_config.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token        TEXT NOT NULL UNIQUE,
    expires_at   DATETIME NOT NULL,
    used         BOOLEAN NOT NULL DEFAULT 0,
    created_at   DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_reset_tokens_token ON password_reset_tokens(token);

-- ----------------------------------------------------------------------------
-- 3. ROI_ZONES
-- Zonas de detección configurables sobre el video (RF-08, RF-09, RF-10).
-- Coordenadas normalizadas (0.0–1.0) para ser independientes de la
-- resolución de captura. Guarda también el estado de la máquina de
-- histéresis/debounce para que sobreviva a reinicios del proceso Flask.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roi_zones (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    name                          TEXT NOT NULL,

    -- Geometría del rectángulo (normalizada 0.0–1.0)
    x1                            REAL NOT NULL CHECK (x1 >= 0 AND x1 <= 1),
    y1                            REAL NOT NULL CHECK (y1 >= 0 AND y1 <= 1),
    x2                            REAL NOT NULL CHECK (x2 >= 0 AND x2 <= 1),
    y2                            REAL NOT NULL CHECK (y2 >= 0 AND y2 <= 1),

    -- Umbrales propios de la zona (RF-10)
    low_stock_threshold           INTEGER NOT NULL DEFAULT 2,
    restocked_threshold           INTEGER NOT NULL DEFAULT 4,
    confirmation_readings         INTEGER NOT NULL DEFAULT 3,

    -- Estado de la máquina de histéresis (persistente entre reinicios)
    current_state                 TEXT NOT NULL DEFAULT 'in_stock'
                                     CHECK (current_state IN ('in_stock', 'out_of_stock')),
    candidate_state                TEXT
                                     CHECK (candidate_state IN ('in_stock', 'out_of_stock') OR candidate_state IS NULL),
    candidate_consecutive_readings INTEGER NOT NULL DEFAULT 0,

    active                        BOOLEAN NOT NULL DEFAULT 1,
    created_at                    DATETIME NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at                    DATETIME NOT NULL DEFAULT (datetime('now', 'localtime')),

    CHECK (x2 > x1 AND y2 > y1),
    CHECK (restocked_threshold > low_stock_threshold)
);

-- ----------------------------------------------------------------------------
-- 4. ROI_READINGS
-- Reemplaza la antigua `camera_records` global: cada lectura de inferencia
-- ahora pertenece a una zona específica. Se mantiene solo una ventana
-- móvil reciente por zona (limpieza vía cleanup job, ver backend).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roi_readings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    roi_id         INTEGER NOT NULL REFERENCES roi_zones(id) ON DELETE CASCADE,
    total_objects  INTEGER NOT NULL,
    timestamp      DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_roi_readings_zone_time ON roi_readings(roi_id, timestamp DESC);

-- ----------------------------------------------------------------------------
-- 5. STOCK_EVENTS
-- Solo se inserta una fila cuando la máquina de estado CONFIRMA una
-- transición real (in_stock -> out_of_stock o viceversa). RF-12 a RF-16.
-- `roi_name_snapshot` preserva el nombre aunque la zona se edite/elimine.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    roi_id              INTEGER REFERENCES roi_zones(id) ON DELETE SET NULL,
    roi_name_snapshot   TEXT NOT NULL,
    previous_state      TEXT NOT NULL CHECK (previous_state IN ('in_stock', 'out_of_stock')),
    new_state           TEXT NOT NULL CHECK (new_state IN ('in_stock', 'out_of_stock')),
    avg_total_objects   REAL,
    image_path          TEXT,
    email_sent          BOOLEAN NOT NULL DEFAULT 0,
    timestamp           DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Índices para los filtros de la pantalla de Historial (RF-13, RF-14, RF-15)
CREATE INDEX IF NOT EXISTS idx_stock_events_timestamp ON stock_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_stock_events_roi ON stock_events(roi_id);
CREATE INDEX IF NOT EXISTS idx_stock_events_type ON stock_events(new_state);

-- ----------------------------------------------------------------------------
-- 6. GENERAL_PARAMETERS
-- Configuración clave-valor parametrizable (RF-18). Poblada con valores
-- iniciales representativos ("nota metodológica": ajustar tras pruebas
-- reales en RPi4).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS general_parameters (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL,
    description   TEXT,
    updated_at    DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- 7. SMTP_CONFIG
-- Credenciales de envío de correo (RF-19, RF-20). La contraseña se guarda
-- cifrada de forma REVERSIBLE (Fernet), no con hash, porque el sistema
-- necesita leerla en texto plano para autenticarse ante el servidor SMTP.
-- La clave de cifrado vive fuera de la BD (variable de entorno).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS smtp_config (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    server              TEXT NOT NULL,
    port                INTEGER NOT NULL,
    username            TEXT NOT NULL,
    encrypted_password  TEXT NOT NULL,
    use_tls             BOOLEAN NOT NULL DEFAULT 1,
    active              BOOLEAN NOT NULL DEFAULT 1,
    updated_at          DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- 8. ALERT_RECIPIENTS
-- Lista simple de correos que reciben TODAS las alertas (RF-21) —
-- sin agrupación por zona, según decisión de alcance del equipo.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_recipients (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT NOT NULL UNIQUE,
    active       BOOLEAN NOT NULL DEFAULT 1,
    created_at   DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ============================================================================
-- Datos iniciales (seed)
-- ============================================================================

INSERT OR IGNORE INTO general_parameters (key, value, description) VALUES
    ('image_retention_days',          '30', 'Días de retención de imágenes de eventos antes de limpieza automática (RNF-04).'),
    ('reading_average_window',        '5',  'Cantidad de lecturas recientes por zona usadas para el promedio móvil de conteo.'),
    ('default_confirmation_readings', '3',  'Valor por defecto de lecturas consecutivas para confirmar cambio de estado en zonas nuevas.');

-- Nota: no se inserta un usuario admin por defecto en este script porque la
-- contraseña debe generarse con hash desde Python (ver init_db.py), nunca
-- como texto plano embebido en el esquema SQL.
