"""kubectl de mentira para testar implantar.py: estado num JSON (FAKE_STATE), log de chamadas em FAKE_LOG."""
import json
import os
import sys

estado_path, log_path = os.environ["FAKE_STATE"], os.environ["FAKE_LOG"]
a = sys.argv[1:]
open(log_path, "a").write(" ".join(a[:5]) + "\n")
estado = json.load(open(estado_path))
ns = a[1]
cmd = a[2]


def mescla(destino, patch):
    for chave, valor in patch.items():
        if chave == "containers":
            for c in valor:
                alvo = next(x for x in destino["containers"] if x["name"] == c["name"])
                for k, v in c.items():
                    if k == "env":
                        for e in v:
                            ex = next((x for x in alvo.setdefault("env", []) if x["name"] == e["name"]), None)
                            if ex:
                                ex.update(e)
                            else:
                                alvo["env"].append(e)
                    elif k != "name":
                        alvo[k] = v
        elif isinstance(valor, dict) and isinstance(destino.get(chave), dict):
            mescla(destino[chave], valor)
        else:
            destino[chave] = valor


if cmd == "get":
    tipos = a[3].split(",")
    itens = [i for i in estado[ns] if (i["kind"] == "Deployment" and "deployments" in tipos) or (i["kind"] == "CronJob" and "cronjobs" in tipos)]
    print(json.dumps({"items": itens}))
elif cmd == "patch":
    tipo, nome = a[3].split("/")
    item = next(i for i in estado[ns] if i["metadata"]["name"] == nome)
    mescla(item, json.loads(a[a.index("-p") + 1]))
    json.dump(estado, open(estado_path, "w"))
    print("patched")
elif cmd == "rollout":
    nome = a[4].split("/")[1]
    if nome in os.environ.get("FAKE_FALHA_ROLLOUT", "").split(","):
        print("error: timed out waiting for the condition", file=sys.stderr)
        sys.exit(1)
    print("successfully rolled out")
else:
    sys.exit(2)
