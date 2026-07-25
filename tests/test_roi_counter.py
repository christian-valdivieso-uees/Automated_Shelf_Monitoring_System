import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.roi_counter import (
    BoundingBox,
    RoiPixelBounds,
    roi_to_pixels,
    is_center_inside,
    count_detections_in_roi,
    assign_detections_to_rois,
)


class TestRoiToPixels:
    def test_convierte_coordenadas_normalizadas_a_pixeles(self):
        roi_px = roi_to_pixels(0.1, 0.2, 0.5, 0.8, frame_width=640, frame_height=480)
        assert roi_px == RoiPixelBounds(x1=64.0, y1=96.0, x2=320.0, y2=384.0)

    def test_frame_completo_cubre_todo_el_rango(self):
        roi_px = roi_to_pixels(0.0, 0.0, 1.0, 1.0, frame_width=1280, frame_height=720)
        assert roi_px == RoiPixelBounds(x1=0.0, y1=0.0, x2=1280.0, y2=720.0)

    def test_rechaza_frame_con_ancho_o_alto_invalido(self):
        with pytest.raises(ValueError):
            roi_to_pixels(0.0, 0.0, 1.0, 1.0, frame_width=0, frame_height=480)

    def test_rechaza_roi_con_x2_menor_o_igual_a_x1(self):
        with pytest.raises(ValueError):
            roi_to_pixels(0.5, 0.0, 0.5, 1.0, frame_width=640, frame_height=480)

    def test_rechaza_coordenadas_fuera_de_rango_0_1(self):
        with pytest.raises(ValueError):
            roi_to_pixels(0.0, 0.0, 1.5, 1.0, frame_width=640, frame_height=480)


class TestIsCenterInside:
    def test_centro_claramente_dentro(self):
        roi_px = RoiPixelBounds(x1=0, y1=0, x2=100, y2=100)
        box = BoundingBox(x1=40, y1=40, x2=60, y2=60)  # centro en (50,50)
        assert is_center_inside(box, roi_px) is True

    def test_centro_claramente_fuera(self):
        roi_px = RoiPixelBounds(x1=0, y1=0, x2=100, y2=100)
        box = BoundingBox(x1=200, y1=200, x2=220, y2=220)
        assert is_center_inside(box, roi_px) is False

    def test_centro_exactamente_sobre_el_borde_cuenta_como_dentro(self):
        roi_px = RoiPixelBounds(x1=0, y1=0, x2=100, y2=100)
        box = BoundingBox(x1=90, y1=90, x2=110, y2=110)  # centro en (100,100)
        assert is_center_inside(box, roi_px) is True

    def test_caja_que_cruza_el_borde_pero_centro_fuera_no_cuenta(self):
        """Una botella que apenas asoma en el ROI no debe contarse ahí."""
        roi_px = RoiPixelBounds(x1=0, y1=0, x2=100, y2=100)
        box = BoundingBox(x1=95, y1=95, x2=150, y2=150)  # centro en (122.5, 122.5)
        assert is_center_inside(box, roi_px) is False


class TestCountDetectionsInRoi:
    def test_cuenta_solo_las_que_caen_dentro(self):
        roi_px = RoiPixelBounds(x1=0, y1=0, x2=100, y2=100)
        boxes = [
            BoundingBox(10, 10, 20, 20),   # dentro
            BoundingBox(30, 30, 40, 40),   # dentro
            BoundingBox(200, 200, 220, 220),  # fuera
        ]
        assert count_detections_in_roi(boxes, roi_px) == 2

    def test_lista_vacia_de_detecciones_da_cero(self):
        roi_px = RoiPixelBounds(x1=0, y1=0, x2=100, y2=100)
        assert count_detections_in_roi([], roi_px) == 0


class TestAssignDetectionsToRois:
    def test_distribuye_conteos_por_zona_independiente(self):
        boxes = [
            BoundingBox(10, 10, 20, 20),     # zona A (arriba)
            BoundingBox(15, 15, 25, 25),     # zona A (arriba)
            BoundingBox(10, 210, 20, 220),   # zona B (abajo)
        ]
        roi_zones = [
            {"id": 1, "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 0.5},   # mitad superior
            {"id": 2, "x1": 0.0, "y1": 0.5, "x2": 1.0, "y2": 1.0},   # mitad inferior
        ]
        conteos = assign_detections_to_rois(boxes, roi_zones, frame_width=100, frame_height=400)
        assert conteos == {1: 2, 2: 1}

    def test_zonas_superpuestas_pueden_contar_la_misma_deteccion_dos_veces(self):
        """Decisión deliberada: cada ROI es independiente, no se asume exclusividad."""
        boxes = [BoundingBox(45, 45, 55, 55)]  # centro en (50,50)
        roi_zones = [
            {"id": 1, "x1": 0.0, "y1": 0.0, "x2": 0.8, "y2": 0.8},
            {"id": 2, "x1": 0.2, "y1": 0.2, "x2": 1.0, "y2": 1.0},
        ]
        conteos = assign_detections_to_rois(boxes, roi_zones, frame_width=100, frame_height=100)
        assert conteos == {1: 1, 2: 1}

    def test_ninguna_zona_configurada_devuelve_diccionario_vacio(self):
        boxes = [BoundingBox(10, 10, 20, 20)]
        assert assign_detections_to_rois(boxes, [], frame_width=100, frame_height=100) == {}
