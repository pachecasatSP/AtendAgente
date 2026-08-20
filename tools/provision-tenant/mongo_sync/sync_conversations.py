#!/usr/bin/env python3
"""Sidecar de sincronização: espelha sessions/messages do state.db (SQLite,
mantido pelo hermes-agent em /opt/data/) para o MongoDB compartilhado do
namespace atendagente (Fase 5 do roadmap multi-tenant).

Roda em loop dentro do pod do tenant, lendo /opt/data/state.db como
read-only e fazendo upsert incremental no Mongo, usando um cursor
(sync_state) guardado no próprio Mongo — não no volume do tenant, pra
nunca escrever ali.
"""
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import boto3
from botocore.config import Config as BotoConfig
from pymongo import MongoClient, ReturnDocument, UpdateOne

CALENDAR_MCP_BASE_URL = os.environ.get("CALENDAR_MCP_BASE_URL", "http://calendar-mcp.atendagente.svc.cluster.local:8000")
GRAPH_API_VERSION = "v21.0"

TENANT_ID = os.environ["TENANT_ID"]
SYNC_INTERVAL_SECONDS = float(os.environ.get("SYNC_INTERVAL_SECONDS", "15"))
STATE_DB_PATH = "/opt/data/state.db"
MONGO_URI = os.environ["MONGO_URI"]
HANDOFF_HTTP_PORT = int(os.environ.get("HANDOFF_HTTP_PORT", "8091"))

# Object storage (Fase 12) — só pro comprovante, prefixo PRIVADO,
# separado das fotos do catálogo (que são públicas de propósito, a
# vitrine é pública). Secret `object-storage-credentials`, optional —
# se não estiver configurado, comprovante simplesmente não funciona
# (best-effort, nunca derruba o resto do sidecar).
OBJECT_STORAGE_ENDPOINT = os.environ.get("OBJECT_STORAGE_ENDPOINT", "")
OBJECT_STORAGE_BUCKET = os.environ.get("OBJECT_STORAGE_BUCKET", "")
OBJECT_STORAGE_ACCESS_KEY = os.environ.get("OBJECT_STORAGE_ACCESS_KEY", "")
OBJECT_STORAGE_SECRET_KEY = os.environ.get("OBJECT_STORAGE_SECRET_KEY", "")
COMPROVANTE_PREFIX = os.environ.get("OBJECT_STORAGE_COMPROVANTE_PREFIX", "atendpragente-comprovantes").strip("/")
_s3_client = None

# Frases-gatilho que o SOUL instrui o bot a usar, sempre iguais, quando
# decide escalar pra um humano. Detectadas aqui (não via tool-call, que se
# mostrou pouco confiável nesse modelo — ver feedback_hermes_mcp_tool_calling)
# pra marcar a sessão como precisando de atenção manual no painel — sem
# depender de nenhuma mensagem separada pro cliente com telefone nenhum.
# "acionar o adolfo": SOUL da AC Soluções (Duda), ver "Quando encaminhar
# para o Adolfo" em poc/SOUL-ac-solucoes.md.
# "vai te atender rapidinho": SOUL.template.md dos tenants AtendPraGente
# (genérico, não depende do nome de quem escala — ver seção "Quando
# encaminhar", revisado 2026-08-20 pra nunca mais ditar telefone).
ESCALATION_MARKERS = ("acionar o adolfo", "vai te atender rapidinho")

# Chat IDs currently under manual handoff (operador assumiu a conversa pelo
# painel). Refeito do zero a cada ciclo de sync — ver refresh_handoff_cache.
# Consultado pelo processo hermes via HTTP local (mesmo pod) antes de cada
# envio automático, porque o container hermes não tem acesso direto ao
# Mongo (só este sidecar tem).
_handoff_chat_ids: set = set()
_handoff_lock = threading.Lock()

# Coleções `agendamentos`/`signups`, resolvidas uma vez em main() — usadas
# pelo endpoint /agenda-confirmar (Fase 11, Peça 3) pra marcar confirmação/
# remarcação sem passar pelo LLM. Mesma razão do handoff: o container hermes
# não fala com o Mongo diretamente, só este sidecar.
_agendamentos_coll = None
_signups_coll = None

# `pedidos`/`sessions` (referência própria, além da local em main()) —
# usadas pelos endpoints /pedido-fechar e /pedido-status (Fase 12).
_pedidos_coll = None
_sessions_coll = None

PEDIDO_NUMERO_REGEX = re.compile(r"pedido\D{0,10}#?\s*(\d+)", re.IGNORECASE)
PEDIDO_STATUS_KEYWORDS = ("status", "andamento", "rastre")
PEDIDO_STATUS_TERMINAIS = ("pago", "cancelado")


def _crc16_ccitt(payload: str) -> str:
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return format(crc, "04X")


def _emv_campo(id_: str, valor: str) -> str:
    return f"{id_}{len(valor):02d}{valor}"


def montar_payload_pix(chave: str, valor: float, nome_recebedor: str, cidade: str, txid: str) -> str:
    """Monta o payload EMV (BR Code) do "Pix Copia e Cola" — formato
    público do Banco Central, sem gateway/PSP nenhum envolvido (Fase 12,
    decisão explícita de não usar QR: o cliente está dentro do WhatsApp,
    sem segunda câmera pra escanear). `valor` fixo (travado no momento
    do pedido, nunca recalculado depois)."""
    nome_recebedor = (nome_recebedor or "AtendPraGente")[:25]
    cidade = (cidade or "BRASIL")[:15]
    txid = (txid or "***")[:25]
    merchant_account = _emv_campo("00", "br.gov.bcb.pix") + _emv_campo("01", chave)
    payload = (
        _emv_campo("00", "01")
        + _emv_campo("26", merchant_account)
        + _emv_campo("52", "0000")
        + _emv_campo("53", "986")
        + _emv_campo("54", f"{valor:.2f}")
        + _emv_campo("58", "BR")
        + _emv_campo("59", nome_recebedor)
        + _emv_campo("60", cidade)
        + _emv_campo("62", _emv_campo("05", txid))
    )
    payload_com_id_crc = payload + "6304"
    return payload_com_id_crc + _crc16_ccitt(payload_com_id_crc)


def _pedido_extrair_numero(texto: str) -> int | None:
    # A busca sempre bate só no número sequencial — o regex \d+ para no
    # primeiro caractere não-numérico, então o sufixo do tenant
    # ("-a3f2b1c8", ver _sufixo_tenant/_numero_exibicao) é ignorado
    # automaticamente sem precisar de tratamento especial. tenant_id já
    # escopa a busca (este processo só atende um tenant), o sufixo é
    # só cosmético/de exibição.
    match = PEDIDO_NUMERO_REGEX.search(texto or "")
    return int(match.group(1)) if match else None


_sufixo_tenant_cache: str | None = None


def _sufixo_tenant() -> str:
    """Últimos 8 caracteres do ObjectId do signup — mesmo cálculo de
    `tenant-panel/app.py:_sufixo_tenant`, duplicado aqui porque são
    processos/deploys separados (mesmo raciocínio de sempre nesse
    projeto)."""
    global _sufixo_tenant_cache
    if _sufixo_tenant_cache is not None:
        return _sufixo_tenant_cache
    tenant = _signups_coll.find_one({"tenant_id": TENANT_ID, "status": "live"}) if _signups_coll is not None else None
    sufixo = str((tenant or {}).get("_id") or "")[-8:] or "00000000"
    _sufixo_tenant_cache = sufixo
    return sufixo


def _numero_exibicao(numero: int) -> str:
    # Hífen é obrigatório — sem ele, o sufixo hex pode começar com
    # dígito (0-9) e colar no número sequencial, quebrando
    # PEDIDO_NUMERO_REGEX (\d+ não teria onde parar).
    return f"{numero}-{_sufixo_tenant().upper()}"


def _pedido_sugestoes(chat_id: str) -> list:
    if _pedidos_coll is None:
        return []
    return list(_pedidos_coll.find(
        {"tenant_id": TENANT_ID, "chat_id": chat_id, "status": {"$in": ["aberto", "aguardando_pagamento"]}}
    ).sort("numero", 1))


def _agrupar_por_lote(pedidos: list) -> list:
    """Agrupa uma lista de pedidos pelo `lote_id` (mesmo carrinho) —
    pedido sem lote_id vira grupo de 1 item só. Usado sempre que se
    lista pedidos em aberto pro cliente, pra não mostrar cada item de
    um mesmo carrinho como se fosse um pedido separado."""
    grupos: dict = {}
    for p in pedidos:
        chave = p.get("lote_id") or str(p["_id"])
        grupos.setdefault(chave, []).append(p)
    return list(grupos.values())


def _pedido_formatar_sugestoes(pedidos: list) -> str:
    grupos = _agrupar_por_lote(pedidos)
    linhas = [
        f"#{_numero_exibicao(grupo[0]['numero'])} — " + ", ".join(p["item_nome"] for p in grupo)
        for grupo in grupos
    ]
    return (
        "Não encontrei esse pedido, mas você tem esses em aberto:\n"
        + "\n".join(linhas)
        + "\nÉ algum desses? Me manda só o número (ex: \"pedido #47\")."
    )


def _gerar_pix(valor: float, txid: str) -> str | None:
    if _signups_coll is None:
        return None
    tenant = _signups_coll.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not tenant:
        return None
    config = tenant.get("config") or {}
    chave = config.get("pagamento_pix_chave")
    if not chave:
        return None
    nome = config.get("nome_negocio") or "AtendPraGente"
    return montar_payload_pix(chave, valor, nome, "BRASIL", txid)


def _pedido_lote(pedido: dict) -> list:
    """Pedido de referência + irmãos do mesmo `lote_id` (mesmo carrinho
    da vitrine, Fase 12 revisado) ainda elegíveis. Pedido sem lote_id
    (avulso, ou de antes dessa revisão) vira um "lote" de 1 item só —
    mantém compatibilidade sem exigir migração de dado antigo."""
    lote_id = pedido.get("lote_id")
    if not lote_id or _pedidos_coll is None:
        return [pedido]
    itens = list(_pedidos_coll.find({
        "tenant_id": TENANT_ID, "lote_id": lote_id,
        "status": {"$in": ["aberto", "aguardando_pagamento"]},
    }).sort("numero", 1))
    return itens or [pedido]


def pedido_fechar(chat_id: str, texto: str) -> dict:
    """Lógica completa do gatilho "quero fechar o pedido #N" — chamada
    pelo webhook patchado (whatsapp_cloud_patched.py) via HTTP local,
    sem o LLM decidir nada (mesmo bypass da Fase 11 Peça 3). Sempre
    escopado por TENANT_ID (chat_id sozinho não identifica o pedido —
    o mesmo número de telefone pode falar com bots de tenants
    diferentes). O número referenciado pode ser só o primeiro item de
    um carrinho com vários — `_pedido_lote` acha os irmãos e o Pix sai
    com o valor somado do lote inteiro, um código só."""
    if _pedidos_coll is None:
        return {"ok": False}
    numero = _pedido_extrair_numero(texto)
    pedido = _pedidos_coll.find_one({"tenant_id": TENANT_ID, "numero": numero}) if numero is not None else None

    if pedido and pedido.get("status") in PEDIDO_STATUS_TERMINAIS:
        msg = (
            "Esse pedido já está confirmado como pago, obrigado! 🙏"
            if pedido["status"] == "pago"
            else "Esse pedido foi cancelado — se quiser, dá uma olhada na vitrine de novo pra fazer um novo pedido."
        )
        return {"ok": True, "resposta": msg}

    elegivel = pedido and pedido.get("status") in ("aberto", "aguardando_pagamento") and (
        not pedido.get("chat_id") or pedido["chat_id"] == chat_id
    )
    if elegivel:
        lote = _pedido_lote(pedido)
        total = sum(p["valor"] for p in lote)
        txid = f"LOTE{pedido['lote_id'][:8]}" if pedido.get("lote_id") else f"PED{pedido['numero']}"
        payload_pix = _gerar_pix(total, txid)
        if payload_pix is None:
            return {
                "ok": True,
                "resposta": "Consegui achar seu pedido, mas ainda não tenho uma forma de pagamento configurada — vou verificar e te aviso.",
            }
        _pedidos_coll.update_many(
            {"_id": {"$in": [p["_id"] for p in lote]}},
            {"$set": {"chat_id": chat_id, "status": "aguardando_pagamento"}},
        )
        if len(lote) == 1:
            resumo = f"Pedido #{_numero_exibicao(lote[0]['numero'])} — {lote[0]['item_nome']}"
        else:
            linhas = [f"• {p['item_nome']} (R$ {p['valor']:.2f})" for p in lote]
            resumo = f"Pedido #{_numero_exibicao(lote[0]['numero'])} ({len(lote)} itens):\n" + "\n".join(linhas)
        resposta = (
            f"{resumo}\nTotal: R$ {total:.2f}\n\n"
            f"Pix Copia e Cola:\n{payload_pix}\n\n"
            "Abre o Pix do seu banco → Copia e Cola → cola esse código → confere o valor → confirma o "
            "pagamento. Depois é só mandar o comprovante aqui que a gente confirma pra você."
        )
        return {"ok": True, "resposta": resposta}

    sugestoes = _pedido_sugestoes(chat_id)
    if sugestoes:
        return {"ok": True, "resposta": _pedido_formatar_sugestoes(sugestoes)}

    if numero is not None:
        return {
            "ok": True,
            "resposta": "Não encontrei esse pedido. Pode confirmar o número? (o mesmo que apareceu quando você clicou em \"Pedir\" na vitrine)",
        }

    return {"ok": False}


RASTREIO_MENSAGENS_STATUS = {
    "em_preparacao": "Seu pedido #{numero} está em preparação! 📦 Assim que for enviado eu te aviso.",
    "enviado": "Seu pedido #{numero} já foi enviado! 🚚",
    "entregue": "Seu pedido #{numero} já foi entregue! 🎉 Qualquer coisa, é só chamar.",
}


def pedido_status(chat_id: str, texto: str) -> dict:
    """Frase-gatilho "qual o status do meu pedido #N" (ou toque no botão
    nativo "Rastrear pedido" da confirmação de pagamento). Responde
    direto com o `rastreio_status` já registrado no painel (Fase 12) —
    sem precisar de operador. Só aciona handoff (Fase 5) quando não há
    rastreio pra informar (pedido ainda não pago, ou não encontrado) —
    nesses casos não tem o que o bot responder sozinho."""
    if _pedidos_coll is None:
        return {"ok": False}
    numero = _pedido_extrair_numero(texto)
    pedido = None
    if numero is not None:
        pedido = _pedidos_coll.find_one({"tenant_id": TENANT_ID, "numero": numero, "chat_id": chat_id})
    if not pedido:
        pedido = _pedidos_coll.find_one(
            {"tenant_id": TENANT_ID, "chat_id": chat_id}, sort=[("criado_em", -1)]
        )
    if not pedido:
        return {"ok": False}

    modelo = RASTREIO_MENSAGENS_STATUS.get(pedido.get("rastreio_status"))
    if modelo:
        return {"ok": True, "resposta": modelo.format(numero=_numero_exibicao(pedido["numero"]))}

    _marcar_handoff_pedido(chat_id)
    return {
        "ok": True,
        "resposta": "Já avisei nossa equipe pra verificar o status do seu pedido — em breve alguém te responde por aqui! 🙏",
    }


def _object_storage_configured() -> bool:
    return bool(OBJECT_STORAGE_ENDPOINT and OBJECT_STORAGE_BUCKET and OBJECT_STORAGE_ACCESS_KEY and OBJECT_STORAGE_SECRET_KEY)


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3", endpoint_url=OBJECT_STORAGE_ENDPOINT,
            aws_access_key_id=OBJECT_STORAGE_ACCESS_KEY,
            aws_secret_access_key=OBJECT_STORAGE_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )
    return _s3_client


_COMPROVANTE_EXT = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}


def _baixar_e_armazenar_comprovante(media_id: str, mime_type: str) -> str | None:
    """Baixa o comprovante da Graph API (2 passos: resolve a URL do
    media_id, depois baixa os bytes) e sobe pro object storage num
    prefixo PRIVADO — sem ACL public-read, diferente do resto do bucket
    (fotos de catálogo são públicas de propósito). Devolve a `key`
    (não uma URL pública) — o painel serve isso autenticado, nunca por
    link direto. Best-effort: qualquer falha devolve None."""
    if not _object_storage_configured() or _signups_coll is None:
        return None
    tenant = _signups_coll.find_one({"tenant_id": TENANT_ID, "status": "live"})
    access_token = (tenant or {}).get("access_token")
    if not access_token:
        return None

    try:
        req = urllib.request.Request(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            info = json.loads(resp.read())
        media_url = info.get("url")
        if not media_url:
            return None
        req2 = urllib.request.Request(media_url, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(req2, timeout=20) as resp2:
            conteudo = resp2.read()
    except Exception as e:
        print(f"[mongo-sync] comprovante: falha ao baixar mídia: {e}")
        return None

    ext = _COMPROVANTE_EXT.get(mime_type, "bin")
    key = f"{COMPROVANTE_PREFIX}/{TENANT_ID}/{uuid.uuid4().hex}.{ext}"
    try:
        _get_s3_client().put_object(
            Bucket=OBJECT_STORAGE_BUCKET, Key=key, Body=conteudo, ContentType=mime_type,
        )  # sem ACL="public-read" de propósito — dado financeiro/pessoal
    except Exception as e:
        print(f"[mongo-sync] comprovante: falha ao subir pro storage: {e}")
        return None
    return key


def pedido_comprovante(chat_id: str, media_id: str, mime_type: str, legenda: str = "") -> dict:
    """Cliente mandou foto/PDF do comprovante — chamado pelo webhook
    quando há pedido `aguardando_pagamento` pra esse chat_id. Sem
    pedido correspondente, quem chama já nem deveria ter invocado isso
    (o webhook só chama quando sabe que há pelo menos 1 candidato).
    Agrupado por `lote_id` (Fase 12 revisado) — um comprovante só
    fecha o carrinho inteiro, não item por item."""
    if _pedidos_coll is None:
        return {"ok": False}
    candidatos = list(_pedidos_coll.find(
        {"tenant_id": TENANT_ID, "chat_id": chat_id, "status": "aguardando_pagamento"}
    ))
    if not candidatos:
        return {"ok": False}

    grupos = _agrupar_por_lote(candidatos)
    lote = None
    if len(grupos) == 1:
        lote = grupos[0]
    else:
        numero = _pedido_extrair_numero(legenda)
        if numero is not None:
            lote = next((g for g in grupos if any(p["numero"] == numero for p in g)), None)
        if lote is None:
            linhas = [
                f"#{_numero_exibicao(grupo[0]['numero'])} — " + ", ".join(p["item_nome"] for p in grupo)
                for grupo in grupos
            ]
            return {
                "ok": True,
                "resposta": "Antes de eu confirmar o recebimento, me diz o número do pedido:\n" + "\n".join(linhas),
            }

    comprovante_key = _baixar_e_armazenar_comprovante(media_id, mime_type)
    if comprovante_key is None:
        return {
            "ok": True,
            "resposta": "Recebi sua mensagem, mas não consegui salvar o comprovante agora — pode tentar mandar de novo em instantes?",
        }

    _pedidos_coll.update_many(
        {"_id": {"$in": [p["_id"] for p in lote]}},
        {"$set": {
            "status": "comprovante_recebido",
            "comprovante_key": comprovante_key,
            "comprovante_recebido_em": now(),
        }},
    )
    _marcar_handoff_pedido(chat_id)
    return {"ok": True, "resposta": f"Recebi o comprovante do pedido #{_numero_exibicao(lote[0]['numero'])}! Vou confirmar e já te aviso. 🙏"}


def _marcar_handoff_pedido(chat_id: str) -> None:
    """Marca só a sessão MAIS RECENTE desse chat_id — nunca `update_many`
    por chat_id sozinho. Bug real encontrado 2026-08-20: um chat_id tem
    várias sessões ao longo do tempo (reinícios de conversa, etc.); o
    cache de handoff (`refresh_handoff_cache`) suprime envio pro
    chat_id inteiro se QUALQUER sessão dele tiver `handoff: true` —
    então marcar sessões antigas/mortas junto contaminava o número
    inteiro pra sempre, mesmo depois da sessão viva ser resolvida. Mesmo
    cuidado que o handoff da Fase 5 (escalação) já tinha, que filtra por
    `session_id` específico, não por chat_id solto."""
    if _sessions_coll is None:
        return
    sessao_atual = _sessions_coll.find_one(
        {"tenant_id": TENANT_ID, "chat_id": chat_id}, sort=[("last_activity_at", -1)]
    )
    if not sessao_atual:
        return
    _sessions_coll.update_one(
        {"_id": sessao_atual["_id"]},
        {"$set": {
            "needs_operator": True, "needs_operator_at": now(),
            "handoff": True, "handoff_by": "pedido (rastreamento manual)", "handoff_at": now(),
        }},
    )


AGENDA_ACAO_PARA_STATUS = {"confirmar": "confirmado", "remarcar": "recusado"}


def refresh_handoff_cache(sessions_coll) -> None:
    chat_ids = {
        doc["chat_id"]
        for doc in sessions_coll.find(
            {"tenant_id": TENANT_ID, "handoff": True}, {"chat_id": 1}
        )
        if doc.get("chat_id")
    }
    with _handoff_lock:
        _handoff_chat_ids.clear()
        _handoff_chat_ids.update(chat_ids)


def _cancelar_evento_google(agendamento: dict) -> None:
    """Apaga o evento na Google Agenda quando o cliente pede remarcação —
    reabre o horário na hora em vez de deixar bloqueado até o dono do
    negócio apagar manualmente (decisão do usuário, 2026-08-20). Só o
    calendar-mcp tem credencial Google; este sidecar não tenta falar com
    a Calendar API diretamente, só repassa via HTTP autenticado com o
    calendar_mcp_token do próprio tenant (mesmo token que o tenant-panel
    usa, lido de `signups.config`). Best-effort: qualquer falha aqui só
    loga, nunca derruba a resposta que o cliente já recebeu."""
    google_event_id = agendamento.get("google_event_id")
    if not google_event_id or _signups_coll is None:
        return
    tenant = _signups_coll.find_one({"tenant_id": agendamento.get("tenant_id"), "status": "live"})
    if not tenant:
        return
    calendar_mcp_token = (tenant.get("config") or {}).get("calendar_mcp_token")
    if not calendar_mcp_token:
        return
    req = urllib.request.Request(
        f"{CALENDAR_MCP_BASE_URL}/cancelar-evento",
        data=json.dumps({"google_event_id": google_event_id}).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {calendar_mcp_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            print(f"[mongo-sync] cancelar-evento tenant={agendamento.get('tenant_id')} status={resp.status}")
    except urllib.error.URLError as e:
        print(f"[mongo-sync] cancelar-evento tenant={agendamento.get('tenant_id')} falhou: {e}")


class HandoffHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # silencia log padrão por request
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/handoff":
            self.send_response(404)
            self.end_headers()
            return
        chat_id = (parse_qs(parsed.query).get("chat_id") or [""])[0]
        with _handoff_lock:
            active = chat_id in _handoff_chat_ids
        body = json.dumps({"handoff": active}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}

        if parsed.path == "/agenda-confirmar":
            result = self._handle_agenda_confirmar(payload)
        elif parsed.path == "/pedido-fechar":
            result = pedido_fechar(str(payload.get("chat_id") or ""), str(payload.get("texto") or ""))
        elif parsed.path == "/pedido-status":
            result = pedido_status(str(payload.get("chat_id") or ""), str(payload.get("texto") or ""))
        elif parsed.path == "/pedido-comprovante":
            result = pedido_comprovante(
                str(payload.get("chat_id") or ""), str(payload.get("media_id") or ""),
                str(payload.get("mime_type") or ""), str(payload.get("legenda") or ""),
            )
        else:
            self.send_response(404)
            self.end_headers()
            return

        body = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_agenda_confirmar(self, payload: dict) -> dict:
        agendamento_id = str(payload.get("agendamento_id") or "")
        acao = str(payload.get("acao") or "")
        novo_status = AGENDA_ACAO_PARA_STATUS.get(acao)
        ok = False
        if novo_status is not None and agendamento_id and _agendamentos_coll is not None:
            try:
                oid = ObjectId(agendamento_id)
            except InvalidId:
                oid = None
            if oid is not None:
                # Só marca se ainda estiver aguardando — evita que um duplo
                # tap (ou reenvio do webhook, comum na Cloud API) sobrescreva
                # uma resposta já processada. find_one_and_update (em vez de
                # update_one) devolve o doc atualizado direto, sem precisar
                # de um find() extra — é dele que _cancelar_evento_google
                # pega google_event_id/tenant_id.
                doc_atualizado = _agendamentos_coll.find_one_and_update(
                    {"_id": oid, "status": "aguardando_confirmacao"},
                    {"$set": {"status": novo_status, "confirmado_em": now()}},
                    return_document=ReturnDocument.AFTER,
                )
                ok = doc_atualizado is not None
                if ok and acao == "remarcar":
                    try:
                        _cancelar_evento_google(doc_atualizado)
                    except Exception as e:
                        print(f"[mongo-sync] cancelar-evento ERRO: {e}")
        return {"ok": ok}


def start_handoff_http_server() -> None:
    server = HTTPServer(("127.0.0.1", HANDOFF_HTTP_PORT), HandoffHTTPHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[mongo-sync] tenant={TENANT_ID} handoff HTTP em 127.0.0.1:{HANDOFF_HTTP_PORT}")


def now() -> datetime:
    return datetime.now(timezone.utc)


def get_cursor(sync_state_coll) -> dict:
    doc = sync_state_coll.find_one({"_id": TENANT_ID})
    if doc is None:
        return {"last_message_id": 0, "last_session_activity": 0}
    return doc


def sync_once(conn: sqlite3.Connection, db, cursor: dict) -> dict:
    sessions_coll = db["sessions"]
    messages_coll = db["messages"]
    sync_state_coll = db["sync_state"]

    session_rows = conn.execute(
        "SELECT id, source, user_id, chat_id, chat_type, thread_id, "
        "display_name, started_at, ended_at, last_activity_at, "
        "message_count FROM sessions WHERE last_activity_at > ? OR "
        "(last_activity_at IS NULL AND ? = '')",
        (cursor["last_session_activity"], cursor["last_session_activity"]),
    ).fetchall()

    session_ops = []
    max_session_activity = cursor["last_session_activity"]
    for row in session_rows:
        (
            sid, source, user_id, chat_id, chat_type, thread_id,
            display_name, started_at, ended_at, last_activity_at,
            message_count,
        ) = row
        if not chat_id:
            # Corrida na criação da sessão: last_activity_at já existe mas
            # chat_id ainda não foi gravado no SQLite (visto na prática
            # 2026-08-15). Não sincroniza nem avança o cursor pra essa
            # linha — ela reaparece na consulta do próximo ciclo (15s)
            # assim que o SQLite preencher o chat_id de verdade, em vez
            # de gravar um doc órfão (sem chat_id o painel não consegue
            # enviar mensagem — "Conversa sem chat_id — não é possível
            # enviar") que só se corrigiria com nova atividade na sessão.
            continue
        doc = {
            "tenant_id": TENANT_ID,
            "session_id": sid,
            "source": source,
            "user_id": user_id,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "thread_id": thread_id,
            "display_name": display_name,
            "started_at": started_at,
            "ended_at": ended_at,
            "last_activity_at": last_activity_at,
            "message_count": message_count,
            "synced_at": now(),
        }
        session_ops.append(
            UpdateOne({"_id": f"{TENANT_ID}:{sid}"}, {"$set": doc}, upsert=True)
        )
        if last_activity_at and str(last_activity_at) > str(max_session_activity):
            max_session_activity = last_activity_at
    if session_ops:
        sessions_coll.bulk_write(session_ops, ordered=False)

    message_rows = conn.execute(
        "SELECT id, session_id, role, content, timestamp, tool_call_id, "
        "tool_name, platform_message_id, active, compacted, display_kind "
        "FROM messages WHERE id > ? ORDER BY id ASC",
        (cursor["last_message_id"],),
    ).fetchall()

    message_ops = []
    escalated_session_ids = set()
    max_message_id = cursor["last_message_id"]
    for row in message_rows:
        (
            mid, session_id, role, content, timestamp, tool_call_id,
            tool_name, platform_message_id, active, compacted, display_kind,
        ) = row
        doc = {
            "tenant_id": TENANT_ID,
            "message_id": mid,
            "session_id": session_id,
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "platform_message_id": platform_message_id,
            "active": active,
            "compacted": compacted,
            "display_kind": display_kind,
            "synced_at": now(),
        }
        message_ops.append(
            UpdateOne({"_id": f"{TENANT_ID}:{mid}"}, {"$set": doc}, upsert=True)
        )
        max_message_id = max(max_message_id, mid)
        if role == "assistant" and content and any(m in content.lower() for m in ESCALATION_MARKERS):
            escalated_session_ids.add(session_id)
    if message_ops:
        messages_coll.bulk_write(message_ops, ordered=False)

    if escalated_session_ids:
        # handoff:true também, não só o alerta visual — a Duda disse que
        # ia acionar um humano, então ela mesma já para de responder até
        # o operador (ou o botão "resolver" no painel) devolver o
        # controle. Sem isso o bot continuava respondendo normalmente
        # logo depois de anunciar que ia escalar.
        sessions_coll.update_many(
            {"tenant_id": TENANT_ID, "session_id": {"$in": list(escalated_session_ids)}},
            {"$set": {
                "needs_operator": True,
                "needs_operator_at": now(),
                "handoff": True,
                "handoff_by": "bot (auto-escalação)",
                "handoff_at": now(),
            }},
        )

    new_cursor = {
        "last_message_id": max_message_id,
        "last_session_activity": max_session_activity,
        "updated_at": now(),
    }
    sync_state_coll.update_one(
        {"_id": TENANT_ID}, {"$set": new_cursor}, upsert=True
    )
    return {"sessions": len(session_ops), "messages": len(message_ops)}


def ensure_indexes(db) -> None:
    db["sessions"].create_index([("tenant_id", 1), ("last_activity_at", -1)])
    db["messages"].create_index([("tenant_id", 1), ("session_id", 1), ("timestamp", 1)])
    db["messages"].create_index([("tenant_id", 1), ("message_id", -1)])


def main() -> None:
    global _agendamentos_coll, _signups_coll, _pedidos_coll, _sessions_coll
    client = MongoClient(MONGO_URI)
    db = client.get_default_database()
    ensure_indexes(db)
    sync_state_coll = db["sync_state"]
    sessions_coll = db["sessions"]
    _agendamentos_coll = db["agendamentos"]
    _signups_coll = db["signups"]
    _pedidos_coll = db["pedidos"]
    _sessions_coll = sessions_coll

    start_handoff_http_server()
    print(f"[mongo-sync] tenant={TENANT_ID} interval={SYNC_INTERVAL_SECONDS}s iniciado")

    while True:
        try:
            conn = sqlite3.connect(f"file:{STATE_DB_PATH}?mode=ro", uri=True)
            conn.execute("PRAGMA query_only = 1;")
            cursor = get_cursor(sync_state_coll)
            result = sync_once(conn, db, cursor)
            conn.close()
            print(
                f"[mongo-sync] tenant={TENANT_ID} "
                f"sessions={result['sessions']} messages={result['messages']}"
            )
        except sqlite3.OperationalError as e:
            print(f"[mongo-sync] tenant={TENANT_ID} state.db indisponível ainda: {e}")
        except Exception as e:
            print(f"[mongo-sync] tenant={TENANT_ID} ERRO: {e}")

        try:
            refresh_handoff_cache(sessions_coll)
        except Exception as e:
            print(f"[mongo-sync] tenant={TENANT_ID} handoff refresh ERRO: {e}")

        time.sleep(SYNC_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
