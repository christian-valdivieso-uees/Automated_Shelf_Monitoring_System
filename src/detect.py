import cv2
from ultralytics import YOLO

# 1. Cargar el modelo NCNN
model = YOLO('best_ncnn_model', task='detect')

# 2. Inicializar la cámara
cap = cv2.VideoCapture(0)

# Opcional: Reducir la resolución de la cámara para mejorar los FPS en el Pi 4
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not cap.isOpened():
    print("Error: No se pudo acceder a la cámara.")
    exit()

print("Iniciando captura... Presiona 'q' para salir.")

while True:
    ret, frame = cap.read()
    if not ret:
        print("Error leyendo el frame de la cámara.")
        break

    # 3. Realizar la inferencia
    results = model(frame, conf=0.25)

    # 4. Dibujar las cajas delimitadoras en el frame
    annotated_frame = results[0].plot()

    # 5. Mostrar el video en pantalla
    cv2.imshow("YOLOv8n NCNN - Detección en vivo", annotated_frame)

    # Condición de salida (Presionar tecla 'q')
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Limpieza de recursos
cap.release()
cv2.destroyAllWindows()