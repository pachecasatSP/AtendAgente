"""Limite de tentativas falhas (login e PIN) em memória.

Um pod só (replicas: 1), então o estado em memória basta; se o backoffice
passar a ter réplicas, isto precisa ir pro Mongo. Reiniciar o pod zera o
contador — aceitável: o objetivo é barrar força bruta, não auditar.
"""
import time

JANELA_SEGUNDOS = 15 * 60
MAX_FALHAS = 5

_falhas: dict[str, list[float]] = {}


def _recentes(chave: str, agora: float) -> list[float]:
    recentes = [t for t in _falhas.get(chave, []) if agora - t < JANELA_SEGUNDOS]
    if recentes:
        _falhas[chave] = recentes
    else:
        _falhas.pop(chave, None)
    return recentes


def bloqueado(chave: str, agora: float | None = None) -> bool:
    return len(_recentes(chave, time.monotonic() if agora is None else agora)) >= MAX_FALHAS


def registrar_falha(chave: str, agora: float | None = None) -> None:
    momento = time.monotonic() if agora is None else agora
    _recentes(chave, momento)
    _falhas.setdefault(chave, []).append(momento)


def zerar(chave: str) -> None:
    _falhas.pop(chave, None)


def reiniciar() -> None:
    """Só pros testes."""
    _falhas.clear()
