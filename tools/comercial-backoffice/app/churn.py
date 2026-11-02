"""Mantém as atribuições de comissão alinhadas ao ciclo de vida do tenant.

O onboarding-service é dono do ciclo de vida (live → cancelado → excluído,
ou cancelado → restaurado). O backoffice só vê isso por HTTP, então este
módulo puxa o estado e ajusta `atribuicoes_comissao`:

- tenant em `cancelados` ou `excluidos_recentemente` da lixeira → encerra a
  atribuição (para de contar comissão e de contar como cliente ativo;
  registra churn e se caiu dentro do clawback de 60 dias);
- tenant que voltou a `live` depois de cancelado (restaurado na janela de
  10 dias da lixeira) → reabre a atribuição, na taxa que já estava travada.

Só age com sinal EXPLÍCITO (o tenant aparece na lixeira); tenant ausente de
qualquer lista é ignorado. Assim uma resposta vazia ou incompleta do
onboarding nunca encerra comissão em massa por engano.
"""
import logging
import time

import httpx

from . import onboarding_client, store

log = logging.getLogger("backoffice.churn")

# Chamado a cada carregamento de dashboard/admin; sem isso cada clique
# viraria duas chamadas HTTP ao onboarding-service.
INTERVALO_MINIMO_SEGUNDOS = 60
_ultima_sincronizacao: float | None = None


def sincronizar() -> dict:
    """Uma rodada completa. Levanta httpx.HTTPError se o onboarding estiver
    fora do ar (nada é alterado nesse caso)."""
    lixeira = onboarding_client.listar_lixeira()
    vivos = {t["tenant_id"] for t in onboarding_client.listar_tenants() if t.get("tenant_id")}
    cancelados = {c["tenant_id"] for c in lixeira.get("cancelados", []) if c.get("tenant_id")}
    excluidos = {e["tenant_id"] for e in lixeira.get("excluidos_recentemente", []) if e.get("tenant_id")}

    encerradas, reabertas = [], []
    for atribuicao in store.list_todas_atribuicoes_ativas():
        tenant_id = atribuicao["tenant_id"]
        if tenant_id in vivos:
            continue
        if tenant_id in excluidos:
            motivo = "excluido"
        elif tenant_id in cancelados:
            motivo = "cancelado"
        else:
            continue
        if store.encerrar_atribuicao(tenant_id, motivo):
            encerradas.append(tenant_id)

    for atribuicao in store.list_atribuicoes_canceladas():
        tenant_id = atribuicao["tenant_id"]
        if tenant_id in vivos and store.reabrir_atribuicao(tenant_id):
            reabertas.append(tenant_id)

    return {"encerradas": encerradas, "reabertas": reabertas}


def sincronizar_se_necessario(agora: float | None = None) -> dict | None:
    """Versão segura pra chamar dentro de páginas: respeita o intervalo
    mínimo e nunca derruba a página se o onboarding estiver fora do ar
    (a comissão mostrada fica como estava até a próxima tentativa)."""
    global _ultima_sincronizacao
    momento = time.monotonic() if agora is None else agora
    if _ultima_sincronizacao is not None and momento - _ultima_sincronizacao < INTERVALO_MINIMO_SEGUNDOS:
        return None
    try:
        resultado = sincronizar()
    except httpx.HTTPError as exc:
        log.warning("sincronização de churn adiada, onboarding indisponível: %s", exc)
        return None
    _ultima_sincronizacao = momento
    if resultado["encerradas"] or resultado["reabertas"]:
        log.info("churn sincronizado: %s", resultado)
    return resultado


def reiniciar_intervalo() -> None:
    """Só pros testes: esquece a última sincronização."""
    global _ultima_sincronizacao
    _ultima_sincronizacao = None
