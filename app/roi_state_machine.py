"""
roi_state_machine.py

Lógica de confirmación de cambio de estado de stock por zona (ROI),
usando histéresis (dos umbrales distintos) + debounce (N lecturas
consecutivas) para evitar falsas alarmas por ruido de detección
entre frames (ADR-05, ver Diseño de Arquitectura de Software).

Este módulo es deliberadamente independiente de Flask, SQLite y la
cámara: opera solo sobre diccionarios simples, lo que lo hace
trivial de probar de forma aislada (ver tests/test_roi_state_machine.py).
"""

from dataclasses import dataclass
from typing import Optional


IN_STOCK = "in_stock"
OUT_OF_STOCK = "out_of_stock"

VALID_STATES = (IN_STOCK, OUT_OF_STOCK)


@dataclass
class RoiState:
    """Estado persistente de una zona ROI (espejo de la tabla roi_zones)."""
    low_stock_threshold: int
    restocked_threshold: int
    confirmation_readings: int
    current_state: str = IN_STOCK
    candidate_state: Optional[str] = None
    candidate_consecutive_readings: int = 0

    def __post_init__(self):
        if self.current_state not in VALID_STATES:
            raise ValueError(f"current_state inválido: {self.current_state}")
        if self.restocked_threshold <= self.low_stock_threshold:
            raise ValueError(
                "restocked_threshold debe ser mayor que low_stock_threshold "
                f"(recibido: restocked={self.restocked_threshold}, "
                f"low={self.low_stock_threshold})"
            )
        if self.confirmation_readings < 1:
            raise ValueError("confirmation_readings debe ser >= 1")


@dataclass
class StateTransition:
    """Resultado cuando la máquina CONFIRMA un cambio real de estado."""
    previous_state: str
    new_state: str


def _propose_state(avg_count: float, roi: RoiState) -> Optional[str]:
    """
    Determina qué estado propondría esta lectura, según los umbrales
    de histéresis. Devuelve None si el conteo cae en la "zona muerta"
    entre ambos umbrales (no hay motivo para proponer un cambio).
    """
    if avg_count <= roi.low_stock_threshold:
        return OUT_OF_STOCK
    if avg_count >= roi.restocked_threshold:
        return IN_STOCK
    return None


def evaluate_reading(avg_count: float, roi: RoiState) -> Optional[StateTransition]:
    """
    Evalúa una nueva lectura (promedio móvil de conteo) contra el estado
    actual de la zona. Muta `roi` in-place actualizando su progreso hacia
    un posible cambio de estado, y devuelve un StateTransition SOLO cuando
    el cambio queda confirmado tras `confirmation_readings` lecturas
    consecutivas a favor del mismo estado candidato.

    Reglas:
    - Si la lectura no propone cambio, o propone el mismo estado ya vigente,
      se resetea cualquier progreso de candidato (evita que lecturas
      aisladas y no consecutivas acumulen falsamente el contador).
    - Si la lectura propone un estado distinto al vigente:
        - si coincide con el candidato en curso, se incrementa el contador.
        - si es un candidato nuevo (o no había), se reinicia el contador en 1.
        - si el contador alcanza confirmation_readings, se CONFIRMA el
          cambio: se actualiza current_state y se limpia el candidato.
    """
    proposal = _propose_state(avg_count, roi)

    if proposal is None or proposal == roi.current_state:
        roi.candidate_state = None
        roi.candidate_consecutive_readings = 0
        return None

    if roi.candidate_state == proposal:
        roi.candidate_consecutive_readings += 1
    else:
        roi.candidate_state = proposal
        roi.candidate_consecutive_readings = 1

    if roi.candidate_consecutive_readings >= roi.confirmation_readings:
        previous_state = roi.current_state
        roi.current_state = proposal
        roi.candidate_state = None
        roi.candidate_consecutive_readings = 0
        return StateTransition(previous_state=previous_state, new_state=proposal)

    return None


def moving_average(readings: list) -> float:
    """Promedio simple de una ventana de lecturas recientes de conteo."""
    if not readings:
        raise ValueError("No se puede calcular el promedio de una lista vacía")
    return sum(readings) / len(readings)
