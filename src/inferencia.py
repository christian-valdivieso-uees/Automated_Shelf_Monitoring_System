from ultralytics import YOLO
from picamera2 import Picamera2
import cv2
import time
import os
import glob
import requests

def guardar_imagen_unica(imagen, carpeta="img_bboxes"):
    """
    Guarda la imagen recibida en la carpeta especificada,
    borrando previamente cualquier archivo que exista en ella
    para asegurar que solo haya una imagen por ciclo.
    """
    # 1. Asegurar que la carpeta exista
    if not os.path.exists(carpeta):
        os.makedirs(carpeta)
        
    # 2. Borrar todo el contenido de la carpeta
    archivos = glob.glob(os.path.join(carpeta, "*"))
    for archivo in archivos:
        try:
            if os.path.isfile(archivo):
                os.remove(archivo)
        except Exception as e:
            print(f"Error al eliminar {archivo}: {e}")
            
    # 3. Guardar la nueva imagen
    ruta_salida = os.path.join(carpeta, "output.jpg")
    cv2.imwrite(ruta_salida, imagen)

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
        # Llamar al método para guardar la imagen única en img_bboxes
        guardar_imagen_unica(annotated)
        total_objects = len(results[0].boxes)
        print(f"Detecciones: {total_objects}")
        try:
            # Enviar POST con total_objects
            requests.post('http://localhost:5000/api/camera_info', json={'total_objects': total_objects})
            
            # Consultar all_records
            records_response = requests.get('http://localhost:5000/api/all_records')
            print(records_response.json())
        except Exception as e:
            print(f"Error al enviar o recibir datos: {e}")
        
        time.sleep(5)

except KeyboardInterrupt:
    print("Detenido")
    picam2.stop()