from ultralytics import YOLO
from picamera2 import Picamera2
import cv2
import time

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

        # Dibujar detecciones y mostrar/guardar
        annotated = results[0].plot()
        cv2.imwrite("output.jpg", annotated)  # o cv2.imshow si tienes monitor

        print(f"Detecciones: {len(results[0].boxes)}")
        time.sleep(0.1)

except KeyboardInterrupt:
    print("Detenido")
    picam2.stop()