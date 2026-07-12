# Automated Shelf Monitoring System - Inferencia

Este repositorio contiene el script de inferencia para el sistema automatizado de monitoreo de estantes, diseñado para ejecutarse en una Raspberry Pi 4 utilizando el módulo de cámara oficial y un modelo YOLO optimizado en formato NCNN.

## 📝 Descripción

El archivo principal `inferencia.py` realiza la detección de objetos de forma continua capturando imágenes directamente desde la cámara de la Raspberry Pi mediante `picamera2` y procesándolas con un modelo YOLO (exportado a formato NCNN para mayor eficiencia). 

## 🔧 Requisitos y Dependencias

- **Hardware:** Raspberry Pi con un módulo de cámara oficial habilitado.
- **Sistema Operativo:** Raspberry Pi OS (Bullseye o posterior, que incluya soporte para libcamera/picamera2).
- **Python:** 3.8+

### Librerías Necesarias

Instala las dependencias de Python necesarias:

```bash
pip install ultralytics opencv-python
```

*Nota: La librería `picamera2` generalmente viene preinstalada en las versiones recientes de Raspberry Pi OS.*

## 🚀 Ejecución

```bash
python src/inferencia.py
```

## ⚙️ Cómo Funciona (`src/inferencia.py`)

1. **Carga del Modelo:** Se carga el modelo YOLO optimizado para inferencia en dispositivos de bajos recursos (`best_ncnn_model`).
2. **Inicialización de la Cámara:** Utiliza `Picamera2` configurada con una resolución de 640x480 en formato `RGB888`. Se añade un pequeño retraso (2 segundos) para permitir que la cámara ajuste la exposición automáticamente.
3. **Bucle de Captura e Inferencia:**
   - Captura el frame actual de la cámara como un array de Numpy.
   - Ejecuta la inferencia utilizando el modelo YOLO cargado.
   - Dibuja las cajas delimitadoras (bounding boxes) en la imagen (`annotated = results[0].plot()`).
   - Guarda la imagen procesada como `output.jpg` en el directorio de ejecución, sobrescribiéndose en cada iteración.
   - Muestra en consola la cantidad de objetos detectados en el frame actual.
4. **Detención Segura:** El script incluye manejo de excepciones para `KeyboardInterrupt`. Puedes detener la ejecución presionando `Ctrl+C`, lo cual detendrá la cámara de manera segura.
