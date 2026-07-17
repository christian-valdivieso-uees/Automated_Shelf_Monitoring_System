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

## Comandos de instalación de librerias

Actualiza la lista de paquetes disponibles en los servidores

```bash
sudo apt update
```

Instala Picamera2 directamente en el entorno global del sistema de la Raspberry Pi, junto con libgl1-mesa-glx y libcamera-dev, que son librerías del sistema necesarias para que el procesamiento de imágenes y OpenCV funcionen sin errores de renderizado.

```bash
sudo apt install -y python3-picamera2 libgl1-mesa-glx libcamera-dev
```

Crea una carpeta llamada `venv` que contiene tu entorno virtual. El argumento vital aquí es --system-site-packages. Esto crea un "puente" que permite que este entorno aislado pueda acceder a las librerías instaladas en el sistema operativo (como el python3-picamera2 del paso anterior) sin tener que reinstalarlas dentro.

```bash
python3 -m venv --system-site-packages venv
```

Activa el entorno virtual. Notarás que el prompt de tu terminal ahora comienza con (venv)

```bash
source venv/bin/activate
```

Instala PyTorch y Torchvision (requeridos por Ultralytics). Al añadir --index-url [https://download.pytorch.org/whl/cpu](https://download.pytorch.org/whl/cpu), forzamos a que descargue la versión compilada estrictamente para procesadores (CPU). Esto reduce el tamaño de la instalación de más de 2 GB a solo unos pocos cientos de megabytes.

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

Instala la librería ultralytics para gestionar tu modelo YOLO y el módulo ncnn, que es el motor de inferencia necesario para leer y ejecutar los pesos de tu modelo de forma fluida.

```bash
pip install ultralytics ncnn
```

Instala OpenCV. El sufijo -headless es fundamental para ahorrar memoria: instala la librería sin los módulos de interfaz gráfica (como las funciones para abrir ventanas flotantes en el escritorio).

```bash
pip install opencv-python-headless
```

Para ejecutar tu archivo, debes usar el comando de Python asegurándote de que el entorno virtual esté activo (tu terminal muestra (venv) al inicio de la línea), simplemente ejecuta:

```bash
python3 inferencia.py
```
