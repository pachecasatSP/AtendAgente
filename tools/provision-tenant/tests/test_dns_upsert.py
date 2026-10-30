"""create_dns_record deixa o host com UM registro A no IP certo (antes só acrescentava e duplicava)."""
import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HOST, IP, VELHO, OUTRO = "loja.atendpragente.com.br", "18.228.100.213", "62.238.103.17", "2.28.15.6"


class Cloudflare(BaseHTTPRequestHandler):
    registros: list = []
    chamadas: list = []
    post_erro = None
    get_status = 200

    def log_message(self, *a):
        pass

    def _responde(self, status, corpo):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(corpo).encode())

    def do_GET(self):
        u = urlparse(self.path)
        Cloudflare.chamadas.append(("GET", u.path))
        if Cloudflare.get_status != 200:
            return self._responde(Cloudflare.get_status, {"success": False, "errors": [{"code": 1, "message": "boom"}]})
        nome = parse_qs(u.query).get("name", [""])[0]
        tipo = parse_qs(u.query).get("type", [None])[0]
        res = [r for r in Cloudflare.registros if r["name"] == nome and (tipo is None or r["type"] == tipo)]
        self._responde(200, {"success": True, "result": res})

    def _corpo(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_POST(self):
        Cloudflare.chamadas.append(("POST", urlparse(self.path).path))
        if Cloudflare.post_erro:
            return self._responde(400, {"success": False, "errors": [{"code": Cloudflare.post_erro}]})
        r = {**self._corpo(), "id": f"id{len(Cloudflare.registros) + 1}"}
        Cloudflare.registros.append(r)
        self._responde(200, {"success": True, "result": r})

    def do_PUT(self):
        Cloudflare.chamadas.append(("PUT", urlparse(self.path).path))
        rid = urlparse(self.path).path.rsplit("/", 1)[-1]
        for r in Cloudflare.registros:
            if r["id"] == rid:
                r.update(self._corpo())
        self._responde(200, {"success": True})

    def do_DELETE(self):
        Cloudflare.chamadas.append(("DELETE", urlparse(self.path).path))
        rid = urlparse(self.path).path.rsplit("/", 1)[-1]
        Cloudflare.registros = [r for r in Cloudflare.registros if r["id"] != rid]
        self._responde(200, {"success": True})


@pytest.fixture()
def pt(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Cloudflare)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("CLOUDFLARE_API_BASE", f"http://127.0.0.1:{srv.server_port}")
    Cloudflare.registros, Cloudflare.chamadas, Cloudflare.post_erro, Cloudflare.get_status = [], [], None, 200
    sys.modules.pop("provision_tenant", None)
    mod = importlib.import_module("provision_tenant")
    yield mod
    srv.shutdown()


def a(ip, rid, nome=HOST):
    return {"id": rid, "type": "A", "name": nome, "content": ip, "ttl": 300, "proxied": False}


def ips():
    return sorted(r["content"] for r in Cloudflare.registros if r["name"] == HOST)


def metodos():
    return [m for m, _ in Cloudflare.chamadas]


def test_sem_registro_cria(pt):
    pt.create_dns_record(HOST, IP, "tok", "zona1")
    assert ips() == [IP] and metodos() == ["GET", "POST"]
    r = Cloudflare.registros[0]
    assert (r["type"], r["ttl"], r["proxied"]) == ("A", 300, False)


def test_ja_certo_nao_mexe(pt):
    Cloudflare.registros = [a(IP, "1")]
    pt.create_dns_record(HOST, IP, "tok", "zona1")
    assert ips() == [IP] and metodos() == ["GET"]


def test_ip_velho_e_atualizado_sem_duplicar(pt):
    Cloudflare.registros = [a(VELHO, "1")]
    pt.create_dns_record(HOST, IP, "tok", "zona1")
    assert ips() == [IP] and metodos() == ["GET", "PUT"]


def test_certo_mais_velho_apaga_o_velho(pt):
    Cloudflare.registros = [a(IP, "1"), a(VELHO, "2")]
    pt.create_dns_record(HOST, IP, "tok", "zona1")
    assert ips() == [IP] and metodos() == ["GET", "DELETE"]


def test_dois_velhos_viram_um_certo(pt):
    Cloudflare.registros = [a(VELHO, "1"), a(OUTRO, "2")]
    pt.create_dns_record(HOST, IP, "tok", "zona1")
    assert ips() == [IP] and metodos() == ["GET", "PUT", "DELETE"]


def test_nao_toca_em_outros_hosts(pt):
    Cloudflare.registros = [a(VELHO, "1", "outro.atendpragente.com.br"), a(VELHO, "2")]
    pt.create_dns_record(HOST, IP, "tok", "zona1")
    assert [r["content"] for r in Cloudflare.registros if r["name"] == "outro.atendpragente.com.br"] == [VELHO]


def test_corrida_registro_ja_existe_nao_e_fatal(pt):
    Cloudflare.post_erro = 81057
    pt.create_dns_record(HOST, IP, "tok", "zona1")  # não levanta


def test_erro_do_cloudflare_vira_provision_error(pt):
    Cloudflare.get_status = 500
    with pytest.raises(pt.ProvisionError, match="500"):
        pt.create_dns_record(HOST, IP, "tok", "zona1")
