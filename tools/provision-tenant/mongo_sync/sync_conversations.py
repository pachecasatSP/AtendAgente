#!/usr/bin/env python3
"""Sidecar de sincronização: espelha sessions/messages do state.db (SQLite,
mantido pelo hermes-agent em /opt/data/) para o MongoDB compartilhado do
namespace atendagente (Fase 5 do roadmap multi-tenant).

Roda em loop dentro do pod do tenant, lendo /opt/data/state.db como
read-only e fazendo upsert incremental no Mongo, usando um cursor
(sync_state) guardado no próprio Mongo — não no volume do tenant, pra
nunca escrever ali.
"""
import os
import sqlite3
import time
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne

TENANT_ID = os.environ["TENANT_ID"]
SYNC_INTERVAL_SECONDS = float(os.environ.get("SYNC_INTERVAL_SECONDS", "15"))
STATE_DB_PATH = "/opt/data/state.db"
MONGO_URI = os.environ["MONGO_URI"]


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
    if message_ops:
        messages_coll.bulk_write(message_ops, ordered=False)

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

        time.sleep(SYNC_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
