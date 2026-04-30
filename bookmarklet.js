/* ============================================================
   Robô Dados Básicos · Migração de Setor · AGU/Sapiens
   Desenvolvido por Igor Farias
   ============================================================ */
(function () {
  'use strict';

  // ── Evita abrir duas vezes ──────────────────────────────────────
  const OVERLAY_ID = '_rdb_overlay';
  if (document.getElementById(OVERLAY_ID)) {
    document.getElementById(OVERLAY_ID).style.display = 'flex';
    return;
  }

  // ── Constantes ─────────────────────────────────────────────────
  const BASE            = 'https://supersapiensbackend.agu.gov.br';
  const DEFAULT_SETOR   = 62140;
  const DEBOUNCE_MS     = 350;

  const ESCALARES = new Set([
    'NUP','alterarChave','dadosRequerimento','dataHoraAbertura',
    'dataHoraDesarquivamento','dataHoraPrazoResposta','descricao',
    'emTramitacaoExterna','hasFundamentacaoRestricao','lembreteArquivista',
    'localizador','nupInvalido','outroNumero','processoOrigem',
    'processoOrigemIncluirDocumentos','protocoloEletronico','requerimento',
    'semValorEconomico','temProcessoOrigem','titulo','unidadeArquivistica',
    'validaNup','valorEconomico','visibilidadeExterna',
  ]);
  const RELACIONAIS = new Set([
    'classificacao','configuracaoNup','especieProcesso','modalidadeFase',
    'modalidadeMeio','procedencia','setorAtual','setorInicial','tipoProtocolo',
  ]);

  // ── Token ───────────────────────────────────────────────────────
  function getToken() {
    const fns = [
      () => localStorage.getItem('token'),  // Novo Sapiens (token direto)
      () => localStorage.getItem('accessToken'),
      () => JSON.parse(localStorage.getItem('auth')).accessToken,
      () => JSON.parse(localStorage.getItem('token')).accessToken,
      () => {
        for (let i = 0; i < localStorage.length; i++) {
          const v = localStorage.getItem(localStorage.key(i));
          if (v && typeof v === 'string' && v.startsWith('eyJ')) return v;
          try {
            const o = JSON.parse(v);
            if (o && o.accessToken && o.accessToken.startsWith('eyJ')) return o.accessToken;
          } catch (_) {}
        }
        return null;
      },
    ];
    for (const fn of fns) {
      try { const t = fn(); if (t && typeof t === 'string' && t.startsWith('eyJ')) return t; }
      catch (_) {}
    }
    return null;
  }

  function decodeJWT(tok) {
    try {
      const b64 = tok.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      return JSON.parse(atob(b64));
    } catch (_) { return {}; }
  }

  function getUserId(tok) {
    const p = decodeJWT(tok);
    return String(p.id || p.usuarioId || p.userId || '').trim() || null;
  }

  // ── API ─────────────────────────────────────────────────────────
  function hdrs(tok) {
    return {
      'Authorization':  'Bearer ' + tok,
      'Accept':         'application/json',
      'Content-Type':   'application/json',
    };
  }

  function apiFetch(tok, path) {
    return fetch(BASE + path, { headers: hdrs(tok) }).then(r => {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function apiPatch(tok, path, body) {
    return fetch(BASE + path, {
      method:  'PATCH',
      headers: hdrs(tok),
      body:    JSON.stringify(body),
    });
  }

  // ── Estilos ─────────────────────────────────────────────────────
  const CSS = `
#_rdb_overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;
  align-items:center;justify-content:center;z-index:2147483647;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
#_rdb_modal{background:#1a1a2e;color:#e0e0e0;border-radius:12px;width:520px;
  max-width:96vw;max-height:90vh;display:flex;flex-direction:column;
  overflow:hidden;box-shadow:0 24px 64px rgba(0,0,0,.8)}
#_rdb_header{background:#16213e;padding:16px 20px;display:flex;
  align-items:center;justify-content:space-between;border-bottom:1px solid #0f3460}
#_rdb_header h2{margin:0;font-size:15px;color:#e94560;letter-spacing:.3px}
#_rdb_close{background:none;border:none;color:#888;font-size:20px;
  cursor:pointer;padding:0 6px;line-height:1}
#_rdb_close:hover{color:#e94560}
#_rdb_body{padding:20px;overflow-y:auto;flex:1}
._rdb_label{font-size:12px;color:#aaa;margin-bottom:4px;display:block}
._rdb_group{margin-bottom:14px;position:relative}
._rdb_inp{width:100%;box-sizing:border-box;background:#0f3460;
  border:1px solid #1a4a8a;color:#e0e0e0;border-radius:6px;
  padding:8px 10px;font-size:13px;outline:none}
._rdb_inp:focus{border-color:#e94560}
._rdb_inp:disabled{opacity:.4;cursor:not-allowed}
._rdb_drop{position:absolute;left:0;right:0;top:calc(100% + 2px);
  background:#16213e;border:1px solid #0f3460;border-radius:6px;
  max-height:220px;overflow-y:auto;z-index:10;display:none}
._rdb_drop.open{display:block}
._rdb_item{padding:9px 12px;cursor:pointer;border-bottom:1px solid #0f3460}
._rdb_item:last-child{border-bottom:none}
._rdb_item:hover,._rdb_item.active{background:#0f3460}
._rdb_sigla{font-weight:700;color:#e94560;font-size:12px}
._rdb_nome{color:#e0e0e0;font-size:13px;margin-top:2px}
._rdb_uni{color:#888;font-size:11px;margin-top:2px}
._rdb_empty{padding:10px 12px;color:#888;font-size:13px}
#_rdb_btn{width:100%;padding:11px;background:#e94560;color:#fff;
  border:none;border-radius:6px;font-size:14px;font-weight:700;
  cursor:pointer;margin-top:6px}
#_rdb_btn:hover:not(:disabled){background:#c73652}
#_rdb_btn:disabled{opacity:.5;cursor:not-allowed}
#_rdb_dry_wrap{display:flex;align-items:center;gap:6px;margin-top:10px}
#_rdb_dry_wrap label{font-size:12px;color:#888;cursor:pointer}
#_rdb_log{background:#0a0a1a;border-radius:6px;padding:10px;
  font-family:monospace;font-size:12px;color:#ccc;max-height:200px;
  overflow-y:auto;display:none;margin-top:14px;white-space:pre-wrap;word-break:break-all}
#_rdb_cnt_wrap{display:none;gap:10px;margin-top:10px}
._rdb_cnt{flex:1;background:#0f3460;border-radius:6px;padding:8px;text-align:center}
._rdb_cnt strong{display:block;font-size:20px}
._rdb_cnt span{font-size:11px;color:#888}
#_rdb_footer{font-size:10px;color:#444;text-align:center;padding:10px 0 0}
`;

  // ── HTML ────────────────────────────────────────────────────────
  const HTML = `
<div id="_rdb_overlay">
  <div id="_rdb_modal">
    <div id="_rdb_header">
      <h2>🔄 Migração de Setor · Sapiens AGU</h2>
      <button id="_rdb_close" title="Fechar">✕</button>
    </div>
    <div id="_rdb_body">

      <div class="_rdb_group">
        <label class="_rdb_label">Unidade</label>
        <input class="_rdb_inp" id="_rdb_q_uni" placeholder="Pesquisar unidade..." autocomplete="off">
        <input type="hidden" id="_rdb_id_uni">
        <div class="_rdb_drop" id="_rdb_drop_uni"></div>
      </div>

      <div class="_rdb_group">
        <label class="_rdb_label">Setor Destino</label>
        <input class="_rdb_inp" id="_rdb_q_set" placeholder="Selecione a unidade primeiro..." autocomplete="off" disabled>
        <input type="hidden" id="_rdb_id_set">
        <div class="_rdb_drop" id="_rdb_drop_set"></div>
      </div>

      <div class="_rdb_group">
        <label class="_rdb_label">Pasta</label>
        <input class="_rdb_inp" id="_rdb_q_pas" placeholder="Pesquisar pasta..." autocomplete="off">
        <input type="hidden" id="_rdb_id_pas">
        <div class="_rdb_drop" id="_rdb_drop_pas"></div>
      </div>

      <button id="_rdb_btn" disabled>Conectando...</button>

      <div id="_rdb_dry_wrap">
        <input type="checkbox" id="_rdb_dryrun">
        <label for="_rdb_dryrun">Dry run (simular sem alterar)</label>
      </div>

      <div id="_rdb_log"></div>

      <div id="_rdb_cnt_wrap">
        <div class="_rdb_cnt"><strong id="_rdb_c_alt">0</strong><span>Alterados</span></div>
        <div class="_rdb_cnt"><strong id="_rdb_c_pul">0</strong><span>Pulados</span></div>
        <div class="_rdb_cnt"><strong id="_rdb_c_fal">0</strong><span>Falhas</span></div>
      </div>

      <div id="_rdb_footer">Projeto em andamento · Desenvolvimento: Igor Farias</div>
    </div>
  </div>
</div>
`;

  // ── Injetar ─────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  const tmp = document.createElement('div');
  tmp.innerHTML = HTML;
  document.body.appendChild(tmp.firstElementChild);

  // ── Referências ─────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  const overlay  = $('_rdb_overlay');
  const btn      = $('_rdb_btn');
  const logEl    = $('_rdb_log');
  const cntWrap  = $('_rdb_cnt_wrap');
  const qUni = $('_rdb_q_uni'), idUni = $('_rdb_id_uni'), dropUni = $('_rdb_drop_uni');
  const qSet = $('_rdb_q_set'), idSet = $('_rdb_id_set'), dropSet = $('_rdb_drop_set');
  const qPas = $('_rdb_q_pas'), idPas = $('_rdb_id_pas'), dropPas = $('_rdb_drop_pas');

  // ── Estado ──────────────────────────────────────────────────────
  let TOKEN   = null;
  let USER_ID = null;
  let running = false;

  // ── Helpers ─────────────────────────────────────────────────────
  function log(msg) {
    logEl.style.display = 'block';
    logEl.textContent += msg + '\n';
    logEl.scrollTop = logEl.scrollHeight;
  }

  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

  // ── Autocomplete factory ────────────────────────────────────────
  function makeAC(qEl, idEl, dropEl, fetchFn, renderFn, onSelect) {
    let active = -1;

    const close = () => { dropEl.classList.remove('open'); active = -1; };
    const open  = () => { dropEl.classList.add('open'); };

    qEl.addEventListener('focus', () => { if (dropEl.children.length) open(); });
    qEl.addEventListener('blur',  () => setTimeout(close, 160));

    qEl.addEventListener('keydown', e => {
      const items = [...dropEl.querySelectorAll('._rdb_item')];
      if      (e.key === 'ArrowDown')  active = Math.min(active + 1, items.length - 1);
      else if (e.key === 'ArrowUp')    active = Math.max(active - 1, 0);
      else if (e.key === 'Enter' && active >= 0) { items[active].click(); return; }
      else if (e.key === 'Escape')     { close(); return; }
      items.forEach((el, i) => el.classList.toggle('active', i === active));
    });

    const doSearch = debounce(async q => {
      dropEl.innerHTML = '<div class="_rdb_empty">Buscando...</div>';
      open();
      try {
        const results = await fetchFn(q);
        if (!results.length) {
          dropEl.innerHTML = '<div class="_rdb_empty">Nenhum resultado.</div>';
          return;
        }
        dropEl.innerHTML = '';
        results.forEach(item => {
          const div = document.createElement('div');
          div.className = '_rdb_item';
          div.innerHTML = renderFn(item);
          div.addEventListener('mousedown', e => e.preventDefault());
          div.addEventListener('click', () => { onSelect(item); close(); });
          dropEl.appendChild(div);
        });
      } catch (e) {
        dropEl.innerHTML = '<div class="_rdb_empty">Erro: ' + e.message + '</div>';
      }
    }, DEBOUNCE_MS);

    qEl.addEventListener('input', () => { idEl.value = ''; doSearch(qEl.value); });
    return { close, search: doSearch };
  }

  // ── Fetch functions ─────────────────────────────────────────────
  async function fetchUnidades(q) {
    const where = encodeURIComponent(JSON.stringify({ nome: 'like:%' + q + '%' }));
    const pop   = encodeURIComponent(JSON.stringify(['unidade']));
    const data  = await apiFetch(TOKEN, `/v1/administrativo/setor?where=${where}&populate=${pop}&limit=200`);
    const entities = data.entities || [];
    const map = new Map();
    entities.forEach(s => {
      const u = s.unidade;
      if (u && u.id && !map.has(u.id)) map.set(u.id, u);
    });
    return [...map.values()].sort((a, b) => (a.nome || '').localeCompare(b.nome || ''));
  }

  async function fetchSetores(q, unidadeId) {
    const where = { nome: 'like:%' + q + '%' };
    if (unidadeId) where['unidade.id'] = 'eq:' + unidadeId;
    const pop  = encodeURIComponent(JSON.stringify(['unidade']));
    const data = await apiFetch(TOKEN,
      `/v1/administrativo/setor?where=${encodeURIComponent(JSON.stringify(where))}&populate=${pop}&limit=50`
    );
    return data.entities || [];
  }

  async function fetchPastas(q) {
    const where = { 'criadoPor.id': 'eq:' + USER_ID, nome: 'like:%' + q + '%' };
    const data  = await apiFetch(TOKEN,
      `/v1/administrativo/folder?where=${encodeURIComponent(JSON.stringify(where))}&limit=50`
    );
    return data.entities || [];
  }

  // ── Autocomplete wiring ─────────────────────────────────────────
  makeAC(qUni, idUni, dropUni,
    fetchUnidades,
    u => `<div class="_rdb_sigla">${u.sigla || ''}</div>
          <div class="_rdb_nome">${u.nome || ''}</div>`,
    u => {
      idUni.value = u.id;
      qUni.value  = (u.sigla ? u.sigla + ' — ' : '') + (u.nome || '');
      idSet.value = ''; qSet.value = '';
      qSet.placeholder = 'Pesquisar setor...';
      qSet.disabled = false;
      dropSet.innerHTML = '';
    }
  );

  makeAC(qSet, idSet, dropSet,
    q => fetchSetores(q, idUni.value || null),
    s => `<div class="_rdb_sigla">${s.sigla || ''}</div>
          <div class="_rdb_nome">${s.nome || ''}</div>
          <div class="_rdb_uni">${s.unidade ? (s.unidade.sigla || '') + ' — ' + (s.unidade.nome || '') : ''}</div>`,
    s => {
      idSet.value = s.id;
      qSet.value  = (s.sigla ? s.sigla + ' — ' : '') + (s.nome || '');
    }
  );

  makeAC(qPas, idPas, dropPas,
    fetchPastas,
    p => `<div class="_rdb_nome">${p.nome || p.descricao || 'Pasta ' + p.id}</div>`,
    p => {
      idPas.value = p.id;
      qPas.value  = p.nome || p.descricao || 'Pasta ' + p.id;
    }
  );

  // ── Defaults ────────────────────────────────────────────────────
  async function loadDefaults() {
    try {
      const pop  = encodeURIComponent(JSON.stringify(['unidade']));
      const data = await apiFetch(TOKEN, `/v1/administrativo/setor/${DEFAULT_SETOR}?populate=${pop}`);
      const s = data.entity || data;
      const u = s.unidade || {};
      idSet.value = s.id;
      qSet.value  = (s.sigla ? s.sigla + ' — ' : '') + (s.nome || '');
      idUni.value = u.id || '';
      qUni.value  = (u.sigla ? u.sigla + ' — ' : '') + (u.nome || '');
      qSet.disabled = false;
      qSet.placeholder = 'Pesquisar setor...';
    } catch (e) {
      log('⚠️  Defaults: ' + e.message);
    }
  }

  // ── Init ────────────────────────────────────────────────────────
  async function init() {
    TOKEN = getToken();
    if (!TOKEN) {
      btn.textContent = 'Faça login no Sapiens primeiro';
      return;
    }
    USER_ID = getUserId(TOKEN);
    if (!USER_ID) {
      try {
        const d = await apiFetch(TOKEN, '/v1/usuario/me');
        USER_ID = String(d.id || '').trim() || null;
      } catch (_) {}
    }
    if (!USER_ID) {
      btn.textContent = 'Erro: usuário não identificado';
      return;
    }
    await loadDefaults();
    btn.disabled = false;
    btn.textContent = 'Executar Migração';
  }

  // ── Payload ─────────────────────────────────────────────────────
  function montarPayload(dados, setorId) {
    const p = {};
    ESCALARES.forEach(c => { if (c in dados) p[c] = dados[c]; });
    RELACIONAIS.forEach(c => {
      if (c in dados) {
        const v = dados[c];
        p[c] = (v && typeof v === 'object') ? v.id : v;
      }
    });
    p.setorAtual = setorId;
    return p;
  }

  // ── Migração ────────────────────────────────────────────────────
  async function runMigration() {
    const setorId = parseInt(idSet.value);
    const pastaId = parseInt(idPas.value);
    const dryRun  = $('_rdb_dryrun').checked;

    if (!setorId) { alert('Selecione o setor destino.'); return; }
    if (!pastaId) { alert('Selecione a pasta.'); return; }

    running = true;
    btn.disabled = true;
    btn.textContent = 'Executando...';
    logEl.style.display = 'block';
    logEl.textContent = '';
    cntWrap.style.display = 'flex';
    const cnt = { alt: 0, pul: 0, fal: 0 };

    const modo = dryRun ? '🟡 DRY RUN' : '🔴 MODO REAL';
    log('═'.repeat(44));
    log('  ' + modo + '  ·  Pasta ' + pastaId + '  ·  Setor → ' + setorId);
    log('═'.repeat(44));

    // Buscar tarefas (paginado)
    const filtro = JSON.stringify({
      'usuarioResponsavel.id': 'eq:' + USER_ID,
      'dataHoraConclusaoPrazo': 'isNull',
      'folder.id': 'eq:' + pastaId,
    });
    const pop = encodeURIComponent(JSON.stringify(['processo']));
    let tarefas = [], offset = 0, limit = 50;
    try {
      while (true) {
        const data = await apiFetch(TOKEN,
          `/v1/administrativo/tarefa?where=${encodeURIComponent(filtro)}&populate=${pop}&limit=${limit}&offset=${offset}`
        );
        const lote = data.entities || [];
        tarefas.push(...lote);
        if (lote.length < limit) break;
        offset += limit;
      }
    } catch (e) {
      log('❌ Erro ao buscar tarefas: ' + e.message);
      btn.disabled = false;
      btn.textContent = 'Executar Migração';
      running = false;
      return;
    }

    log('\n  📋 ' + tarefas.length + ' tarefa(s) encontrada(s).\n');

    for (let i = 0; i < tarefas.length; i++) {
      const tarefa   = tarefas[i];
      const processo = tarefa.processo;
      const idx      = `[${i + 1}/${tarefas.length}]`;

      if (!processo || !processo.id) {
        log(idx + ' ⚠️  Sem processo vinculado (tarefa ' + tarefa.id + ')');
        cnt.pul++;
      } else {
        const pid = processo.id;
        const nup = processo.NUP || processo.nup || pid;
        try {
          const dadosResp = await apiFetch(TOKEN, `/v1/administrativo/processo/${pid}`);
          const dados = dadosResp.entity || dadosResp;
          const setorAtualId = dados.setorAtual
            ? (dados.setorAtual.id || dados.setorAtual)
            : null;

          if (String(setorAtualId) === String(setorId)) {
            log(idx + ' ⏭️  NUP ' + nup + ' — já no setor destino');
            cnt.pul++;
          } else if (dryRun) {
            log(idx + ' 🟡 NUP ' + nup + ' — seria alterado (dry run)');
            cnt.alt++;
          } else {
            const payload = montarPayload(dados, setorId);
            const r = await apiPatch(TOKEN, `/v1/administrativo/processo/${pid}`, payload);
            if (r.ok) {
              log(idx + ' ✅ NUP ' + nup + ' — alterado');
              cnt.alt++;
            } else {
              log(idx + ' ❌ NUP ' + nup + ' — falha HTTP ' + r.status);
              cnt.fal++;
            }
          }
        } catch (e) {
          log(idx + ' ❌ Exceção: ' + e.message);
          cnt.fal++;
        }
      }

      $('_rdb_c_alt').textContent = cnt.alt;
      $('_rdb_c_pul').textContent = cnt.pul;
      $('_rdb_c_fal').textContent = cnt.fal;

      await wait(400);
    }

    log('\n' + '═'.repeat(44));
    log('  ✔️  Concluído — Alt: ' + cnt.alt + ' | Pul: ' + cnt.pul + ' | Fal: ' + cnt.fal);
    log('═'.repeat(44));
    btn.disabled = false;
    btn.textContent = 'Executar Migração';
    running = false;
  }

  // ── Eventos ─────────────────────────────────────────────────────
  $('_rdb_close').addEventListener('click', () => { overlay.style.display = 'none'; });
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.style.display = 'none'; });
  btn.addEventListener('click', () => { if (!running) runMigration(); });

  // ── Start ────────────────────────────────────────────────────────
  init();
})();
