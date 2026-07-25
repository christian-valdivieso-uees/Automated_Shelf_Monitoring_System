"""
roi_counter.py

Lógica de asignación de detecciones (cajas delimitadoras de YOLO) a las
zonas ROI configuradas (RF-05, RF-06). Es un módulo puro: no depende de
la cámara, YOLO, Flask ni SQLite — recibe coordenadas simples y devuelve
conteos, lo que lo hace trivial de probar de forma aislada.

Convención de coordenadas:
- Las zonas ROI se guardan normalizadas (0.0–1.0) en roi_zones (x1,y1,x2,y2).
- Las detecciones de YOLO llegan en píxeles absolutos del frame capturado
  (results[0].boxes.xyxy), dependientes de la resolución de captura.
- Este módulo convierte el ROI a píxeles usando el tamaño real del frame,
  para que la asignación sea correcta sin importar la resolución.
"""

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class BoundingBox:
    """Caja delimitadora en píxeles absolutos, tal como la entrega YOLO."""
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self):
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)


@dataclass(frozen=True)
class RoiPixelBounds:
    """Límites de una zona ROI ya convertidos a píxeles absolutos."""
    x1: float
    y1: float
    x2: float
    y2: float


def roi_to_pixels(roi_x1: float, roi_y1: float, roi_x2: float, roi_y2: float,
                   frame_width: int, frame_height: int) -> RoiPixelBounds:
    """
    Convierte coordenadas normalizadas (0.0–1.0) de una zona ROI a
    píxeles absolutos, dado el tamaño real del frame capturado.
    """
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("frame_width y frame_height deben ser positivos")
    if not (0 <= roi_x1 < roi_x2 <= 1) or not (0 <= roi_y1 < roi_y2 <= 1):
        raise ValueError("Coordenadas de ROI inválidas (deben cumplir 0<=x1<x2<=1)")

    return RoiPixelBounds(
        x1=roi_x1 * frame_width,
        y1=roi_y1 * frame_height,
        x2=roi_x2 * frame_width,
        y2=roi_y2 * frame_height,
    )


def is_center_inside(box: BoundingBox, roi_px: RoiPixelBounds) -> bool:
    """
    Determina si el CENTRO de una detección cae dentro de una zona ROI.
    Se usa el centro (no la caja completa) porque una botella que cruza
    el borde del ROI debe contarse en la zona donde está su mayor parte.
    """
    cx, cy = box.center
    return roi_px.x1 <= cx <= roi_px.x2 and roi_px.y1 <= cy <= roi_px.y2


def count_detections_in_roi(boxes: Sequence[BoundingBox], roi_px: RoiPixelBounds) -> int:
    """Cuenta cuántas detecciones tienen su centro dentro de la zona ROI."""
    return sum(1 for box in boxes if is_center_inside(box, roi_px))


def assign_detections_to_rois(boxes: Sequence[BoundingBox], roi_zones: Sequence[dict],
                               frame_width: int, frame_height: int) -> dict:
    """
    Distribuye un conjunto de detecciones entre varias zonas ROI activas.

    `roi_zones` es una lista de dicts con al menos las claves
    id, x1, y1, x2, y2 (tal como devuelve db.get_active_roi_zones()).

    Devuelve un dict {roi_id: conteo}. Una misma detección puede contarse
    en más de una zona si los ROI se superponen (decisión deliberada: cada
    zona es independiente y no se asume exclusividad geométrica).
    """
    counts = {}
    for roi in roi_zones:
        roi_px = roi_to_pixels(roi["x1"], roi["y1"], roi["x2"], roi["y2"], frame_width, frame_height)
        counts[roi["id"]] = count_detections_in_roi(boxes, roi_px)
    return counts
