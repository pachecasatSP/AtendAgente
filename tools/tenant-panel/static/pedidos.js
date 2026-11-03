/* Pedidos (/pedidos). Rotas: GET /painel/api/pedidos?offset=N, POST /painel/api/pedidos/{id}/{pago|cancelar|avancar-rastreio|rastreio-visto|endereco},
   GET /painel/api/pedidos/{id}/comprovante. Mesma lógica de sempre: rolagem infinita + poll de 15 s só da 1ª página. */
(function () {
  'use strict';
  var STATUS = {
    aberto: ['', 'Aberto (sem contato)'],
    aguardando_pagamento: ['aviso', 'Aguardando pagamento'],
    comprovante_recebido: ['aviso', 'Comprovante recebido'],
    pago: ['ok', 'Pago'],
    cancelado: ['erro', 'Cancelado']
  };
  var RASTREIO_SEQUENCIA = ['em_preparacao', 'enviado', 'entregue'];
  var RASTREIO_LABEL = { em_preparacao: 'Em preparação', enviado: 'Enviado', entregue: 'Entregue' };
  var BRL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  var offset = 0, carregando = false, acabou = false, verificando = false;
  var pedidosPorId = new Map();
  var $ = function (id) { return document.getElementById(id); };

  function escapeHtml(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function icone(nome) { return '<svg class="icone" aria-hidden="true"><use href="#i-' + nome + '"/></svg>'; }
  function fmtValor(v) { return BRL.format(v || 0); }
  function fmtData(iso) { return iso ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''; }

  function renderPedido(p) {
    var row = document.createElement('article');
    row.className = 'pedido-row'; row.dataset.id = p.id;
    var st = STATUS[p.status] || ['', p.status];
    var acoes = [];
    if (p.tem_comprovante) acoes.push('<a class="btn contorno" href="/painel/api/pedidos/' + encodeURIComponent(p.id) + '/comprovante" target="_blank" rel="noopener">Ver comprovante<span class="so-leitor"> (abre em outra aba)</span></a>');
    if (p.status !== 'pago' && p.status !== 'cancelado') {
      acoes.push('<button type="button" class="btn" data-acao="pago">Marcar como pago</button>');
      acoes.push('<button type="button" class="btn contorno perigo" data-acao="cancelar">Cancelar pedido</button>');
    }
    if (p.status === 'pago') {
      var atual = p.rastreio_status || RASTREIO_SEQUENCIA[0];
      var proximo = RASTREIO_SEQUENCIA[RASTREIO_SEQUENCIA.indexOf(atual) + 1];
      if (proximo) acoes.push('<button type="button" class="btn contorno" data-especial="avancar-rastreio">' + RASTREIO_LABEL[atual] + ' → ' + RASTREIO_LABEL[proximo] + '</button>');
      else acoes.push('<span class="pill ok">' + RASTREIO_LABEL[atual] + '</span>');
    }
    var qtd = (p.itens || []).length;
    var contato = escapeHtml(p.contato_nome || p.chat_id || '');
    // Endereço só para pedido com produto (evento não entrega). A captura automática preenche sozinha; aqui fica editável como fallback.
    var endereco = p.precisa_endereco ?
      '<div class="pedido-endereco">' + icone('pino') + '<label for="end-' + escapeHtml(p.id) + '">Endereço</label>' +
      '<input type="text" id="end-' + escapeHtml(p.id) + '" class="pedido-endereco-input" autocomplete="street-address" placeholder="Ainda não informado" value="' + escapeHtml(p.endereco || '') + '">' +
      '<button type="button" class="btn contorno" data-especial="salvar-endereco">Salvar</button></div>' : '';
    // Cliente perguntou o status e não havia rastreio para responder sozinho: fica visível até o operador marcar que já respondeu.
    var alerta = p.rastreio_solicitado_em ?
      '<div class="pedido-alerta-rastreio" role="status">' + icone('alerta') + '<span>Cliente perguntou o rastreio às ' + fmtData(p.rastreio_solicitado_em) + '. Responda manualmente.</span>' +
      '<button type="button" class="btn" data-especial="rastreio-visto">Marcar como visto</button></div>' : '';
    row.innerHTML =
      '<div class="pedido-top"><h2 class="pedido-numero">Pedido #' + escapeHtml(p.numero_exibicao) + '</h2><span class="pill ' + st[0] + '">' + escapeHtml(st[1]) + '</span></div>' + alerta +
      '<div class="pedido-destaque"><span class="pedido-valor">' + fmtValor(p.valor_total) + '</span>' +
      '<span class="pedido-cliente">' + icone('pessoa') + (contato || 'sem contato') + '</span>' +
      '<button type="button" class="btn contorno" data-especial="ver-itens">' + qtd + ' ' + (qtd === 1 ? 'item' : 'itens') + '</button></div>' +
      '<div class="pedido-meta">' + fmtData(p.criado_em) + '</div>' + endereco + '<div class="pedido-acoes">' + acoes.join('') + '</div>';
    row.querySelectorAll('[data-acao]').forEach(function (b) { b.addEventListener('click', function () { executarAcao(p.id, b.dataset.acao, b); }); });
    var por = function (nome, fn) { row.querySelectorAll('[data-especial="' + nome + '"]').forEach(function (b) { b.addEventListener('click', function () { fn(b); }); }); };
    por('avancar-rastreio', function (b) { avancarRastreio(p.id, b); });
    por('salvar-endereco', function (b) { salvarEndereco(p.id, b, row); });
    por('ver-itens', function () { abrirItens(p); });
    por('rastreio-visto', function (b) { marcarRastreioVisto(p.id, b, row); });
    return row;
  }

  function abrirItens(p) {
    $('modal-itens-titulo').textContent = 'Pedido #' + p.numero_exibicao;
    $('modal-itens-lista').innerHTML = (p.itens || []).map(function (i) {
      return '<div class="item-linha"><span class="item-nome">' + escapeHtml(i.item_nome) + '</span><span class="item-valor">' + fmtValor(i.valor) + '</span></div>';
    }).join('') + '<div class="itens-total"><span>Total</span><span>' + fmtValor(p.valor_total) + '</span></div>';
    $('modal-itens').showModal();
  }

  async function chamar(url, opcoes, erroPadrao) {
    var resp = await fetch(url, opcoes);
    var data = await resp.json().catch(function () { return {}; });
    if (resp.status === 401) { window.location.href = '/painel/login'; throw new Error('Sessão expirada.'); }
    if (!resp.ok) throw new Error(data.detail || erroPadrao);
    return data;
  }

  async function marcarRastreioVisto(id, btn, row) {
    btn.disabled = true;
    try {
      await chamar('/painel/api/pedidos/' + id + '/rastreio-visto', { method: 'POST' }, 'Não consegui marcar como visto.');
      var a = row.querySelector('.pedido-alerta-rastreio'); if (a) a.remove();
      atualizarBannerRastreio();
    } catch (err) { window.avisar(err.message); btn.disabled = false; }
  }

  // Faixa no topo somando os pedidos (dentre os carregados) com alerta de rastreio pendente.
  function atualizarBannerRastreio() {
    var n = Array.from(pedidosPorId.values()).filter(function (r) { return r.querySelector('.pedido-alerta-rastreio'); }).length;
    var banner = $('banner-rastreio');
    if (!n) { banner.hidden = true; return; }
    $('banner-rastreio-texto').textContent = n === 1 ? '1 cliente perguntou o rastreio e está esperando resposta manual.' : n + ' clientes perguntaram o rastreio e estão esperando resposta manual.';
    banner.hidden = false;
  }

  // Um só caminho de inserção (paginação e poll): evita linha duplicada quando o mesmo pedido chega pelos dois.
  function upsertPedido(p, opcoes) {
    var prepend = opcoes && opcoes.prepend;
    var existente = pedidosPorId.get(p.id), assinatura = JSON.stringify(p);
    var destacar = existente ? existente.dataset.assinatura !== assinatura : !!prepend;
    var row = renderPedido(p); row.dataset.assinatura = assinatura;
    if (existente) existente.replaceWith(row);
    else { var lista = $('pedidos-lista'); if (prepend && lista.firstChild) lista.insertBefore(row, lista.firstChild); else lista.appendChild(row); }
    pedidosPorId.set(p.id, row);
    if (destacar) row.classList.add('flash');
    return row;
  }

  async function salvarEndereco(id, btn, row) {
    var input = row.querySelector('.pedido-endereco-input');
    var endereco = (input.value || '').trim();
    if (!endereco) { window.avisar('Digite o endereço.'); input.focus(); return; }
    btn.disabled = true;
    try {
      await chamar('/painel/api/pedidos/' + id + '/endereco', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endereco: endereco }) }, 'Não consegui salvar o endereço.');
      btn.textContent = 'Salvo';
      setTimeout(function () { btn.textContent = 'Salvar'; }, 2000);
    } catch (err) { window.avisar(err.message); } finally { btn.disabled = false; }
  }

  async function avancarRastreio(id, btn) {
    btn.disabled = true;
    try { await chamar('/painel/api/pedidos/' + id + '/avancar-rastreio', { method: 'POST' }, 'Não consegui avançar o rastreio.'); recarregar(); }
    catch (err) { window.avisar(err.message); btn.disabled = false; }
  }

  async function executarAcao(id, acao, btn) {
    btn.disabled = true;
    try {
      await chamar('/painel/api/pedidos/' + id + '/' + acao, { method: 'POST' }, 'Não consegui atualizar o pedido.');
      recarregar();   // marcar pago/cancelar age no lote inteiro (carrinho com vários itens) e pode afetar outras linhas visíveis
    } catch (err) { window.avisar(err.message); btn.disabled = false; }
  }

  function recarregar() { offset = 0; acabou = false; pedidosPorId.clear(); $('pedidos-lista').innerHTML = ''; carregarMais(); }

  function marcarAtualizado() {
    $('atualizar-info').textContent = 'Atualizado às ' + new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  async function carregarMais() {
    if (carregando || acabou) return;
    carregando = true;
    var carregandoEl = $('pedidos-carregando'), erroEl = $('pedidos-erro');
    carregandoEl.hidden = false; erroEl.hidden = true;
    try {
      var resp = await fetch('/painel/api/pedidos?offset=' + offset);
      var data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Não consegui buscar os pedidos.');
      data.pedidos.forEach(function (p) { upsertPedido(p); });
      offset += data.pedidos.length; acabou = !data.tem_mais;
      $('pedidos-vazio').hidden = pedidosPorId.size > 0;
      marcarAtualizado(); atualizarBannerRastreio();
    } catch (err) { erroEl.textContent = err.message; erroEl.hidden = false; }
    finally { carregando = false; carregandoEl.hidden = acabou; }
  }

  // Poll leve (só a 1ª página) para pegar pedido novo ou mudança de status/endereço sem recarregar; não mexe em offset/acabou.
  async function verificarAtualizacoes() {
    if (verificando || carregando) return;
    verificando = true;
    try {
      var resp = await fetch('/painel/api/pedidos?offset=0');
      if (!resp.ok) return;
      var data = await resp.json();
      data.pedidos.slice().reverse().forEach(function (p) { upsertPedido(p, { prepend: !pedidosPorId.has(p.id) }); });
      if (data.pedidos.length) $('pedidos-vazio').hidden = true;
      marcarAtualizado(); atualizarBannerRastreio();
    } catch (err) { /* poll de fundo não incomoda o operador */ }
    finally { verificando = false; }
  }

  $('btn-atualizar').addEventListener('click', verificarAtualizacoes);
  new IntersectionObserver(function (entries) { if (entries[0].isIntersecting) carregarMais(); }).observe($('carregar-sentinela'));
  carregarMais();
  setInterval(verificarAtualizacoes, 15000);
})();
