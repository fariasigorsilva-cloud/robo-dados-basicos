#!/usr/bin/env python3
"""
Dados Básicos — Migração de Setor · AGU
Launcher web standalone, compatível com macOS e Windows.
Sem terminal, sem código visível ao usuário.
"""

from __future__ import annotations

import base64
import json
import platform
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

PORT        = 7772
PYTHON      = sys.executable
BASE_URL    = "https://supersapiensbackend.agu.gov.br"
SAPIENS_URL = "https://supersapiens.agu.gov.br"
PORTA_DEBUG = 9222
USER_DATA_DIR = (
    "/tmp/sapiens_dados_basicos"
    if platform.system() == "Darwin"
    else str(Path.home() / "AppData" / "Local" / "Temp" / "sapiens_dados_basicos")
)

_CAMPOS_ESCALARES = {
    "NUP", "alterarChave", "dadosRequerimento", "dataHoraAbertura",
    "dataHoraDesarquivamento", "dataHoraPrazoResposta", "descricao",
    "emTramitacaoExterna", "hasFundamentacaoRestricao", "lembreteArquivista",
    "localizador", "nupInvalido", "outroNumero", "processoOrigem",
    "processoOrigemIncluirDocumentos", "protocoloEletronico", "requerimento",
    "semValorEconomico", "temProcessoOrigem", "titulo", "unidadeArquivistica",
    "validaNup", "valorEconomico", "visibilidadeExterna",
}
_CAMPOS_RELACIONAIS = {
    "classificacao", "configuracaoNup", "especieProcesso", "modalidadeFase",
    "modalidadeMeio", "procedencia", "setorAtual", "setorInicial", "tipoProtocolo",
}

_TOKEN_SCRIPTS = [
    "return localStorage.getItem('accessToken');",
    "try { return JSON.parse(localStorage.getItem('auth')).accessToken; } catch(e){ return null; }",
    "try { return JSON.parse(localStorage.getItem('token')).accessToken; } catch(e){ return null; }",
    """
    try {
        for (let i = 0; i < localStorage.length; i++) {
            let key = localStorage.key(i);
            let val = localStorage.getItem(key);
            if (!val) continue;
            if (val.startsWith('eyJ')) return val;
            try {
                let obj = JSON.parse(val);
                if (obj && obj.accessToken && obj.accessToken.startsWith('eyJ'))
                    return obj.accessToken;
                if (obj && obj.token && typeof obj.token === 'string' && obj.token.startsWith('eyJ'))
                    return obj.token;
            } catch(e) {}
        }
        return null;
    } catch(e) { return null; }
    """,
]

# ── Estado global ─────────────────────────────────────────────
_estado = {
    "fase":         "idle",   # idle | capturando | conectado | executando | concluido | erro
    "token":        None,
    "id_usuario":   None,
    "nome_usuario": None,
    "log":          [],
    "contadores":   {"alterados": 0, "pulados": 0, "falhas": 0},
    "erro":         None,
}
_driver = None
_lock   = threading.Lock()

def _log(msg: str):
    with _lock:
        _estado["log"].append(msg)

# ── Utilitários ───────────────────────────────────────────────

def chrome_executavel() -> str:
    if platform.system() == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    for c in [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
    ]:
        if Path(c).exists():
            return c
    return "chrome"

def _capturar_token_driver(driver):
    try:
        from selenium.common.exceptions import WebDriverException
    except ImportError:
        return None
    for script in _TOKEN_SCRIPTS:
        try:
            t = driver.execute_script(script)
            if t and isinstance(t, str) and t.startswith("eyJ"):
                return t
        except WebDriverException:
            continue
    return None

def _decodificar_jwt(token: str) -> dict:
    try:
        partes = token.split(".")
        if len(partes) < 2:
            return {}
        b64 = partes[1] + "=" * (4 - len(partes[1]) % 4)
        return json.loads(base64.b64decode(b64))
    except Exception:
        return {}

def _descobrir_usuario(token: str):
    import requests as req
    payload = _decodificar_jwt(token)
    id_u = (
        str(payload.get("id") or "").strip()
        or str(payload.get("usuarioId") or "").strip()
        or str(payload.get("userId") or "").strip()
        or None
    )
    nome = payload.get("nome") or payload.get("name") or payload.get("username") or None
    if id_u:
        return id_u, nome
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    for ep in ["/v1/usuario/me", "/v1/usuario/perfil", "/v1/perfil"]:
        try:
            r = req.get(f"{BASE_URL}{ep}", headers=headers, timeout=10)
            if r.status_code == 200:
                d = r.json()
                id_api = str(d.get("id") or "").strip() or None
                if id_api:
                    return id_api, d.get("nome") or d.get("name") or d.get("username")
        except Exception:
            continue
    return None, None

def _extrair_id(valor):
    if valor is None:
        return None
    if isinstance(valor, dict):
        return valor.get("id")
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None

def _montar_payload(dados: dict, setor: int) -> dict:
    p = {}
    for c in _CAMPOS_ESCALARES:
        if c in dados:
            p[c] = dados[c]
    for c in _CAMPOS_RELACIONAIS:
        if c in dados:
            p[c] = _extrair_id(dados[c])
    p["setorAtual"] = setor
    return p

# ── Threads ───────────────────────────────────────────────────

def _thread_abrir_chrome():
    try:
        subprocess.Popen([
            chrome_executavel(),
            f"--remote-debugging-port={PORTA_DEBUG}",
            f"--user-data-dir={USER_DATA_DIR}",
            SAPIENS_URL,
        ])
        with _lock:
            _estado["fase"] = "chrome_aberto"
    except Exception as e:
        with _lock:
            _estado["fase"] = "erro"
            _estado["erro"] = f"Falha ao abrir Chrome: {e}"

def _thread_capturar_token():
    global _driver
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{PORTA_DEBUG}")
        with _lock:
            _estado["fase"] = "capturando"
        driver = webdriver.Chrome(options=opts)
        _driver = driver
        token = None
        for _ in range(6):
            token = _capturar_token_driver(driver)
            if token:
                break
            time.sleep(3)
        if not token:
            with _lock:
                _estado["fase"] = "erro"
                _estado["erro"] = "Token não encontrado. Verifique se está logado no Sapiens."
            return
        id_u, nome = _descobrir_usuario(token)
        if not id_u:
            with _lock:
                _estado["fase"] = "erro"
                _estado["erro"] = "ID do usuário não encontrado no token."
            return
        with _lock:
            _estado.update({"token": token, "id_usuario": id_u, "nome_usuario": nome, "fase": "conectado"})
    except Exception as e:
        with _lock:
            _estado["fase"] = "erro"
            _estado["erro"] = str(e)

def _thread_migracao(token, id_usuario, id_pasta, setor_destino, dry_run):
    import requests as req
    contadores = {"alterados": 0, "pulados": 0, "falhas": 0}
    with _lock:
        _estado.update({"fase": "executando", "log": [], "contadores": contadores.copy()})

    session = req.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    modo_txt = "🟡 DRY RUN" if dry_run else "🔴 MODO REAL"
    _log(f"{'═'*48}")
    _log(f"  {modo_txt}  ·  Pasta {id_pasta}  ·  Setor → {setor_destino}")
    _log(f"{'═'*48}")

    # Buscar tarefas com paginação
    filtro   = json.dumps({"usuarioResponsavel.id": f"eq:{id_usuario}", "dataHoraConclusaoPrazo": "isNull", "folder.id": f"eq:{id_pasta}"})
    populate = urllib.parse.quote(json.dumps(["processo"]))
    tarefas  = []
    offset, limit = 0, 50
    try:
        while True:
            url = (f"{BASE_URL}/v1/administrativo/tarefa"
                   f"?where={urllib.parse.quote(filtro)}&populate={populate}"
                   f"&limit={limit}&offset={offset}")
            r = session.get(url, timeout=30)
            if r.status_code != 200:
                _log(f"❌  Erro ao buscar tarefas: HTTP {r.status_code}")
                with _lock:
                    _estado["fase"] = "erro"
                    _estado["erro"] = f"HTTP {r.status_code}"
                return
            lote = r.json().get("entities", [])
            tarefas.extend(lote)
            if len(lote) < limit:
                break
            offset += limit
    except Exception as e:
        _log(f"❌  Exceção: {e}")
        with _lock:
            _estado["fase"] = "erro"
            _estado["erro"] = str(e)
        return

    total = len(tarefas)
    _log(f"\n  📋  {total} tarefa(s) encontrada(s).\n")

    for i, tarefa in enumerate(tarefas, 1):
        _processar_tarefa(session, tarefa, i, total, setor_destino, dry_run, contadores)
        with _lock:
            _estado["contadores"] = contadores.copy()
        time.sleep(0.4)

    _log(f"\n{'═'*48}")
    _log(f"  ✅ Concluído · {total} tarefa(s) processada(s).")
    _log(f"  Alterados: {contadores['alterados']}  Pulados: {contadores['pulados']}  Falhas: {contadores['falhas']}")
    _log(f"{'═'*48}")
    with _lock:
        _estado["fase"] = "concluido"
        _estado["contadores"] = contadores.copy()

def _processar_tarefa(session, tarefa, i, total, setor_destino, dry_run, contadores):
    try:
        proc_ref    = tarefa.get("processo") or {}
        at_id       = proc_ref.get("@id") or ""
        id_processo = proc_ref.get("id") or (at_id.split("/")[-1] if at_id else None)
        id_tarefa   = tarefa.get("id") or "?"

        if not id_processo:
            _log(f"  [{i}/{total}] ⚠️  Tarefa {id_tarefa} sem processo — pulando.")
            contadores["pulados"] += 1
            return

        _log(f"\n  [{i}/{total}] Tarefa {id_tarefa} · Processo {id_processo}")

        r = session.get(
            f"{BASE_URL}/v1/administrativo/processo/{id_processo}"
            f"?populate={urllib.parse.quote(json.dumps(['populateAll']))}",
            timeout=30
        )
        if r.status_code != 200:
            _log(f"  ❌ GET falhou: HTTP {r.status_code}")
            contadores["falhas"] += 1
            return

        dados       = r.json()
        nup         = dados.get("NUP") or dados.get("nup") or dados.get("numeroProcesso") or "?"
        setor_atual = _extrair_id(dados.get("setorAtual"))

        _log(f"  NUP: {nup}")
        _log(f"  Setor: {setor_atual}  →  {setor_destino}")

        if setor_atual and str(setor_atual) == str(setor_destino):
            _log("  ✅ Já está no setor destino — pulando.")
            contadores["pulados"] += 1
            return

        payload = _montar_payload(dados, setor_destino)

        if dry_run:
            _log(f"  🟡 [DRY RUN] Nada enviado.")
            contadores["pulados"] += 1
            return

        r2 = session.put(
            f"{BASE_URL}/v1/administrativo/processo/{id_processo}"
            f"?populate={urllib.parse.quote(json.dumps([]))}&context={urllib.parse.quote('{}')}",
            json=payload, timeout=30
        )
        if r2.status_code in (200, 201, 204):
            _log("  ✅ Alterado com sucesso.")
            contadores["alterados"] += 1
        else:
            _log(f"  ❌ Falha HTTP {r2.status_code}: {r2.text[:200]}")
            contadores["falhas"] += 1

    except Exception as e:
        _log(f"  ❌ Erro inesperado: {e}")
        contadores["falhas"] += 1

# ══════════════════════════════════════════════════════════════
#  HTML — Interface moderna
# ══════════════════════════════════════════════════════════════

HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dados Básicos · AGU</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg:       #07071a;
  --surf:     rgba(255,255,255,0.04);
  --surf2:    rgba(255,255,255,0.07);
  --border:   rgba(255,255,255,0.08);
  --bfocus:   rgba(139,92,246,0.45);
  --purple:   #8b5cf6;
  --cyan:     #22d3ee;
  --green:    #10b981;
  --red:      #f87171;
  --yellow:   #fbbf24;
  --text:     #f1f5f9;
  --muted:    #64748b;
  --subtle:   #94a3b8;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);
  min-height:100vh;padding:40px 16px 80px;
  background-image:
    radial-gradient(ellipse 90% 60% at 50% -10%, rgba(139,92,246,.18) 0%, transparent 70%),
    radial-gradient(ellipse 50% 40% at 85% 90%,  rgba(34,211,238,.07) 0%, transparent 60%);
}
.wrap{max-width:640px;margin:0 auto}

/* ── Header ── */
.hdr{text-align:center;margin-bottom:44px}
.pill{
  display:inline-flex;align-items:center;gap:7px;
  background:rgba(139,92,246,.1);border:1px solid rgba(139,92,246,.22);
  border-radius:100px;padding:5px 15px;font-size:11px;font-weight:600;
  color:#a78bfa;letter-spacing:.08em;text-transform:uppercase;margin-bottom:18px;
}
.pill-dot{
  width:7px;height:7px;border-radius:50%;background:var(--purple);
  box-shadow:0 0 8px var(--purple);animation:blink 2s infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
h1{font-size:34px;font-weight:700;letter-spacing:-.6px;line-height:1.1;margin-bottom:10px}
h1 em{font-style:normal;background:linear-gradient(120deg,#a78bfa 0%,#22d3ee 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{color:var(--muted);font-size:14px;line-height:1.5}

/* ── Cards ── */
.card{
  background:var(--surf);border:1px solid var(--border);border-radius:18px;
  padding:26px;margin-bottom:10px;transition:border-color .2s,background .2s;
  position:relative;overflow:hidden;
}
.card::before{
  content:'';position:absolute;inset:0;border-radius:inherit;
  background:linear-gradient(135deg,rgba(139,92,246,.06),transparent 60%);
  opacity:0;transition:opacity .3s;pointer-events:none;
}
.card:hover::before{opacity:1}
.card:hover{border-color:rgba(255,255,255,.13)}
.card.is-done{border-color:rgba(16,185,129,.3);background:rgba(16,185,129,.03)}
.card.is-active{border-color:rgba(139,92,246,.35);background:rgba(139,92,246,.04)}

.step-hdr{display:flex;align-items:center;gap:14px;margin-bottom:22px}
.step-num{
  width:30px;height:30px;border-radius:50%;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  font-size:13px;font-weight:700;transition:all .25s;
  background:var(--surf2);border:1px solid var(--border);color:var(--muted);
}
.step-num.done{background:var(--green);border-color:var(--green);color:#fff;font-size:15px}
.step-num.active{background:var(--purple);border-color:var(--purple);color:#fff}
.step-title{font-size:16px;font-weight:600}
.step-desc{font-size:13px;color:var(--muted);margin-top:2px}

/* ── Buttons ── */
.btn-row{display:flex;gap:10px;flex-wrap:wrap}
.btn{
  display:inline-flex;align-items:center;gap:8px;border:none;
  border-radius:11px;font-family:'Inter',sans-serif;font-size:13px;font-weight:600;
  cursor:pointer;transition:all .15s;padding:11px 20px;white-space:nowrap;
}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none!important}
.btn-outline{background:var(--surf2);border:1px solid var(--border);color:var(--subtle)}
.btn-outline:hover:not(:disabled){background:rgba(255,255,255,.1);color:var(--text);border-color:rgba(255,255,255,.15)}
.btn-teal{background:rgba(34,211,238,.1);border:1px solid rgba(34,211,238,.2);color:var(--cyan)}
.btn-teal:hover:not(:disabled){background:rgba(34,211,238,.18)}
.btn-hero{
  width:100%;justify-content:center;padding:16px;font-size:15px;border-radius:14px;
  background:linear-gradient(135deg,#7c3aed,#4f46e5);color:#fff;
  box-shadow:0 0 28px rgba(124,58,237,.28);
}
.btn-hero:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 6px 28px rgba(124,58,237,.45)}
.btn-hero:active:not(:disabled){transform:translateY(0)}

/* ── Status strip ── */
.status-strip{
  display:flex;align-items:center;gap:10px;margin-top:18px;
  padding:11px 16px;border-radius:11px;
  background:rgba(0,0,0,.25);border:1px solid var(--border);
  font-size:13px;transition:all .3s;
}
.dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;transition:all .3s}
.dot-idle   {background:#334155}
.dot-spin   {background:var(--yellow);animation:blink .8s infinite}
.dot-ok     {background:var(--green);box-shadow:0 0 8px var(--green)}
.dot-err    {background:var(--red)}

/* ── Inputs ── */
.field{margin-bottom:18px}
.field:last-child{margin-bottom:0}
.label{display:block;font-size:11px;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px}
.inp-row{display:flex;gap:10px;align-items:center}
input[type=text]{
  background:rgba(0,0,0,.3);border:1px solid var(--border);
  color:var(--text);border-radius:11px;padding:11px 15px;
  font-family:'JetBrains Mono',monospace;font-size:13px;outline:none;
  transition:border-color .15s,background .15s;flex:1;
}
input[type=text]:focus{border-color:var(--bfocus);background:rgba(139,92,246,.05)}
input[type=text]::placeholder{color:var(--muted)}
.hint{font-size:11px;color:var(--muted);margin-top:7px}

/* ── Mode toggle ── */
.mode-card{
  display:flex;align-items:center;gap:14px;padding:14px 16px;
  border-radius:12px;cursor:pointer;transition:all .15s;
  border:1.5px solid transparent;margin-bottom:8px;
}
.mode-card:last-child{margin-bottom:0}
.mode-card:hover{background:rgba(255,255,255,.04)}
.mode-card.sel{background:rgba(139,92,246,.08);border-color:rgba(139,92,246,.3)}
.mode-icon{font-size:22px;flex-shrink:0}
.mode-text .mode-name{font-size:14px;font-weight:600}
.mode-text .mode-sub{font-size:12px;color:var(--muted);margin-top:2px}
.radio{
  width:20px;height:20px;border-radius:50%;border:2px solid var(--border);
  margin-left:auto;flex-shrink:0;display:flex;align-items:center;justify-content:center;
  transition:all .15s;
}
.mode-card.sel .radio{border-color:var(--purple);background:var(--purple)}
.radio-inner{width:7px;height:7px;border-radius:50%;background:#fff;display:none}
.mode-card.sel .radio-inner{display:block}

/* ── Terminal ── */
.terminal{
  border-radius:14px;overflow:hidden;margin-top:10px;
  border:1px solid rgba(255,255,255,.1);
  box-shadow:0 20px 60px rgba(0,0,0,.4);
}
.term-bar{
  background:#161625;padding:11px 18px;
  display:flex;align-items:center;gap:7px;
  border-bottom:1px solid rgba(255,255,255,.07);
}
.tbt{width:11px;height:11px;border-radius:50%}
.tbt-r{background:#ff5f57}.tbt-y{background:#ffbd2e}.tbt-g{background:#28c840}
.term-label{font-size:11px;color:#475569;margin-left:10px;font-family:'JetBrains Mono',monospace}
.term-body{
  background:#0d0d1a;padding:18px 20px;
  font-family:'JetBrains Mono',monospace;font-size:11.5px;line-height:1.85;
  color:#a3e635;height:240px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;
}
.term-body::-webkit-scrollbar{width:4px}
.term-body::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:4px}
.t-ok  {color:#34d399}
.t-err {color:#f87171}
.t-warn{color:#fbbf24}
.t-dim {color:#475569}

/* ── Results ── */
.results{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px}
.res-card{
  background:var(--surf);border:1px solid var(--border);border-radius:14px;
  padding:22px 12px;text-align:center;transition:all .4s;
}
.res-card.lit{border-color:rgba(139,92,246,.25);background:rgba(139,92,246,.05)}
.res-ico{font-size:26px;margin-bottom:10px}
.res-num{
  font-size:38px;font-weight:700;letter-spacing:-1.5px;line-height:1;
  color:var(--text);transition:all .3s;
}
.res-lbl{font-size:11px;font-weight:600;color:var(--muted);margin-top:7px;
  text-transform:uppercase;letter-spacing:.07em}

/* ── Toast ── */
.toast{
  position:fixed;bottom:28px;left:50%;transform:translateX(-50%) translateY(8px);
  background:rgba(10,10,26,.95);border:1px solid var(--border);backdrop-filter:blur(16px);
  color:var(--text);padding:12px 22px;border-radius:12px;font-size:13px;font-weight:500;
  z-index:999;opacity:0;pointer-events:none;transition:all .25s;
  box-shadow:0 8px 32px rgba(0,0,0,.5);white-space:nowrap;
}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

/* ── Divider ── */
.div{height:1px;background:var(--border);margin:8px 0}
</style>
</head>
<body>
<div id="srv-banner" style="display:none;align-items:center;gap:12px;background:#450a0a;border:1px solid #f87171;border-radius:12px;padding:14px 18px;margin:0 0 18px;color:#fca5a5;font-size:13px;font-weight:600;">
  <span style="font-size:20px">🔴</span>
  <span>Servidor offline — rode <code style="background:rgba(255,255,255,.1);padding:2px 7px;border-radius:5px;font-family:monospace">python3 app_dados_basicos.py</code> e recarregue a página</span>
</div>
<div id="js-err-banner" style="display:none;align-items:center;gap:12px;background:#422006;border:1px solid #fbbf24;border-radius:12px;padding:14px 18px;margin:0 0 18px;color:#fde68a;font-size:13px;font-weight:600;">
  <span style="font-size:20px">⚠️</span>
  <span>Erro JavaScript: </span>
</div>

<div class="wrap">

  <!-- Header -->
  <div class="hdr">
    <div class="pill"><div class="pill-dot"></div>Super Sapiens · AGU</div>
    <h1>Dados <em>Básicos</em></h1>
    <p class="sub">Migração automatizada de Setor Atual em lote</p>
    <p id="js-ok" style="display:none;margin-top:10px;font-size:12px;color:#10b981;font-weight:600">✅ JavaScript ativo</p>
  </div>

  <!-- Step 1 — Conexão -->
  <div class="card" id="c1">
    <div class="step-hdr">
      <div class="step-num" id="sn1">1</div>
      <div>
        <div class="step-title">Conectar ao Sapiens</div>
        <div class="step-desc">Abra o Chrome, faça login e capture o token da sessão</div>
      </div>
    </div>
    <div class="btn-row">
      <button class="btn btn-outline" id="btn-chrome" onclick="abrirChrome(this)">🌐  Abrir Chrome</button>
      <button class="btn btn-teal" id="btn-token" onclick="capturarToken(this)">🔑  Capturar Token</button>
    </div>
    <div class="status-strip" id="strip">
      <div class="dot dot-idle" id="dot"></div>
      <span id="strip-txt">Aguardando conexão...</span>
    </div>
  </div>

  <!-- Step 2 — Configuração -->
  <div class="card" id="c2">
    <div class="step-hdr">
      <div class="step-num" id="sn2">2</div>
      <div>
        <div class="step-title">Configurar</div>
        <div class="step-desc">Defina a pasta de origem e o setor de destino</div>
      </div>
    </div>
    <div class="field">
      <label class="label">ID da Pasta</label>
      <input type="text" id="inp-pasta" placeholder="ex: 125719">
      <div class="hint">Você encontra o ID na URL da pasta no Sapiens</div>
    </div>
    <div class="field">
      <label class="label">Setor de Destino</label>
      <div class="inp-row">
        <input type="text" id="inp-setor" value="62140" style="max-width:130px">
        <span style="font-size:13px;color:var(--muted)">Protocolo DCJUD1</span>
      </div>
    </div>
  </div>

  <!-- Step 3 — Modo + Executar -->
  <div class="card" id="c3">
    <div class="step-hdr">
      <div class="step-num" id="sn3">3</div>
      <div>
        <div class="step-title">Modo de Execução</div>
        <div class="step-desc">Escolha como o robô deve operar</div>
      </div>
    </div>
    <div class="mode-card sel" id="m-dry" onclick="setModo('dryrun')">
      <div class="mode-icon">🟡</div>
      <div class="mode-text">
        <div class="mode-name">Dry Run</div>
        <div class="mode-sub">Simula tudo sem alterar nenhum processo — ideal para conferir antes</div>
      </div>
      <div class="radio"><div class="radio-inner"></div></div>
    </div>
    <div class="mode-card" id="m-real" onclick="setModo('real')">
      <div class="mode-icon">🔴</div>
      <div class="mode-text">
        <div class="mode-name">Executar</div>
        <div class="mode-sub">Altera o Setor Atual dos processos de fato no Sapiens</div>
      </div>
      <div class="radio"><div class="radio-inner"></div></div>
    </div>
    <div style="margin-top:20px">
      <button class="btn btn-hero" id="btn-go" disabled onclick="iniciar()">
        🚀 &nbsp; Iniciar Migração
      </button>
    </div>
  </div>

  <!-- Log + Resultados (oculto até iniciar) -->
  <div id="secao-log" style="display:none">
    <div class="terminal">
      <div class="term-bar">
        <div class="tbt tbt-r"></div>
        <div class="tbt tbt-y"></div>
        <div class="tbt tbt-g"></div>
        <span class="term-label">log de execução</span>
      </div>
      <div class="term-body" id="term"></div>
    </div>
    <div class="results">
      <div class="result-card res-card" id="rc-alt">
        <div class="res-ico">✅</div>
        <div class="res-num" id="n-alt">0</div>
        <div class="res-lbl">Alterados</div>
      </div>
      <div class="result-card res-card" id="rc-pul">
        <div class="res-ico">⏭️</div>
        <div class="res-num" id="n-pul">0</div>
        <div class="res-lbl">Pulados</div>
      </div>
      <div class="result-card res-card" id="rc-fal">
        <div class="res-ico">❌</div>
        <div class="res-num" id="n-fal">0</div>
        <div class="res-lbl">Falhas</div>
      </div>
    </div>
  </div>

</div>

<div class="toast" id="toast"></div>

<script>
// ── Diagnóstico ───────────────────────────────────────────────
window.onerror = function(msg, src, line, col, err) {
  var b = document.getElementById('js-err-banner');
  if (b) { b.style.display='flex'; b.querySelector('span').textContent = msg + ' (linha ' + line + ')'; }
  return false;
};

// Mostra "JS ativo" assim que o script executa
(function() {
  var el = document.getElementById('js-ok');
  if (el) el.style.display = 'block';
  // Verifica servidor
  fetch('/estado')
    .then(function(r) { return r.json(); })
    .then(function() {
      var b = document.getElementById('srv-banner');
      if (b) b.style.display = 'none';
    })
    .catch(function() {
      var b = document.getElementById('srv-banner');
      if (b) b.style.display = 'flex';
    });
})();

var _modo   = 'dryrun';
var _cursor = 0;
var _poll   = null;
var _tpoll  = null;

// ── Modo ──────────────────────────────────────────────────────
function setModo(m) {
  _modo = m;
  document.getElementById('m-dry').classList.toggle('sel', m === 'dryrun');
  document.getElementById('m-real').classList.toggle('sel', m === 'real');
}

// ── Toast ─────────────────────────────────────────────────────
function toast(msg, dur) {
  dur = dur || 3200;
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(function() { el.classList.remove('show'); }, dur);
}

// ── API ───────────────────────────────────────────────────────
function api(path, params) {
  params = params || {};
  var qs = new URLSearchParams(params).toString();
  return fetch(path + (qs ? '?' + qs : ''))
    .then(function(r) { return r.json(); })
    .catch(function(e) { return { ok: false, erro: 'Servidor não responde: ' + e.message }; });
}

// ── Status strip ──────────────────────────────────────────────
function setStrip(tipo, msg) {
  var tipos = {idle:'dot-idle', spin:'dot-spin', ok:'dot-ok', err:'dot-err'};
  var dot = document.getElementById('dot');
  dot.className = 'dot ' + (tipos[tipo] || 'dot-idle');
  document.getElementById('strip-txt').textContent = msg;
}

// ── Step 1: Conexão ───────────────────────────────────────────
function abrirChrome(btn) {
  // feedback síncrono imediato
  if (btn) { btn.disabled = true; btn.innerHTML = '⏳&nbsp; Abrindo...'; }
  setStrip('spin', 'Abrindo Chrome...');
  api('/abrir_chrome').then(function(r) {
    if (btn) { btn.disabled = false; btn.innerHTML = '🌐&nbsp; Abrir Chrome'; }
    if (r.ok) {
      setStrip('spin', 'Chrome aberto — faça login no Sapiens e clique em Capturar Token');
      toast('🌐 Chrome aberto com sucesso!');
    } else {
      setStrip('err', r.erro || 'Erro ao abrir Chrome');
      toast('❌ ' + (r.erro || 'Erro ao abrir Chrome'), 5000);
    }
  });
}

function capturarToken(btn) {
  var b = btn || document.getElementById('btn-token');
  b.disabled = true;
  b.innerHTML = '⏳&nbsp; Capturando...';
  setStrip('spin', 'Conectando ao Chrome e buscando o token JWT...');
  api('/capturar_token').then(function(r) {
    if (!r.ok) {
      setStrip('err', r.erro || 'Erro ao iniciar captura');
      b.disabled = false;
      b.innerHTML = '🔑&nbsp; Capturar Token';
      toast('❌ ' + (r.erro || 'Erro'), 5000);
      return;
    }
    _tpoll = setInterval(verificarToken, 1500);
  });
}

function verificarToken() {
  api('/estado').then(function(r) {
    if (r.fase === 'conectado') {
      clearInterval(_tpoll);
      var btn = document.getElementById('btn-token');
      btn.disabled = false;
      btn.innerHTML = '🔑&nbsp; Capturar Token';
      var nome = r.nome_usuario ? ' · ' + r.nome_usuario : '';
      setStrip('ok', 'Conectado — ID ' + r.id_usuario + nome);
      document.getElementById('sn1').textContent = '✓';
      document.getElementById('sn1').classList.add('done');
      document.getElementById('c1').classList.add('is-done');
      document.getElementById('btn-go').disabled = false;
      toast('✅ Token capturado! Você está conectado.');
    } else if (r.fase === 'erro') {
      clearInterval(_tpoll);
      var btn = document.getElementById('btn-token');
      btn.disabled = false;
      btn.innerHTML = '🔑&nbsp; Capturar Token';
      setStrip('err', r.erro || 'Erro na captura');
      toast('❌ ' + (r.erro || 'Erro'), 5000);
    }
  });
}

// ── Step 3: Iniciar ───────────────────────────────────────────
function iniciar() {
  var pasta = document.getElementById('inp-pasta').value.trim();
  var setor = document.getElementById('inp-setor').value.trim();
  if (!pasta)                  { toast('⚠️ Informe o ID da Pasta'); return; }
  if (!setor || isNaN(+setor)) { toast('⚠️ Setor destino inválido'); return; }

  var sec = document.getElementById('secao-log');
  sec.style.display = 'block';
  setTimeout(function() { sec.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 80);

  _cursor = 0;
  document.getElementById('term').innerHTML = '';
  ['n-alt','n-pul','n-fal'].forEach(function(id) { document.getElementById(id).textContent = '0'; });
  ['rc-alt','rc-pul','rc-fal'].forEach(function(id) { document.getElementById(id).classList.remove('lit'); });

  var btn = document.getElementById('btn-go');
  btn.disabled = true;
  btn.innerHTML = '⏳&nbsp; Executando...';

  api('/iniciar', { pasta: pasta, setor: setor, modo: _modo }).then(function(r) {
    if (!r.ok) {
      btn.disabled = false;
      btn.innerHTML = '🚀 &nbsp; Iniciar Migração';
      toast('❌ ' + (r.erro || 'Erro'), 5000);
      return;
    }
    _poll = setInterval(pollEstado, 900);
  });
}

// ── Polling do estado ─────────────────────────────────────────
function pollEstado() {
  api('/estado').then(function(r) {
    if (r.log && r.log.length > _cursor) {
      var body = document.getElementById('term');
      r.log.slice(_cursor).forEach(function(linha) {
        var s = document.createElement('span');
        if      (linha.indexOf('✅') >= 0 || linha.indexOf('Concluído') >= 0)  s.className = 't-ok';
        else if (linha.indexOf('❌') >= 0 || linha.indexOf('Falha') >= 0 || linha.indexOf('Erro') >= 0) s.className = 't-err';
        else if (linha.indexOf('🟡') >= 0 || linha.indexOf('⚠️') >= 0 || linha.indexOf('DRY') >= 0)    s.className = 't-warn';
        else if (linha.indexOf('═') >= 0 || linha.indexOf('─') >= 0)  s.className = 't-dim';
        s.textContent = linha + '\\n';
        body.appendChild(s);
      });
      body.scrollTop = body.scrollHeight;
      _cursor = r.log.length;
    }
    if (r.contadores) {
      animNum('n-alt', r.contadores.alterados);
      animNum('n-pul', r.contadores.pulados);
      animNum('n-fal', r.contadores.falhas);
      if (r.contadores.alterados > 0) document.getElementById('rc-alt').classList.add('lit');
      if (r.contadores.pulados   > 0) document.getElementById('rc-pul').classList.add('lit');
      if (r.contadores.falhas    > 0) document.getElementById('rc-fal').classList.add('lit');
    }
    if (r.fase === 'concluido' || r.fase === 'erro') {
      clearInterval(_poll);
      var btn = document.getElementById('btn-go');
      btn.disabled = false;
      btn.innerHTML = '🚀 &nbsp; Iniciar Migração';
      if (r.fase === 'concluido') toast('✅ Migração concluída!', 5000);
      else toast('❌ Erro: ' + (r.erro || ''), 6000);
    }
  });
}

// ── Animação de número ────────────────────────────────────────
var _prevNums = {};
function animNum(id, alvo) {
  if (_prevNums[id] === alvo) return;
  _prevNums[id] = alvo;
  var el = document.getElementById(id);
  var de = parseInt(el.textContent) || 0;
  if (de === alvo) return;
  var t = 0, steps = 20, dur = 400;
  var iv = setInterval(function() {
    t++;
    el.textContent = Math.round(de + (alvo - de) * (t / steps));
    if (t >= steps) { clearInterval(iv); el.textContent = alvo; }
  }, dur / steps);
}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
#  SERVIDOR HTTP
# ══════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, data):
        b = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        parsed = urlparse(self.path)
        p      = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        path   = parsed.path

        if path == "/":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        elif path == "/abrir_chrome":
            threading.Thread(target=_thread_abrir_chrome, daemon=True).start()
            self._json({"ok": True})

        elif path == "/capturar_token":
            threading.Thread(target=_thread_capturar_token, daemon=True).start()
            self._json({"ok": True})

        elif path == "/estado":
            with _lock:
                self._json({
                    "fase":         _estado["fase"],
                    "id_usuario":   _estado["id_usuario"],
                    "nome_usuario": _estado["nome_usuario"],
                    "log":          _estado["log"][:],
                    "contadores":   _estado["contadores"].copy(),
                    "erro":         _estado["erro"],
                })

        elif path == "/iniciar":
            pasta    = p.get("pasta", "").strip()
            setor_s  = p.get("setor", "62140").strip()
            modo     = p.get("modo", "dryrun")
            if not pasta or not setor_s.isdigit():
                self._json({"ok": False, "erro": "Parâmetros inválidos"})
                return
            with _lock:
                token      = _estado["token"]
                id_usuario = _estado["id_usuario"]
            if not token or not id_usuario:
                self._json({"ok": False, "erro": "Não conectado ao Sapiens"})
                return
            threading.Thread(
                target=_thread_migracao,
                args=(token, id_usuario, pasta, int(setor_s), modo != "real"),
                daemon=True,
            ).start()
            self._json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    server = _Server(("127.0.0.1", PORT), Handler)
    url    = f"http://127.0.0.1:{PORT}"
    print(f"✅ Dados Básicos AGU · {url}")
    print("   Feche esta janela para encerrar.")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")
