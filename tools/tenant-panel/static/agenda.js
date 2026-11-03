/* Agenda (/agenda): semana por semana. Rotas: GET /painel/api/agenda/eventos?semana=N e POST /painel/api/agenda/confirmar. */
(function () {
  'use strict';
  var DIAS = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'];
  var STATUS = {
    aguardando_confirmacao: ['aviso', 'Aguardando confirmação'],
    confirmado: ['ok', 'Confirmado'],
    recusado: ['erro', 'Pediu remarcação']
  };
  var semanaOffset = 0;
  var fmtDiaSP = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' });
  var $ = function (id) { return document.getElementById(id); };

  function chave(d) {   // data local como AAAA-MM-DD, sem passar por UTC (toISOString deslocava o dia à noite)
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }
  function dm(d) { return String(d.getDate()).padStart(2, '0') + '/' + String(d.getMonth() + 1).padStart(2, '0'); }

  async function enviarConfirmacao(id, btn) {
    btn.disabled = true; btn.textContent = 'Enviando…';
    try {
      var resp = await fetch('/painel/api/agenda/confirmar', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agendamento_id: id })
      });
      var data = await resp.json().catch(function () { return {}; });
      if (!resp.ok) throw new Error(data.detail || 'Não consegui mandar a confirmação.');
      carregarSemana();
    } catch (err) {
      window.avisar(err.message);
      btn.disabled = false; btn.textContent = 'Confirmar com o cliente';
    }
  }

  function eventoEl(ev) {
    var art = document.createElement('article');
    art.className = 'evento';
    var hora = document.createElement('span');
    hora.className = 'evento-hora';
    hora.textContent = new Date(ev.inicio).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
    var titulo = document.createElement('h3');
    titulo.className = 'evento-titulo'; titulo.textContent = ev.titulo || '(sem título)';
    art.appendChild(hora); art.appendChild(titulo);
    var acoes = document.createElement('div'); acoes.className = 'evento-acoes';
    var info = STATUS[ev.status];
    if (info) {
      var pill = document.createElement('span'); pill.className = 'pill ' + info[0]; pill.textContent = info[1]; acoes.appendChild(pill);
    } else if (ev.status === 'agendado' && ev.tem_contato && ev.id) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'btn contorno'; b.textContent = 'Confirmar com o cliente';
      b.addEventListener('click', function () { enviarConfirmacao(ev.id, b); });
      acoes.appendChild(b);
    }
    if (ev.link_evento) {     // link e botão são irmãos: antes o botão ficava dentro do <a>
      var a = document.createElement('a');
      a.className = 'evento-link'; a.href = ev.link_evento; a.target = '_blank'; a.rel = 'noopener';
      a.appendChild(document.createTextNode('Abrir na Google Agenda'));
      var so = document.createElement('span'); so.className = 'so-leitor'; so.textContent = ' (abre em outra aba)'; a.appendChild(so);
      acoes.appendChild(a);
    }
    if (acoes.childNodes.length) art.appendChild(acoes);
    return art;
  }

  async function carregarSemana() {
    var grid = $('week-grid'), erro = $('agenda-erro'), carregando = $('agenda-carregando');
    erro.hidden = true; carregando.hidden = false; grid.setAttribute('aria-busy', 'true'); grid.innerHTML = '';
    try {
      var resp = await fetch('/painel/api/agenda/eventos?semana=' + semanaOffset);
      var data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Não consegui buscar os eventos.');
      var inicio = new Date(data.semana_inicio + 'T00:00:00'), fim = new Date(data.semana_fim + 'T00:00:00');
      $('week-label').textContent = dm(inicio) + ' a ' + dm(fim) + '/' + fim.getFullYear();
      var hoje = fmtDiaSP.format(new Date());
      var porDia = {}, ordem = [];
      for (var i = 0; i < 7; i++) { var d = new Date(inicio); d.setDate(d.getDate() + i); var k = chave(d); porDia[k] = []; ordem.push([k, d]); }
      // o servidor manda o horário em UTC (ex.: 22h em Brasília chega como 01h do dia seguinte): agrupa pelo dia em São Paulo, não pelo texto
      (data.eventos || []).forEach(function (ev) { if (!ev.inicio) return; var dia = fmtDiaSP.format(new Date(ev.inicio)); if (porDia[dia]) porDia[dia].push(ev); });
      ordem.forEach(function (par, idx) {
        var k = par[0], d = par[1];
        var col = document.createElement('section');
        col.className = 'dia' + (k === hoje ? ' hoje' : '');
        var cab = document.createElement('h3');
        cab.className = 'dia-cab';
        cab.innerHTML = DIAS[idx] + '<span class="num">' + d.getDate() + '</span>';
        if (k === hoje) { var so = document.createElement('span'); so.className = 'so-leitor'; so.textContent = ' (hoje)'; cab.appendChild(so); }
        col.appendChild(cab);
        var lista = document.createElement('div'); lista.className = 'dia-eventos';
        var eventos = porDia[k].sort(function (a, b) { return new Date(a.inicio) - new Date(b.inicio); });
        if (!eventos.length) {
          var vazio = document.createElement('p'); vazio.className = 'dia-vazio'; vazio.textContent = 'Sem compromissos'; lista.appendChild(vazio);
        } else { eventos.forEach(function (ev) { lista.appendChild(eventoEl(ev)); }); }
        col.appendChild(lista); grid.appendChild(col);
      });
    } catch (err) {
      erro.textContent = err.message; erro.hidden = false;
    } finally {
      carregando.hidden = true; grid.removeAttribute('aria-busy');
    }
  }

  $('week-prev').addEventListener('click', function () { semanaOffset -= 1; carregarSemana(); });
  $('week-next').addEventListener('click', function () { semanaOffset += 1; carregarSemana(); });
  $('week-hoje').addEventListener('click', function () { semanaOffset = 0; carregarSemana(); });
  carregarSemana();
})();
