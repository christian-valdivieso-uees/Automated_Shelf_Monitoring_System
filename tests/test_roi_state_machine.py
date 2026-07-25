"""
Pruebas unitarias de app.roi_state_machine.

Cubre los criterios de aceptación de RF-07 (indicador de estado por
zona) y la justificación de ADR-05 (histéresis + debounce evita
falsas alarmas por ruido de detección).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.roi_state_machine import (
    RoiState,
    StateTransition,
    evaluate_reading,
    moving_average,
    IN_STOCK,
    OUT_OF_STOCK,
)


def make_roi(**overrides):
    """Zona de prueba: umbral bajo=2, recuperado=4, confirmación=3 lecturas."""
    defaults = dict(
        low_stock_threshold=2,
        restocked_threshold=4,
        confirmation_readings=3,
        current_state=IN_STOCK,
    )
    defaults.update(overrides)
    return RoiState(**defaults)


class TestValidacionDeConstruccion:
    def test_rechaza_umbral_recuperado_menor_o_igual_al_bajo(self):
        with pytest.raises(ValueError):
            make_roi(low_stock_threshold=5, restocked_threshold=3)

    def test_rechaza_confirmation_readings_menor_a_uno(self):
        with pytest.raises(ValueError):
            make_roi(confirmation_readings=0)

    def test_rechaza_estado_inicial_invalido(self):
        with pytest.raises(ValueError):
            make_roi(current_state="no_existe")


class TestZonaMuertaDeHisteresis:
    """Lecturas entre ambos umbrales no deben proponer ningún cambio."""

    def test_lectura_en_zona_muerta_no_mueve_el_estado(self):
        roi = make_roi()
        transition = evaluate_reading(avg_count=3, roi=roi)
        assert transition is None
        assert roi.current_state == IN_STOCK
        assert roi.candidate_state is None
        assert roi.candidate_consecutive_readings == 0

    def test_lectura_igual_al_estado_vigente_no_acumula_candidato(self):
        roi = make_roi(current_state=IN_STOCK)
        # avg_count alto reafirma in_stock, no debería iniciar candidato
        evaluate_reading(avg_count=6, roi=roi)
        assert roi.candidate_state is None
        assert roi.candidate_consecutive_readings == 0


class TestConfirmacionDeTransicion:
    def test_no_confirma_con_menos_lecturas_que_confirmation_readings(self):
        roi = make_roi(confirmation_readings=3)
        r1 = evaluate_reading(avg_count=1, roi=roi)
        r2 = evaluate_reading(avg_count=1, roi=roi)
        assert r1 is None
        assert r2 is None
        assert roi.current_state == IN_STOCK
        assert roi.candidate_consecutive_readings == 2

    def test_confirma_transicion_a_sin_stock_tras_n_lecturas_consecutivas(self):
        roi = make_roi(confirmation_readings=3)
        evaluate_reading(avg_count=1, roi=roi)
        evaluate_reading(avg_count=1, roi=roi)
        transition = evaluate_reading(avg_count=1, roi=roi)

        assert transition == StateTransition(previous_state=IN_STOCK, new_state=OUT_OF_STOCK)
        assert roi.current_state == OUT_OF_STOCK
        assert roi.candidate_state is None
        assert roi.candidate_consecutive_readings == 0

    def test_confirma_transicion_de_regreso_a_con_stock(self):
        roi = make_roi(confirmation_readings=2, current_state=OUT_OF_STOCK)
        r1 = evaluate_reading(avg_count=5, roi=roi)
        assert r1 is None
        r2 = evaluate_reading(avg_count=5, roi=roi)
        assert r2 == StateTransition(previous_state=OUT_OF_STOCK, new_state=IN_STOCK)
        assert roi.current_state == IN_STOCK


class TestResistenciaAlRuido:
    """
    El caso que justifica ADR-05: una lectura aislada y ruidosa no debe
    disparar una alerta si no se sostiene por N lecturas consecutivas.
    """

    def test_una_lectura_aislada_no_confirma_ni_deja_rastro(self):
        roi = make_roi(confirmation_readings=3)
        evaluate_reading(avg_count=1, roi=roi)  # candidato out_of_stock, contador=1
        transition = evaluate_reading(avg_count=6, roi=roi)  # vuelve a in_stock: resetea

        assert transition is None
        assert roi.current_state == IN_STOCK
        assert roi.candidate_state is None
        assert roi.candidate_consecutive_readings == 0

    def test_oscilacion_intermitente_nunca_confirma(self):
        """
        Alterna entre proponer out_of_stock e in_stock sin sostenerse:
        con histéresis + debounce, jamás debería confirmarse un cambio.
        """
        roi = make_roi(confirmation_readings=3)
        lecturas = [1, 6, 1, 6, 1, 6, 1, 6]
        transiciones = [evaluate_reading(avg_count=v, roi=roi) for v in lecturas]

        assert all(t is None for t in transiciones)
        assert roi.current_state == IN_STOCK

    def test_reinicia_contador_si_el_candidato_cambia_a_mitad_de_camino(self):
        roi = make_roi(confirmation_readings=3)
        evaluate_reading(avg_count=1, roi=roi)  # candidato=out_of_stock, contador=1
        evaluate_reading(avg_count=1, roi=roi)  # contador=2
        evaluate_reading(avg_count=3, roi=roi)  # zona muerta: resetea a 0
        assert roi.candidate_state is None
        assert roi.candidate_consecutive_readings == 0

        # Debe volver a necesitar 3 lecturas completas desde cero
        r1 = evaluate_reading(avg_count=1, roi=roi)
        r2 = evaluate_reading(avg_count=1, roi=roi)
        assert r1 is None and r2 is None
        r3 = evaluate_reading(avg_count=1, roi=roi)
        assert r3 == StateTransition(previous_state=IN_STOCK, new_state=OUT_OF_STOCK)


class TestMovingAverage:
    def test_calcula_promedio_simple(self):
        assert moving_average([2, 4, 6]) == 4.0

    def test_lista_vacia_lanza_error(self):
        with pytest.raises(ValueError):
            moving_average([])


class TestCasosDeBorde:
    def test_avg_count_exactamente_en_el_umbral_bajo_propone_sin_stock(self):
        roi = make_roi(low_stock_threshold=2, confirmation_readings=1)
        transition = evaluate_reading(avg_count=2, roi=roi)
        assert transition == StateTransition(previous_state=IN_STOCK, new_state=OUT_OF_STOCK)

    def test_avg_count_exactamente_en_el_umbral_recuperado_propone_con_stock(self):
        roi = make_roi(restocked_threshold=4, confirmation_readings=1, current_state=OUT_OF_STOCK)
        transition = evaluate_reading(avg_count=4, roi=roi)
        assert transition == StateTransition(previous_state=OUT_OF_STOCK, new_state=IN_STOCK)
