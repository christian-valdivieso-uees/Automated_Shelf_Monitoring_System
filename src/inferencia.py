from ultralytics import YOLO
from picamera2 import Picamera2
import cv2
import time
import requests
import base64


# 1. Cargar el modelo NCNN
model = YOLO('best_ncnn_model')

# 2. Inicializar la cámara con picamera2 (NO cv2.VideoCapture)
picam2 = Picamera2()
config = picam2.create_preview_configuration(main={"size": (640, 480), "format": "RGB888"})
picam2.configure(config)
picam2.start()
time.sleep(2)  # deja que ajuste exposición

try:
    while True:
        frame = picam2.capture_array()  # numpy array, ya en RGB888
        results = model(frame)
        annotated = results[0].plot()
        total_objects = len(results[0].boxes)
        print(f"Detecciones: {total_objects}")
        try:
            # Convertir imagen a base64
            _, buffer = cv2.imencode('.jpg', annotated)
            image_base64 = base64.b64encode(buffer).decode('utf-8')
            
            # Enviar POST con total_objects e imagen
            requests.post('http://localhost:5000/api/camera_info', json={'total_objects': total_objects, 'image': image_base64})
            
            # Consultar all_records
            records_response = requests.get('http://localhost:5000/api/all_records')
            print(records_response.json())
        except Exception as e:
            print(f"Error al enviar o recibir datos: {e}")
        
        time.sleep(5)

except KeyboardInterrupt:
    print("Detenido")
    picam2.stop()