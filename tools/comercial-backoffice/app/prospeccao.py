"""Busca automática de negócios por cidade+ramo no Google Places, pra
alimentar a lista fria de prospecção (specs/comissao-vendedores.md,
seção 11 — captação de leads). Roda em dois lugares com o mesmo motor:
um CronJob diário (orçamento fixo, barato) e um botão manual no admin
(`/admin/prospeccao/buscar-mais`, pra quando o vendedor precisa de mais
candidatos antes da próxima rodada automática).

Orçamento: cada chamada de Text Search custa acima da cota grátis do
Google (5.000/mês) — o CronJob usa 50/dia de propósito (~1.500/mês,
dentro da cota). O botão manual soma ao mesmo orçamento do dia, então
usar ele empurra o total do dia pra cima — ver README pra como
acompanhar gasto real no Google Cloud Console.
"""
from . import places_client, store

CIDADES = ["São Pedro da Aldeia", "Cabo Frio", "Búzios", "Araruama"]

# Comércio/serviço simples (mix-alvo Entrada/Começando) + turismo de
# hospedagem/passeio (mix-alvo Crescimento/Consolidado) — mesma
# segmentação do spec, seção 11. Deliberadamente sem "agência de
# turismo" (fora do perfil de cliente que usa WhatsApp pra atender
# consumidor final direto).
CATEGORIAS = [
    "loja de roupas", "salão de beleza", "barbearia", "pet shop",
    "farmácia", "ótica", "papelaria", "loja de conveniência",
    "oficina mecânica", "pousada", "restaurante", "buffet de festas",
    "passeio de barco",
]

QUERY_MATRIX = [f"{categoria} em {cidade}" for cidade in CIDADES for categoria in CATEGORIAS]


def buscar_lote(max_chamadas: int) -> dict:
    """Continua da posição salva (round-robin pela matriz inteira, sem
    repetir termo à toa dentro da mesma volta) e faz até max_chamadas
    buscas. Cada resultado é upsertado por place_id — rodar de novo
    sobre o mesmo termo só atualiza `visto_em`, não duplica."""
    cursor = store.get_prospeccao_cursor()
    total = len(QUERY_MATRIX)
    novos = 0
    atualizados = 0
    erros = []
    chamadas = 0
    while chamadas < max_chamadas:
        query = QUERY_MATRIX[cursor % total]
        cidade = next((c for c in CIDADES if c in query), "")
        categoria = query.replace(f" em {cidade}", "")
        try:
            resultados = places_client.buscar(query)
            for lugar in resultados:
                place_id = lugar.get("id")
                if not place_id:
                    continue
                nome = lugar.get("displayName", {}).get("text", "")
                telefone = lugar.get("nationalPhoneNumber")
                store.upsert_prospeccao(
                    place_id=place_id,
                    negocio=nome,
                    endereco=lugar.get("formattedAddress", ""),
                    cidade=cidade,
                    categoria=categoria,
                    telefone=telefone,
                    site=lugar.get("websiteUri"),
                )
                novos += 1
        except Exception as exc:  # noqa: BLE001 — segue pro próximo termo, não trava o lote
            erros.append(f"{query}: {exc}")
        cursor += 1
        chamadas += 1
    store.set_prospeccao_cursor(cursor)
    return {"chamadas": chamadas, "resultados_upsertados": novos, "erros": erros}
