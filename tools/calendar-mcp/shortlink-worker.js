// Cloudflare Worker de encurtador de link (Fase 10, 2026-08-19) — só
// leitura: GET /{id} busca no KV "SHORTLINKS" e redireciona (302). Quem
// grava é server.py (shorten_url), via API REST da Cloudflare (KV
// write), não este Worker. Deployado manualmente via API (não há
// pipeline de CI) — ver comando em infra_shortlink_kv.md na memória do
// projeto pra reaplicar depois de uma mudança aqui:
//   curl -X PUT https://api.cloudflare.com/client/v4/accounts/{account}/workers/scripts/atendpragente-shortlinks \
//     -H "Authorization: Bearer $TOKEN" \
//     -F "metadata=@worker-metadata.json;type=application/json" \
//     -F "shortlink-worker.js=@shortlink-worker.js;type=application/javascript+module"
// Rota: link.atendpragente.com.br/* (zone eff07b89ce80fc01d01533b3327b209a).
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const id = url.pathname.replace(/^\/+/, "");
    if (!id) {
      return new Response("AtendPraGente shortlinks", { status: 200 });
    }
    const dest = await env.SHORTLINKS.get(id);
    if (!dest) {
      return new Response("Link nao encontrado", { status: 404 });
    }
    return Response.redirect(dest, 302);
  },
};
