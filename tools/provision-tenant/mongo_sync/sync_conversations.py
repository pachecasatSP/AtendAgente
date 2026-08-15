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
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from pymongo import MongoClient, UpdateOne

TENANT_ID = os.environ["TENANT_ID"]
SYNC_INTERVAL_SECONDS = float(os.environ.get("SYNC_INTERVAL_SECONDS", "15"))
STATE_DB_PATH = "/opt/data/state.db"
MONGO_URI = os.environ["MONGO_URI"]
HANDOFF_HTTP_PORT = int(os.environ.get("HANDOFF_HTTP_PORT", "8091"))

# Frase-gatilho que o SOUL instrui o bot a usar, sempre igual, quando
# decide escalar pra um humano — ver "Quando encaminhar para o Adolfo"
# em poc/SOUL-ac-solucoes.md. Detectada aqui (não via tool-call, que se
# mostrou pouco confiável nesse modelo — ver feedback_hermes_mcp_tool_calling)
# pra marcar a sessão como precisando de atenção manual no painel.
ESCALATION_MARKER = "acionar o adolfo"

# Chat IDs currently under manual handoff (operador assumiu a conversa pelo
# painel). Refeito do zero a cada ciclo de sync — ver refresh_handoff_cache.
# Consultado pelo processo hermes via HTTP local (mesmo pod) antes de cada
# envio automático, porque o container hermes não tem acesso direto ao
# Mongo (só este sidecar tem).
_handoff_chat_ids: set = set()
_handoff_lock = threading.Lock()


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
        if role == "assistant" and content and ESCALATION_MARKER in content.lower():
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
    client = MongoClient(MONGO_URI)
    db = client.get_default_database()
    ensure_indexes(db)
    sync_state_coll = db["sync_state"]
    sessions_coll = db["sessions"]

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
