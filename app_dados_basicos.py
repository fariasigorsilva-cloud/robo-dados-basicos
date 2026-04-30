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
    "return localStorage.getItem('token');",  # Novo Sapiens (token direto)
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
    "fase":               "idle",   # idle | capturando | conectado | executando | concluido | erro
    "token":              None,
    "id_usuario":         None,
    "nome_usuario":       None,
    "log":                [],
    "contadores":         {"alterados": 0, "pulados": 0, "falhas": 0},
    "erro":               None,
    # lotação do usuário — preenchidos após captura do token
    "lotacao_unidade_id":   None,
    "lotacao_unidade_nome": None,
    "lotacao_setor_id":     None,
    "lotacao_setor_nome":   None,
}
_driver       = None
_lock         = threading.Lock()
server        = None   # preenchido em __main__; acessado pela rota /encerrar
_ultimo_ping  = None   # None = ainda não recebeu o primeiro /ping
_PING_TIMEOUT = 15     # segundos sem ping → encerrar


def _watchdog():
    """Encerra o servidor se o browser parar de enviar /ping por mais de _PING_TIMEOUT s."""
    while True:
        time.sleep(5)
        if _ultimo_ping is None:
            continue   # browser ainda não abriu
        if time.time() - _ultimo_ping > _PING_TIMEOUT:
            if server is not None:
                server.shutdown()
            break

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

def _descobrir_lotacao(token: str):
    """Tenta extrair unidade e setor de lotação do usuário via API."""
    import requests as req
    headers  = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    populate = urllib.parse.quote(json.dumps(["lotacaoPrincipal", "setor", "unidade"]))
    for ep in ["/v1/usuario/me", "/v1/usuario/perfil"]:
        try:
            r = req.get(f"{BASE_URL}{ep}?populate={populate}", headers=headers, timeout=10)
            if r.status_code != 200:
                continue
            d = r.json()
            # tenta vários caminhos possíveis na resposta
            lotacao = (d.get("lotacaoPrincipal") or d.get("lotacao") or {})
            setor   = (lotacao.get("setor") or d.get("setorAtual") or d.get("setor") or {})
            unidade = (setor.get("unidade") or lotacao.get("unidade") or d.get("unidade") or {})
            s_id    = setor.get("id")
            s_nome  = setor.get("nome") or setor.get("sigla") or ""
            u_id    = unidade.get("id")
            u_nome  = unidade.get("nome") or unidade.get("sigla") or ""
            if s_id or u_id:
                return u_id, u_nome, s_id, s_nome
        except Exception:
            continue
    return None, None, None, None

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
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        opts = Options()
        opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{PORTA_DEBUG}")
        with _lock:
            _estado["fase"] = "capturando"
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
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
        u_id, u_nome, s_id, s_nome = _descobrir_lotacao(token)
        with _lock:
            _estado.update({
                "token":              token,
                "id_usuario":         id_u,
                "nome_usuario":       nome,
                "fase":               "conectado",
                "lotacao_unidade_id":   u_id,
                "lotacao_unidade_nome": u_nome,
                "lotacao_setor_id":     s_id,
                "lotacao_setor_nome":   s_nome,
            })
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
            _log(f"  ❌ Falha HTTP {r2.status_code}")
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

/* ── Autocomplete de setor ── */
.ac-wrap{position:relative}
.ac-drop{
  position:absolute;top:calc(100% + 5px);left:0;right:0;z-index:150;
  background:#13132a;border:1px solid rgba(139,92,246,.25);border-radius:12px;
  overflow:hidden;max-height:300px;overflow-y:auto;
  box-shadow:0 12px 40px rgba(0,0,0,.6);display:none;
}
.ac-drop::-webkit-scrollbar{width:5px}
.ac-drop::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:4px}
.ac-item{
  padding:13px 16px;cursor:pointer;
  border-bottom:1px solid rgba(255,255,255,.05);transition:background .1s;
  display:flex;align-items:center;gap:12px;
}
.ac-item:last-child{border-bottom:none}
.ac-item:hover{background:rgba(139,92,246,.16)}
.ac-sigla{
  color:var(--purple);font-weight:700;font-size:11.5px;
  font-family:'JetBrains Mono',monospace;flex-shrink:0;min-width:70px;
}
.ac-info{display:flex;flex-direction:column;gap:4px;min-width:0}
.ac-nome{color:var(--text);font-size:13px;font-weight:500}
.ac-unidade{color:var(--muted);font-size:12px;white-space:normal;line-height:1.3}
.ac-empty{padding:14px 16px;color:var(--muted);font-size:13px;text-align:center}

/* ── Botão Encerrar (fixo) ── */
.btn-encerrar{
  position:fixed;top:18px;right:18px;z-index:200;
  display:inline-flex;align-items:center;gap:6px;
  background:rgba(248,113,113,.08);border:1px solid rgba(248,113,113,.2);
  color:#f87171;font-family:'Inter',sans-serif;font-size:12px;font-weight:600;
  padding:7px 14px;border-radius:10px;cursor:pointer;transition:all .15s;
}
.btn-encerrar:hover{background:rgba(248,113,113,.18);border-color:rgba(248,113,113,.4)}
.btn-encerrar:disabled{opacity:.5;cursor:not-allowed}
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
      <label class="label">Pasta</label>
      <div class="ac-wrap">
        <input type="text" id="inp-pasta-busca" placeholder="Clique para listar ou digite o nome da pasta..." autocomplete="off">
        <input type="hidden" id="inp-pasta">
        <div class="ac-drop" id="ac-drop-pasta"></div>
      </div>
      <div class="hint" id="pasta-hint">Conecte-se primeiro, depois selecione a pasta</div>
    </div>
    <div class="field">
      <label class="label">Setor de Destino</label>
      <div style="font-size:11px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.07em;margin-bottom:7px">Unidade</div>
      <div class="ac-wrap" style="margin-bottom:12px">
        <input type="text" id="inp-unidade-busca" placeholder="Pesquise a unidade..." autocomplete="off">
        <input type="hidden" id="inp-unidade">
        <div class="ac-drop" id="ac-drop-unidade"></div>
      </div>
      <div style="font-size:11px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.07em;margin-bottom:7px">Setor</div>
      <div class="ac-wrap">
        <input type="text" id="inp-setor-busca" placeholder="Selecione a unidade primeiro..." autocomplete="off" disabled>
        <input type="hidden" id="inp-setor">
        <div class="ac-drop" id="ac-drop"></div>
      </div>
      <div class="hint" id="setor-hint">Conecte-se ao Sapiens para pesquisar</div>
    </div>
  </div>

  <!-- Step 3 — Modo + Executar -->
  <div class="card" id="c3">
    <div class="step-hdr">
      <div class="step-num" id="sn3">3</div>
      <div>
        <div class="step-title">Executar</div>
        <div class="step-desc">Pronto — clique para iniciar a migração</div>
      </div>
    </div>
    <div style="margin-top:4px">
      <button class="btn btn-hero" id="btn-go" disabled onclick="iniciar()">
        🚀 &nbsp; Iniciar Migração
      </button>
      <label style="display:flex;align-items:center;gap:9px;margin-top:14px;cursor:pointer;user-select:none;width:fit-content">
        <input type="checkbox" id="chk-dryrun" style="accent-color:#fbbf24;width:15px;height:15px;flex-shrink:0">
        <span style="font-size:12px;color:var(--muted)">🟡 Dry Run — simular sem alterar os processos</span>
      </label>
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

<button class="btn-encerrar" id="btn-enc" onclick="encerrar(this)">✕&nbsp; Encerrar</button>

<p style="text-align:center;font-size:11px;color:#334155;margin-top:32px;letter-spacing:.04em">
  Projeto em andamento &nbsp;·&nbsp; Desenvolvimento: Igor Farias
</p>

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

var _modo   = 'real';   // padrão: executar de verdade
var _cursor = 0;
var _poll   = null;
var _tpoll  = null;

// ── Checkbox Dry Run ──────────────────────────────────────────
document.getElementById('chk-dryrun').addEventListener('change', function() {
  _modo = this.checked ? 'dryrun' : 'real';
  var btn = document.getElementById('btn-go');
  if (!btn.disabled) {
    btn.innerHTML = this.checked
      ? '🟡 &nbsp; Simular (Dry Run)'
      : '🚀 &nbsp; Iniciar Migração';
  }
});

// ── Pré-preencher campos com defaults (setor 62140 / EDCJUD1) ─
function _preencherPadrao(r) {
  if (!r.ok) return;
  var hidU = document.getElementById('inp-unidade');
  var inpU = document.getElementById('inp-unidade-busca');
  var inpS = document.getElementById('inp-setor-busca');
  var hidS = document.getElementById('inp-setor');
  var hint = document.getElementById('setor-hint');
  if (r.unidade_id) {
    hidU.value = r.unidade_id;
    inpU.value = (r.unidade_sigla ? r.unidade_sigla + '  —  ' : '') + r.unidade_nome;
    inpS.disabled = false;
    inpS.placeholder = 'Pesquise o setor desta unidade...';
  }
  if (r.setor_id) {
    hidS.value = r.setor_id;
    inpS.value = (r.setor_sigla ? r.setor_sigla + '  —  ' : '') + r.setor_nome;
    hint.textContent = 'Setor padrão · ID: ' + r.setor_id + '  (altere se necessário)';
  }
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
      api('/defaults').then(function(d) { _preencherPadrao(d); });
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
  if (!pasta)                  { toast('⚠️ Selecione uma pasta na lista'); return; }
  if (!setor || isNaN(+setor)) { toast('⚠️ Selecione um setor de destino na lista'); return; }

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

// ── Autocomplete de Setor (dois passos: Unidade → Setor) ──────
(function() {
  var inpU  = document.getElementById('inp-unidade-busca');
  var hidU  = document.getElementById('inp-unidade');
  var dropU = document.getElementById('ac-drop-unidade');
  var inpS  = document.getElementById('inp-setor-busca');
  var hidS  = document.getElementById('inp-setor');
  var dropS = document.getElementById('ac-drop');
  var hint  = document.getElementById('setor-hint');
  var _tU, _tS;

  function acItem(sigla, nome, sub) {
    var item = document.createElement('div');
    item.className = 'ac-item';
    var subHtml = sub ? '<span class="ac-unidade">' + sub + '</span>' : '';
    item.innerHTML =
      '<span class="ac-sigla">' + (sigla || '') + '</span>' +
      '<span class="ac-info"><span class="ac-nome">' + nome + '</span>' + subHtml + '</span>';
    return item;
  }

  // ── Setor ──────────────────────────────────────────────────
  function buscarSetores(q) {
    var uid = hidU.value;
    if (!uid) {
      dropS.innerHTML = '<div class="ac-empty">Selecione a unidade primeiro</div>';
      dropS.style.display = 'block'; return;
    }
    api('/buscar_setores', { q: q || '', unidade_id: uid }).then(function(r) {
      dropS.innerHTML = '';
      var lista = r.setores || [];
      if (!lista.length) {
        dropS.innerHTML = '<div class="ac-empty">Nenhum setor encontrado</div>';
        dropS.style.display = 'block'; return;
      }
      lista.forEach(function(s) {
        var item = acItem(s.sigla || ('#' + s.id), s.nome, '');
        item.addEventListener('mousedown', function(e) {
          e.preventDefault();
          inpS.value = (s.sigla ? s.sigla + '  —  ' : '') + s.nome;
          hidS.value = s.id;
          hint.textContent = 'Setor selecionado · ID: ' + s.id;
          dropS.style.display = 'none';
        });
        dropS.appendChild(item);
      });
      dropS.style.display = 'block';
    });
  }

  inpS.addEventListener('focus', function() { buscarSetores(this.value.trim()); });
  inpS.addEventListener('input', function() {
    hidS.value = '';
    clearTimeout(_tS);
    var q = this.value.trim();
    _tS = setTimeout(function() { buscarSetores(q); }, 350);
  });
  inpS.addEventListener('blur', function() {
    setTimeout(function() { dropS.style.display = 'none'; }, 160);
  });

  // ── Unidade ────────────────────────────────────────────────
  function buscarUnidades(q) {
    api('/buscar_unidades', { q: q || '' }).then(function(r) {
      dropU.innerHTML = '';
      var lista = r.unidades || [];
      if (!lista.length) {
        dropU.innerHTML = '<div class="ac-empty">' + (r.erro || 'Nenhuma unidade encontrada') + '</div>';
        dropU.style.display = 'block'; return;
      }
      lista.forEach(function(u) {
        var item = acItem(u.sigla || ('#' + u.id), u.nome, '');
        item.addEventListener('mousedown', function(e) {
          e.preventDefault();
          inpU.value = (u.sigla ? u.sigla + '  —  ' : '') + u.nome;
          hidU.value = u.id;
          dropU.style.display = 'none';
          // Reset setor e já carrega lista desta unidade
          inpS.value = ''; hidS.value = '';
          inpS.disabled = false;
          inpS.placeholder = 'Pesquise o setor desta unidade...';
          hint.textContent = 'Agora selecione o setor';
          buscarSetores('');
        });
        dropU.appendChild(item);
      });
      dropU.style.display = 'block';
    });
  }

  inpU.addEventListener('focus', function() { buscarUnidades(this.value.trim()); });
  inpU.addEventListener('input', function() {
    hidU.value = ''; hidS.value = ''; inpS.value = '';
    inpS.disabled = true;
    inpS.placeholder = 'Selecione a unidade primeiro...';
    clearTimeout(_tU);
    var q = this.value.trim();
    _tU = setTimeout(function() { buscarUnidades(q); }, 350);
  });
  inpU.addEventListener('blur', function() {
    setTimeout(function() { dropU.style.display = 'none'; }, 160);
  });

  // ── Pré-preencher com lotação do usuário ao conectar ───────
  window._preencherLotacao = function(r) {
    if (r.lotacao_unidade_id) {
      hidU.value = r.lotacao_unidade_id;
      inpU.value = (r.lotacao_unidade_nome || r.lotacao_unidade_id);
      inpS.disabled = false;
      inpS.placeholder = 'Pesquise o setor desta unidade...';
    }
    if (r.lotacao_setor_id) {
      hidS.value = r.lotacao_setor_id;
      inpS.value = (r.lotacao_setor_nome || String(r.lotacao_setor_id));
      hint.textContent = 'Setor padrão · ID: ' + r.lotacao_setor_id;
    }
  };
})();

// ── Autocomplete de Pasta ─────────────────────────────────────
(function() {
  var inp  = document.getElementById('inp-pasta-busca');
  var hid  = document.getElementById('inp-pasta');
  var drop = document.getElementById('ac-drop-pasta');
  var hint = document.getElementById('pasta-hint');
  var _t2;

  function renderDropPasta(pastas) {
    drop.innerHTML = '';
    if (!pastas || pastas.length === 0) {
      drop.innerHTML = '<div class="ac-empty">Nenhuma pasta encontrada</div>';
      drop.style.display = 'block';
      return;
    }
    pastas.forEach(function(pasta) {
      var item = document.createElement('div');
      item.className = 'ac-item';
      var descHtml = pasta.descricao
        ? '<span class="ac-unidade">' + pasta.descricao + '</span>'
        : '';
      item.innerHTML =
        '<span class="ac-info"><span class="ac-nome">' + pasta.nome + '</span>' + descHtml + '</span>' +
        '<span class="ac-sigla">#' + pasta.id + '</span>';
      item.addEventListener('mousedown', function(e) {
        e.preventDefault();
        inp.value  = pasta.nome;
        hid.value  = pasta.id;
        hint.textContent = 'ID selecionado: ' + pasta.id;
        drop.style.display = 'none';
      });
      drop.appendChild(item);
    });
    drop.style.display = 'block';
  }

  function buscarPastas(q) {
    api('/buscar_pastas', { q: q || '' }).then(function(r) {
      if (r.erro && !(r.pastas && r.pastas.length)) {
        drop.innerHTML = '<div class="ac-empty">' + r.erro + '</div>';
        drop.style.display = 'block';
        return;
      }
      renderDropPasta(r.pastas || []);
    });
  }

  var buscarDebounced = (function() {
    return function(q) {
      clearTimeout(_t2);
      _t2 = setTimeout(function() { buscarPastas(q); }, 350);
    };
  })();

  inp.addEventListener('focus', function() {
    buscarPastas(this.value.trim());
  });
  inp.addEventListener('input', function() {
    hid.value = '';
    hint.textContent = 'Pesquisando...';
    buscarDebounced(this.value.trim());
  });
  inp.addEventListener('blur', function() {
    setTimeout(function() { drop.style.display = 'none'; }, 160);
  });
})();

// ── Heartbeat (mantém servidor vivo enquanto browser está aberto) ──
setInterval(function() { fetch('/ping').catch(function(){}); }, 5000);

// ── Encerrar (fallback explícito) ─────────────────────────────
function encerrar(btn) {
  if (btn) btn.disabled = true;
  fetch('/encerrar').catch(function(){}).finally(function() { window.close(); });
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
                    "fase":                 _estado["fase"],
                    "id_usuario":           _estado["id_usuario"],
                    "nome_usuario":         _estado["nome_usuario"],
                    "log":                  _estado["log"][:],
                    "contadores":           _estado["contadores"].copy(),
                    "erro":                 _estado["erro"],
                    "lotacao_unidade_id":   _estado["lotacao_unidade_id"],
                    "lotacao_unidade_nome": _estado["lotacao_unidade_nome"],
                    "lotacao_setor_id":     _estado["lotacao_setor_id"],
                    "lotacao_setor_nome":   _estado["lotacao_setor_nome"],
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

        elif path == "/buscar_pastas":
            q = p.get("q", "").strip()
            with _lock:
                token      = _estado["token"]
                id_usuario = _estado["id_usuario"]
            if not token:
                self._json({"ok": False, "erro": "Conecte-se ao Sapiens primeiro", "pastas": []})
                return
            import requests as req
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                filtro_dict = {"criadoPor.id": f"eq:{id_usuario}"}
                if len(q) >= 2:
                    filtro_dict["nome"] = f"like:%{q}%"
                url = (f"{BASE_URL}/v1/administrativo/folder"
                       f"?limit=50&populate={urllib.parse.quote('[]')}"
                       f"&where={urllib.parse.quote(json.dumps(filtro_dict))}")
                r = req.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    entities = data.get("entities", data if isinstance(data, list) else [])
                    pastas = [
                        {
                            "id":        f.get("id"),
                            "nome":      f.get("nome", ""),
                            "descricao": f.get("descricao", "") or "",
                        }
                        for f in entities if f.get("id")
                    ]
                    self._json({"ok": True, "pastas": pastas})
                else:
                    self._json({"ok": True, "pastas": []})
            except Exception as e:
                self._json({"ok": False, "erro": str(e), "pastas": []})

        elif path == "/defaults":
            with _lock:
                token = _estado["token"]
            if not token:
                self._json({"ok": False})
                return
            import requests as req
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                populate = urllib.parse.quote(json.dumps(["unidade"]))
                r = req.get(
                    f"{BASE_URL}/v1/administrativo/setor/62140?populate={populate}",
                    headers=headers, timeout=10
                )
                if r.status_code == 200:
                    s = r.json()
                    u = s.get("unidade") or {}
                    self._json({
                        "ok":            True,
                        "setor_id":      s.get("id", 62140),
                        "setor_nome":    s.get("nome", ""),
                        "setor_sigla":   s.get("sigla", ""),
                        "unidade_id":    u.get("id"),
                        "unidade_nome":  u.get("nome", ""),
                        "unidade_sigla": u.get("sigla", ""),
                    })
                else:
                    self._json({"ok": False})
            except Exception:
                self._json({"ok": False})

        elif path == "/buscar_unidades":
            # Não existe endpoint /unidade no Sapiens — extrai unidades únicas
            # a partir dos setores (populate=["unidade"]), que já funciona.
            q = p.get("q", "").strip()
            with _lock:
                token = _estado["token"]
            if not token:
                self._json({"ok": False, "erro": "Conecte-se ao Sapiens primeiro", "unidades": []})
                return
            import requests as req
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                filtro_dict = {}
                if len(q) >= 2:
                    filtro_dict["unidade.nome"] = f"like:%{q}%"
                populate = urllib.parse.quote(json.dumps(["unidade"]))
                url = (f"{BASE_URL}/v1/administrativo/setor"
                       f"?limit=200&populate={populate}")
                if filtro_dict:
                    url += f"&where={urllib.parse.quote(json.dumps(filtro_dict))}"
                r = req.get(url, headers=headers, timeout=15)
                if r.status_code == 200:
                    data     = r.json()
                    entities = data.get("entities", data if isinstance(data, list) else [])
                    seen, unidades = set(), []
                    for s in entities:
                        u   = s.get("unidade") or {}
                        uid = u.get("id")
                        if uid and uid not in seen:
                            seen.add(uid)
                            unidades.append({
                                "id":    uid,
                                "nome":  u.get("nome", ""),
                                "sigla": u.get("sigla", ""),
                            })
                    unidades.sort(key=lambda x: x["nome"])
                    self._json({"ok": True, "unidades": unidades})
                else:
                    self._json({"ok": True, "unidades": []})
            except Exception as e:
                self._json({"ok": False, "erro": str(e), "unidades": []})

        elif path == "/buscar_setores":
            q          = p.get("q", "").strip()
            unidade_id = p.get("unidade_id", "").strip()
            with _lock:
                token = _estado["token"]
            if not token:
                self._json({"ok": False, "erro": "Conecte-se ao Sapiens primeiro", "setores": []})
                return
            if not unidade_id:
                self._json({"ok": True, "setores": []})
                return
            import requests as req
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                filtro_dict = {"unidade.id": f"eq:{unidade_id}"}
                if len(q) >= 2:
                    filtro_dict["nome"] = f"like:%{q}%"
                populate = urllib.parse.quote(json.dumps(["unidade"]))
                url = (f"{BASE_URL}/v1/administrativo/setor"
                       f"?where={urllib.parse.quote(json.dumps(filtro_dict))}&limit=50"
                       f"&populate={populate}")
                r = req.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    entities = data.get("entities", data if isinstance(data, list) else [])
                    setores = []
                    for s in entities:
                        if not s.get("id"):
                            continue
                        uni = s.get("unidade") or {}
                        unidade_nome = uni.get("nome") or uni.get("sigla") or ""
                        setores.append({
                            "id":      s.get("id"),
                            "nome":    s.get("nome", ""),
                            "sigla":   s.get("sigla", ""),
                            "unidade": unidade_nome,
                        })
                    self._json({"ok": True, "setores": setores})
                else:
                    self._json({"ok": True, "setores": []})
            except Exception as e:
                self._json({"ok": False, "erro": str(e), "setores": []})

        elif path == "/ping":
            global _ultimo_ping
            _ultimo_ping = time.time()
            self._json({"ok": True})

        elif path == "/encerrar":
            self._json({"ok": True})
            threading.Timer(0.5, server.shutdown).start()

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
    threading.Thread(target=_watchdog, daemon=True).start()
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("\nEncerrado.")
