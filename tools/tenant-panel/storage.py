"""Upload de fotos do catálogo pro Hetzner Object Storage (S3-compatible),
servidas depois via CDN da Cloudflare — ver specs/catalogo-produtos.md.

Sem essas variáveis configuradas (bucket ainda não provisionado), o
upload falha com erro claro em vez de quebrar silenciosamente; o resto
do painel (conversas, configurações, catálogo sem foto) funciona normal.
"""
import os
import uuid

import boto3
from botocore.config import Config
from fastapi import HTTPException

OBJECT_STORAGE_ENDPOINT = os.environ.get("OBJECT_STORAGE_ENDPOINT", "")
OBJECT_STORAGE_BUCKET = os.environ.get("OBJECT_STORAGE_BUCKET", "")
OBJECT_STORAGE_ACCESS_KEY = os.environ.get("OBJECT_STORAGE_ACCESS_KEY", "")
OBJECT_STORAGE_SECRET_KEY = os.environ.get("OBJECT_STORAGE_SECRET_KEY", "")
# Domínio proxied pela Cloudflare na frente do bucket (cache de imagem) —
# não é o endpoint do Hetzner em si, ver specs/catalogo-produtos.md.
OBJECT_STORAGE_CDN_BASE_URL = os.environ.get("OBJECT_STORAGE_CDN_BASE_URL", "").rstrip("/")
# O bucket é compartilhado com outros projetos (não é exclusivo do
# AtendAgente) — todo objeto nosso vive sob esse prefixo, pra não
# misturar com dados de outro produto no mesmo bucket.
OBJECT_STORAGE_PREFIX = os.environ.get("OBJECT_STORAGE_PREFIX", "atendpragente-pics").strip("/")

MAX_PHOTO_BYTES = 3 * 1024 * 1024
CONTENT_TYPE_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

_client = None


def configured() -> bool:
    return bool(
        OBJECT_STORAGE_ENDPOINT and OBJECT_STORAGE_BUCKET
        and OBJECT_STORAGE_ACCESS_KEY and OBJECT_STORAGE_SECRET_KEY
        and OBJECT_STORAGE_CDN_BASE_URL
    )


def _get_client():
    global _client
    if not configured():
        raise HTTPException(status_code=500, detail="Upload de fotos não configurado neste servidor")
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=OBJECT_STORAGE_ENDPOINT,
            aws_access_key_id=OBJECT_STORAGE_ACCESS_KEY,
            aws_secret_access_key=OBJECT_STORAGE_SECRET_KEY,
            config=Config(signature_version="s3v4"),
        )
    return _client


def upload_photo(tenant_id: str, slug: str, file_bytes: bytes, content_type: str) -> str:
    if len(file_bytes) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Imagem maior que 3MB")
    ext = CONTENT_TYPE_EXT.get(content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Formato de imagem não suportado (use JPEG, PNG ou WebP)")

    # Sufixo aleatório evita servir foto velha em cache do CDN quando o
    # cliente troca a imagem de um produto que já tinha foto.
    key = f"{OBJECT_STORAGE_PREFIX}/{tenant_id}/{slug}-{uuid.uuid4().hex[:8]}.{ext}"
    _get_client().put_object(
        Bucket=OBJECT_STORAGE_BUCKET, Key=key, Body=file_bytes,
        ContentType=content_type, ACL="public-read",
    )
    return f"{OBJECT_STORAGE_CDN_BASE_URL}/{key}"


def delete_photo(foto_url: str | None) -> None:
    """Limpeza de melhor esforço — nunca trava a operação principal
    (editar/excluir produto) se o bucket estiver fora do ar."""
    if not foto_url or not configured() or not foto_url.startswith(OBJECT_STORAGE_CDN_BASE_URL):
        return
    key = foto_url[len(OBJECT_STORAGE_CDN_BASE_URL):].lstrip("/")
    try:
        _get_client().delete_object(Bucket=OBJECT_STORAGE_BUCKET, Key=key)
    except Exception:
        pass
