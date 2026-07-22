from flask import Flask, jsonify, request
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'shelf_monitoring.db')

# Execute a query and return the result
def execute_query(query, data=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if data:
        cursor.execute(query, data)
    else:
        cursor.execute(query)
    conn.commit()
    result = cursor.fetchall()
    conn.close()
    return result

# Initialize the database and create tables if they don't exist
def init_database():
    data_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    
    execute_query('''
        CREATE TABLE IF NOT EXISTS camera_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_objects INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    execute_query('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            contrasena TEXT NOT NULL
        )
    ''')
    
    # Insertar usuario admin por defecto si no existe
    execute_query('''
        INSERT OR IGNORE INTO users (nombre, contrasena)
        VALUES ('admin', 'admin')
    ''')

# Insert a new record into the camera_records table
def insert_camera_record(total_objects):
    execute_query('''
        INSERT INTO camera_records (total_objects, timestamp)
        VALUES (?, ?)
    ''', (total_objects, datetime.now()))

# Keep only the 5 most recent records
def cleanup_old_records():
    execute_query('''
        DELETE FROM camera_records 
        WHERE id NOT IN (
            SELECT id FROM camera_records 
            ORDER BY timestamp DESC 
            LIMIT 5
        )
    ''')

# Get the average of the last 5 records if there are exactly 5 records
def calculate_average_total_objects():
    result = execute_query('''
        SELECT CASE 
            WHEN (SELECT COUNT(*) FROM camera_records) = 5 
            THEN AVG(total_objects) 
            ELSE NULL 
        END FROM camera_records
    ''')

    return result[0][0]

# Get information endpoint
@app.route('/api/average_total_objects', methods=['GET'])
def get_average_total_objects():
    average = calculate_average_total_objects()
    average_int = int(average) if average else None
    
    return jsonify({"average": average_int})

# Get all records endpoint
@app.route('/api/all_records', methods=['GET'])
def get_all_records():
    result = execute_query('''
        SELECT * FROM camera_records
    ''')
    return jsonify(result)

# Endpoint to receive camera information
@app.route('/api/camera_info', methods=['POST'])
def camera_info():
    data = request.get_json()
    total_objects = data.get('total_objects')

    insert_camera_record(total_objects)
    cleanup_old_records()
    
    return jsonify({"status": "ok", "total_objects": total_objects})

if __name__ == '__main__':
    if not os.path.exists(DB_PATH):
        init_database()
    app.run(debug=True)
