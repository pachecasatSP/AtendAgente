"""Cliente fino pro Google Places API (Text Search) — usado só pela
prospecção automática (app/prospeccao.py). Cada chamada custa dinheiro
acima da cota grátis (5.000/mês no tier Text Search Pro), por isso o
orçamento de chamadas é controlado por quem chama isso, nunca aqui.
"""
import os

import httpx

GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Só os campos que a gente usa — o Places API (New) cobra por SKU
# dependendo dos campos pedidos; pedir só isso mantém a chamada no SKU
# "Pro" mais barato (não pede rating/horário, que sobe pra "Enterprise").
FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri"


def buscar(query: str) -> list[dict]:
    """Uma chamada de Text Search — retorna até 20 resultados brutos da
    API (cada resultado tem id/displayName/formattedAddress/telefone/site,
    conforme FIELD_MASK). Levanta a exceção do httpx se a chamada falhar
    — quem chama decide se aborta o lote ou segue pro próximo termo."""
    if not GOOGLE_PLACES_API_KEY:
        raise RuntimeError("GOOGLE_PLACES_API_KEY não configurada")
    resp = httpx.post(
        SEARCH_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
            "X-Goog-FieldMask": FIELD_MASK,
        },
        json={"textQuery": query, "languageCode": "pt-BR", "regionCode": "BR"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("places", [])
