#!/usr/bin/env python3
"""Cria o primeiro usuário admin do backoffice (não existe cadastro público).

A senha é lida da entrada padrão — nunca como argumento, pra não ficar no
histórico do shell nem na lista de processos. Rodando dentro do pod:

    kubectl -n atendagente-admin exec -i deploy/backoffice -- \\
        python /app/criar_admin.py --nome "Fulano" --email fulano@x.com --username fulano <<'EOF'
    a-senha-aqui
    EOF

Recusa username repetido e senha com menos de 8 caracteres. Admin só nasce
por aqui (ou direto no banco): a tela /admin/vendedores só cria vendedores.
"""
import argparse
import sys

MIN_SENHA = 8


def main(argv: list[str] | None = None, entrada=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nome", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--telefone", default="")
    args = parser.parse_args(argv)

    senha = (entrada or sys.stdin).readline().rstrip("\r\n")  # \r: heredoc de terminal Windows
    if len(senha) < MIN_SENHA:
        print(f"Senha precisa ter pelo menos {MIN_SENHA} caracteres (lida da entrada padrão).", file=sys.stderr)
        return 2

    from app import store  # import tardio: exige MONGO_URI só quando de fato vai gravar

    if store.get_vendedor_by_username(args.username):
        print(f"Já existe o usuário '{args.username}'.", file=sys.stderr)
        return 1
    store.create_vendedor(args.nome, args.email, args.telefone, args.username, senha, role="admin")
    print(f"Admin '{args.username}' criado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
