"""
robo_sapiens.py
Assistente de triagem e rascunho de pareceres - AGU Super Sapiens
Autor: Procurador Federal
Arquitetura: Selenium (captura token) + requests (backend silencioso) + Gemini (IA)
Compatível com: Python 3.9+ | SDK: google-genai (novo)
"""

from __future__ import annotations  # resolve str | None no Python 3.9

import base64
import time
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    from pypdf import PdfReader
    PYPDF_OK = True
except ImportError:
    PYPDF_OK = False

import logging
from logging.handlers import RotatingFileHandler

import requests
from google import genai
from google.genai import types as genai_types
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

# ══════════════════════════════════════════════════════════════
#  LOG PERSISTENTE
# ══════════════════════════════════════════════════════════════

def _configurar_log(dir_base: Path) -> None:
    dir_logs = dir_base / "logs"
    dir_logs.mkdir(parents=True, exist_ok=True)
    log_path = dir_logs / f"robo_pauta_{__import__('datetime').date.today().strftime('%Y-%m-%d')}.log"
    handler = RotatingFileHandler(log_path, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)
    logging.info(f"Log iniciado: {log_path}")


# ══════════════════════════════════════════════════════════════
#  CONFIGURAÇÕES — lidas do arquivo .env
# ══════════════════════════════════════════════════════════════

from dotenv import load_dotenv
import os
load_dotenv(Path(__file__).parent / ".env")

GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
DRY_RUN          = "--dry-run" in sys.argv
DEBUGGER_ADDRESS = "127.0.0.1:9222"
ID_USUARIO       = os.environ.get("ID_USUARIO", "20008")
ID_FOLDER        = os.environ.get("ID_FOLDER", "125702")
BASE_URL         = "https://supersapiensbackend.agu.gov.br"

# ══════════════════════════════════════════════════════════════
#  LISTAS DE TEMAS — edite conforme sua experiência de pauta
# ══════════════════════════════════════════════════════════════

_TEMAS_SEM_ACOMPANHAMENTO = """
- IBAMA / ICMBio / órgão ambiental: redução de multa ambiental por dosimetria individual — aplicar SOMENTE se o único pedido for redução do valor da multa por critério econômico ou proporcionalidade, SEM tese de nulidade do auto de infração. Âncora: documentos discutem exclusivamente o valor ou a dosimetria da multa, sem questionar a legalidade do ato.

- IBAMA: multa por transporte de madeira sem documento (ATPF/DOF) — aplicar SOMENTE se os documentos mencionarem expressamente "ATPF", "DOF", "documento de origem florestal" ou "transporte de madeira sem autorização". Âncora obrigatória: termo ATPF ou DOF presente nos documentos.

- INSS: ações em geral de cobrança ou concessão de benefício — aplicar SOMENTE se NÃO houver menção expressa a "violência contra a mulher", "Lei Maria da Penha" ou "Lei 11.340". Âncora negativa: ausência dessas expressões confirma o enquadramento.

- DNIT / órgão de trânsito: ação anulatória de auto de infração de trânsito individual — aplicar SOMENTE se o pedido for anulação de multa de trânsito de um único veículo/condutor, SEM questionar norma em tese. Âncora: processo individual sem tese normativa.

- FNDE: ações que versam sobre salário-educação — aplicar SOMENTE se os documentos mencionarem expressamente "salário-educação" ou "contribuição social do salário-educação". Âncora: termo "salário-educação" presente nos documentos.

- EXECUÇÃO FISCAL: qualquer autarquia — aplicar SEMPRE. Execução fiscal NUNCA merece acompanhamento especial, independentemente do valor ou da autarquia. Âncora: classe processual "Execução Fiscal" ou equivalente.

- EMBARGOS À EXECUÇÃO: qualquer autarquia — aplicar como NÃO SALVO se a tese dos embargos se encaixar com precisão em algum tema da lista de ACOMPANHAMENTO: SIM abaixo. Se não houver enquadramento preciso na lista de SIM, marcar NÃO. Âncora: classe processual "Embargos à Execução" ou equivalente.

- AÇÕES DE IMPROBIDADE ajuizadas por município — aplicar SOMENTE se o polo ativo for expressamente um município (Prefeitura, Câmara Municipal). Âncora: município como parte autora identificado nos documentos.
"""

_TEMAS_COM_ACOMPANHAMENTO = """
- INSS: ação regressiva com fundamento em violência contra a mulher — aplicar SOMENTE se os documentos mencionarem expressamente "violência contra a mulher", "Lei Maria da Penha" ou "Lei 11.340/2006". Âncora obrigatória: uma dessas expressões deve constar dos documentos.

- INMETRO: validade da intimação exclusivamente por meio eletrônico, sem AR — aplicar SOMENTE se a controvérsia for sobre a dispensa de aviso de recebimento (AR) em notificações do INMETRO. Âncora: "INMETRO" + "intimação eletrônica" ou "aviso de recebimento" ou "AR" nos documentos.

- ANS: cobrança do IVR (Índice de Valorização de Ressarcimento) — aplicar SOMENTE se os documentos mencionarem expressamente "IVR" ou "Índice de Valorização de Ressarcimento". Âncora: termo "IVR" presente.

- ANS: ressarcimento ao SUS por operadora de plano de saúde — aplicar SOMENTE se os documentos mencionarem expressamente "ressarcimento ao SUS" e "operadora" ou "plano de saúde". Âncora: "ressarcimento ao SUS" presente.

- ANS: efeitos do Desenrola sobre execuções fiscais — aplicar SOMENTE se os documentos mencionarem expressamente "Desenrola" ou "REsp 2147981". Âncora: um desses termos presente.

- ANS: quórum de deliberação do Conselho de Saúde Suplementar — aplicar SOMENTE se os documentos mencionarem expressamente "quórum" e "Conselho de Saúde Suplementar" ou "CONSU". Âncora: esses termos presentes conjuntamente.

- CADE: rediscussão do mérito da infração concorrencial em embargos à execução — aplicar SOMENTE se a autarquia for o CADE e a ação for embargos à execução de multa por infração à ordem econômica. Âncora: "CADE" + "embargos à execução" + "infração à ordem econômica" ou "antitruste".

- ANATEL: constitucionalidade das taxas TFI e TFF — aplicar SOMENTE se os documentos mencionarem expressamente "TFI", "TFF", "taxa de fiscalização de instalação" ou "taxa de fiscalização de funcionamento". Âncora: um desses termos presente.

- ANATEL: base de cálculo da CIDE/FUST — aplicar SOMENTE se os documentos mencionarem expressamente "CIDE/FUST", "FUST" ou "Fundo de Universalização dos Serviços de Telecomunicações". Âncora: termo "FUST" presente.

- ANATEL: base de cálculo do ônus contratual (preço público de concessão) — aplicar SOMENTE se os documentos mencionarem expressamente "ônus contratual" e "concessão" ou "autorização" de serviço de telecomunicações. Âncora: "ônus contratual" presente.

- ANATEL: correção monetária sobre outorga de serviço móvel celular — aplicar SOMENTE se os documentos mencionarem expressamente "outorga" e "serviço móvel celular" ou "radiofrequência" e "correção monetária". Âncora: esses termos presentes conjuntamente.

- ANTT: pedágio free flow — aplicar SOMENTE se os documentos mencionarem expressamente "free flow", "pedágio sem barreira" ou "evasão de pedágio" em rodovia com sistema eletrônico sem cancela. Âncora: "free flow" ou "pedágio sem barreira" presente.

- ANTT: inscrição no SERASA antes da dívida ativa — aplicar SOMENTE se a controvérsia for sobre a legalidade de inscrição em cadastro de restrição de crédito (SERASA, SPC) antes da inscrição em dívida ativa. Âncora: "SERASA" ou "cadastro de inadimplentes" + "dívida ativa" nos documentos.

- DNIT: prazo prescricional em PAAR (multa contratual) — aplicar SOMENTE se os documentos mencionarem expressamente "PAAR" ou "Processo Administrativo de Apuração de Responsabilidade" e "prescrição". Âncora: "PAAR" + "prescrição" presentes.

- DNIT: NIP ao condutor distinto do proprietário (IRDR TRF4) — aplicar SOMENTE se os documentos mencionarem expressamente "NIP", "Notificação de Imposição de Penalidade" e "condutor" distinto do "proprietário". Âncora: "NIP" + "condutor" + "proprietário" presentes.

- DNIT: preço público por ocupação de faixa de domínio por empresas de gás ou saneamento — aplicar SOMENTE se os documentos mencionarem expressamente "faixa de domínio" e empresa de "gás" ou "saneamento" ou "água e esgoto". Âncora: "faixa de domínio" + setor de gás ou saneamento presentes.

- IBAMA: TCFA de filiais de grandes empresas — aplicar SOMENTE se os documentos mencionarem expressamente "TCFA" ou "Taxa de Controle e Fiscalização Ambiental" e "filial". Âncora: "TCFA" + "filial" presentes.

- IBAMA: armazenamento de óleo por concessionárias de veículos — aplicar SOMENTE se os documentos mencionarem expressamente "óleo" e "concessionária de veículos" ou "armazenamento". Âncora: "óleo" + "concessionária" presentes.

- IBAMA: agricultura/pecuária em campos nativos (Operações Campereada e Araxá) — aplicar SOMENTE se os documentos mencionarem expressamente "Campereada", "Araxá", "campos de altitude", "campos nativos" ou "campos sulinos". Âncora: um desses termos presente.

- IBAMA: pesca sem PREPS (enquadramento no art. 37 do Decreto 6.514/2008) — aplicar SOMENTE se os documentos mencionarem expressamente "PREPS" ou "rastreamento de embarcação" e "pesca". Âncora: "PREPS" presente.

- IBAMA: prescrição sobre termo de embargo ambiental (IRDR 1008130/TRF1) — aplicar SOMENTE se os documentos mencionarem expressamente "termo de embargo" e "prescrição intercorrente". Âncora: "embargo" + "prescrição intercorrente" presentes.

- IBAMA / ICMBio: intimação por edital para alegações finais quando autuado tem endereço conhecido (Tema 1329/STJ) — aplicar SOMENTE se os documentos mencionarem expressamente "intimação por edital" e "alegações finais" no contexto de processo administrativo sancionador ambiental. Âncora: "edital" + "alegações finais" presentes.

- IBAMA: tipificação do desmatamento amazônico como objeto de especial preservação (art. 50 do Decreto 6.514/2008) — aplicar SOMENTE se os documentos mencionarem expressamente "art. 50" do Decreto 6.514 e "vegetação nativa" do bioma amazônico. Âncora: "art. 50" + "Amazônia" ou "bioma amazônico" presentes.

- IBAMA: conversão de multa ambiental em serviços de preservação — aplicar SOMENTE se os documentos mencionarem expressamente "conversão de multa" e "serviços de preservação" ou "art. 72, §4º". Âncora: "conversão de multa" presente.

- ANP: termo inicial dos juros em multas da ANP (IAC 11/STJ) — aplicar SOMENTE se a autarquia for a ANP e a controvérsia for sobre o termo inicial de juros ou multa moratória em decisão administrativa definitiva. Âncora: "ANP" + "juros" ou "mora" + "decisão administrativa definitiva" presentes.

- ANP: dupla visita (LC 123) antes de auto de infração da ANP — aplicar SOMENTE se os documentos mencionarem expressamente "dupla visita" e "LC 123" ou "Lei Complementar 123". Âncora: "dupla visita" presente.

- ANP: rediscussão do mérito da infração em embargos após exaurimento administrativo — aplicar SOMENTE se a autarquia for a ANP, a ação for embargos à execução e a tese for rediscussão do mérito da autuação administrativa. Âncora: "ANP" + "embargos à execução" + tese de mérito da infração.

- ICMBio: decadência do ato de criação de unidade de conservação — aplicar SOMENTE se os documentos mencionarem expressamente "decadência" e "unidade de conservação" e "desapropriação". Âncora: esses três termos presentes conjuntamente.

- ICMBio: competência para autuar em áreas fora de unidades de conservação — aplicar SOMENTE se a controvérsia for sobre a competência do ICMBio para fiscalizar área fora de unidade de conservação. Âncora: "ICMBio" + "competência" + área fora da unidade de conservação.

- ANM: CFEM com preço de transferência para subsidiárias em paraísos fiscais — aplicar SOMENTE se os documentos mencionarem expressamente "CFEM" e "preço de transferência" ou "paraíso fiscal" ou "subfaturamento". Âncora: "CFEM" + "preço de transferência" presentes.

- ANM: validade de lançamento de CFEM baseado em RAL — aplicar SOMENTE se os documentos mencionarem expressamente "CFEM" e "RAL" ou "Relatório Anual de Lavra". Âncora: "CFEM" + "RAL" presentes.

- ANM: notificação ao endereço do Cadastro Mineiro (CTDM) — aplicar SOMENTE se os documentos mencionarem expressamente "Cadastro Mineiro" ou "CTDM" e "notificação" ou "intimação". Âncora: "Cadastro Mineiro" ou "CTDM" presentes.

- ANM: base de cálculo da CFEM pré-Lei 13.540/2017 (IN-DNPM 06/2000) — aplicar SOMENTE se os documentos mencionarem expressamente "IN-DNPM 06/2000" ou "IN 06/2000" e "CFEM". Âncora: "IN 06/2000" ou "IN-DNPM" + "CFEM" presentes.

- ANM / ICMBio / órgão ambiental: prescrição intercorrente trienal no processo administrativo sancionador — aplicar SOMENTE se os documentos mencionarem expressamente "prescrição intercorrente" e "processo administrativo sancionador". Âncora: ambos os termos presentes.

- MULTA ABAIXO DO MÍNIMO LEGAL: qualquer autarquia — aplicar SOMENTE se os documentos indicarem risco concreto de redução de multa abaixo do valor mínimo previsto expressamente em lei. Âncora: menção expressa a valor mínimo legal de multa e risco de redução abaixo dele.

- AÇÕES COLETIVAS movidas por sindicatos, associações, entidades de classe ou similares — aplicar SOMENTE se o polo ativo for expressamente um sindicato, associação, federação, confederação ou entidade de classe, e a ação tiver natureza coletiva (mandado de segurança coletivo, ação civil pública, ação anulatória) com potencial de beneficiar um grupo de pessoas além do autor. Âncora: termo "sindicato", "associação", "federação", "confederação" ou "entidade de classe" presente como parte autora, combinado com natureza coletiva da ação identificada nos documentos.
"""

# Número de downloads paralelos (4 é seguro; aumente com cautela)
MAX_WORKERS_DOWNLOAD = 4

# ── Pastas de saída ─────────────────────────────────────────
_HOJE            = __import__('datetime').date.today().strftime("%Y-%m-%d")
DIR_BASE         = Path("/Users/fsigor/Desktop/ROBÔ SAPIENS - PAUTA")
DIR_SAIDA        = DIR_BASE / "saida_sapiens" / _HOJE  # pasta do dia atual
DIR_PDFS         = DIR_BASE / "saida_sapiens" / "pdfs" # PDFs em cache compartilhado (reutilizável)
DIR_MINUTAS      = DIR_SAIDA / "minutas"                # análises HTML do dia

# Tipos de documento que interessam para análise de pauta
# Apenas os 5 tipos essenciais — tudo mais é ignorado
TIPOS_RELEVANTES = {
    "petição inicial",  # litígio em tese
    "acórdão",          # decisão colegiada
    "decisão",          # decisão monocrática terminativa
    "outros",           # volumes digitalizados + intimação de pauta
    "volume",           # volume físico digitalizado (tipo específico)
}

# Palavras-chave para filtrar documentos do tipo "OUTROS"
# Baixa apenas se descricaoOutros corresponder a um desses padrões:
#   1. Volume físico digitalizado: _V001_, _V002_... 
#   2. Intimação de pauta: "INTIMAÇÃO DE PAUTA", "INTIMACAO DE PAUTA"
#   3. Acórdão
#   4. Decisão monocrática terminativa
#   5. Petição inicial
_PALAVRAS_OUTROS_RELEVANTES = re.compile(
    r"_[Vv]\d+_"                          # volumes digitalizados (_V001_, _V002_...)
    r"|intima[cç][aã]o\s+de\s+pauta"      # intimação de pauta
    r"|ac[oó]rd[aã]o"                     # acórdão
    r"|decis[aã]o\s+monocr"               # decisão monocrática
    r"|peti[cç][aã]o\s+inicial"           # petição inicial
    r"|senten[cç]a"                        # sentença (qualquer variação)
    r"|indeferimento\s+do\s+pedido"      # indeferimento do pedido
    r"|deferimento\s+do\s+pedido"        # deferimento do pedido
    r"|julgamento\s+antecipado"           # julgamento antecipado
    r"|\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"  # número CNJ formatado (padrão normal)
    r"|^\d{17,20}$"                                    # número processo só dígitos
    r"|decis[aã]o\s+agravada"                         # decisão agravada (agravo)
    r"|\bAGRAVO\b"                                    # agravo de instrumento como OUTROS
    r"|\bAI\s+\w"                                    # AI seguido de nome (agravo)
    r"|parecer(\s+do\s+mpf)?"                        # parecer do MPF
    r"|PDFSAM"                                       # fragmentos de PDF digitalizado (processos delegados)
    r"|[\w-]+\.\d{4}\.\d\.\d{2}\.\d{4}\s+VOL\s+[\d.]+"  # padrão VOL ex: 5464-63.2016.4.01.3400 VOL 1.1
    r"|[\w-]+\.\d{4}\.\d\.\d{2}\.\d{4}1$"           # melhoria 1: CNJ sem zeros + sufixo volume (41001, 41002...)
    r"|\d{7}-\d{2}\.\d{4}\.[48]\.\d{2}\.\d{4,5}0[1-9]$"  # melhoria 3: justiça estadual delegada (9999/9199) com sufixo volume 01,02...
    r"|_[Vv]\d{3,}_\d{3,}_",                         # exclui apensos A001_V001 — tratado abaixo
    re.IGNORECASE | re.MULTILINE
)

# Detecta documentos "Certidão de Processo Migrado" — usados como marcador de limite
# para o fallback de apelação sem volumes (ex: "CERTIDÃO DE PROCESSO MIGRADO1")
_RE_CERTIDAO_MIGRADA = re.compile(
    r"certid[aã]o.*migrado|migrado.*certid[aã]o",
    re.IGNORECASE,
)

# Prompt base enviado ao Gemini
PROMPT_PARECER = """
IDENTIFICAÇÃO DO PROCESSO (USE ESTES DADOS — NÃO SUBSTITUA PELOS ENCONTRADOS NOS DOCUMENTOS):
NUP: {nup}
Nº CNJ: {cnj}
ENTE REPRESENTADO: {ente}

════════════════════════════════════════
DIRETRIZ DE RIGOR JURÍDICO — LEIA ANTES DE QUALQUER ANÁLISE
════════════════════════════════════════

Você é um Procurador Federal da AGU de alta performance, com postura CÉTICA e RESTRITIVA,
fazendo análise de pauta de Tribunais Regionais Federais.
Sua análise vale como orientação institucional — erros de enquadramento têm consequências reais.

PROIBIÇÕES ABSOLUTAS:
- NUNCA invente dados, partes, números de processo ou fatos não presentes nos documentos.
- NUNCA aplique teses ou precedentes de autarquias diferentes da representada no processo.
- NUNCA generalize: "também é multa", "também é execução fiscal" não são fundamentos válidos.
- NUNCA deixe TEMA_IDENTIFICADO em branco ou genérico (ex.: "ANÁLISE GERAL", "MATÉRIA DIVERSA").

════════════════════════════════════════
INSTRUÇÕES DE LEITURA
════════════════════════════════════════

- Priorize a leitura da intimação de pauta, do acórdão recorrido e da sentença de primeiro grau.
- Use os demais documentos apenas como complemento.
- A análise deve ser objetiva e direta. Não elabore além do necessário.
- Se os documentos não contiverem informação suficiente para preencher algum campo, escreva "[Não localizado nos documentos]". NUNCA invente dados.

════════════════════════════════════════
FORMATO DE RESPOSTA OBRIGATÓRIO
════════════════════════════════════════

Elabore a resposta nos blocos abaixo, nesta ordem exata.

REGRA GERAL: se a informação não for localizável nos documentos, preencha com [Não localizado nos documentos]. Nunca infira, estime ou invente dados.

REGRA UNIVERSAL PARA TODOS OS CAMPOS ABAIXO: se a informação não for encontrada expressamente nos documentos, escreva exatamente "Não localizado nos documentos". NUNCA invente, estime ou infira dados.

---DADOS_ESTRUTURADOS---
NOME_RECORRENTE: [nome completo da parte recorrente — ou "Não localizado nos documentos"]
NOME_RECORRIDO: [nome completo da parte recorrida — ou "Não localizado nos documentos"]
NOME_RELATOR: [nome do relator e turma, ex: Des. Federal João Silva — 2ª Turma — ou "Não localizado nos documentos"]
DADOS_PAUTA: [data da sessão e tipo, ex: 15/04/2026 — Julgamento Virtual — ou "Não localizado nos documentos"]
TIPO_ORIGINARIO: [tipo da ação de origem em primeiro grau — ex: Ação Ordinária, Mandado de Segurança, Execução Fiscal, Ação Civil Pública, Embargos à Execução. NUNCA escreva "Apelação", "Agravo de Instrumento" ou qualquer classe recursal — informe sempre a ação originária. Se a competência for originária do TRF, acrescente "(originário TRF)". Se não for possível identificar, escreva "Não localizado nos documentos".]
TEMA_IDENTIFICADO: [identifique o objeto litigioso concreto com precisão — ex: "Validade de auto de infração do IBAMA por desmatamento em APP", "Revisão de benefício previdenciário — incapacidade laborativa". NUNCA use termos genéricos como "matéria administrativa" ou "ANÁLISE GERAL". Se não for possível identificar, escreva "Não localizado nos documentos".]
TESE_PRECEDENTE: [cite o precedente vinculante aplicável ao caso — Tema STF/STJ com número e ementa resumida — ou "Não localizado nos documentos". Não invente temas ou números.]
DECISAO_RECORRIDA: [descreva em até 10 palavras quem recorreu e o tipo/resultado da decisão recorrida. Ex: "Particular recorre de sentença de improcedência", "IBAMA recorre de sentença que anulou auto de infração", "Particular recorre de decisão que indeferiu tutela antecipada". Seja específico — nunca escreva apenas "sentença" ou "decisão". Se não for possível identificar, escreva "Não localizado nos documentos".]
DOCS_SUFICIENTES: [SIM se os documentos permitiram analisar o mérito — NÃO se insuficientes]
---FIM_DADOS---

Se DOCS_SUFICIENTES for NÃO, encerre aqui. Não gere os blocos abaixo.

---EMENTA---
[Uma frase concisa no estilo de ementa jurídica, ex: "PROCESSUAL CIVIL. EXECUÇÃO FISCAL. REDIRECIONAMENTO. SÓCIO-GERENTE. DISSOLUÇÃO IRREGULAR. POSSIBILIDADE."]
---FIM_EMENTA---

---RELATORIO---
[Narrativa neutra e objetiva. Máximo 3 parágrafos. Descreva: (1) o objeto da ação originária e a decisão recorrida; (2) a parte que recorreu e os fundamentos do recurso; (3) a posição da AGU/ente representado.]
---FIM_RELATORIO---

---FUNDAMENTACAO---
[Análise técnico-jurídica. Máximo 3 parágrafos. Aborde: (1) a questão jurídica controvertida; (2) o precedente aplicável ou a ausência dele; (3) a perspectiva da AGU sobre o mérito.]
---FIM_FUNDAMENTACAO---

---CONCLUSAO---
[O próximo passo processual prático e objetivo. Ex: "Aguardar inclusão em pauta para julgamento." ou "Verificar se há contrarrazões pendentes de apresentação." Não sugira sustentação oral, memoriais ou atuações especiais.]
---FIM_CONCLUSAO---

DOCUMENTOS DO PROCESSO:
{documentos}
"""

# ══════════════════════════════════════════════════════════════
#  PRÉ-VERIFICAÇÃO DO AMBIENTE (macOS / Python 3.9)
# ══════════════════════════════════════════════════════════════

CHROME_CMD = (
    "/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome"
    " --remote-debugging-port=9222"
    " --user-data-dir=/tmp/sapiens_debug"
)


def verificar_ambiente() -> None:
    """Checa todos os pré-requisitos e aborta com diagnóstico claro se algo faltar."""
    # Modo web: token fornecido externamente, Chrome/Selenium não são necessários
    if os.environ.get("SAPIENS_TOKEN"):
        print("🌐 Modo web — verificação de Chrome/Selenium ignorada.")
        return

    erros  = []
    avisos = []

    print("\n🔍 Verificando ambiente...\n")

    # 1. Python >= 3.9
    v = sys.version_info
    if v < (3, 9):
        erros.append(
            f"Python {v.major}.{v.minor} detectado. É necessário Python 3.9 ou superior."
        )
    else:
        print(f"  ✅ Python {v.major}.{v.minor}.{v.micro}")

    # 2. chromedriver no PATH
    chromedriver_path = shutil.which("chromedriver")
    if not chromedriver_path:
        erros.append(
            "chromedriver não encontrado no PATH.\n"
            "     Solução: brew install --cask chromedriver\n"
            "     Depois:  xattr -d com.apple.quarantine $(which chromedriver)"
        )
    else:
        print(f"  ✅ chromedriver: {chromedriver_path}")
        try:
            result = subprocess.run(
                ["xattr", "-l", chromedriver_path],
                capture_output=True, text=True
            )
            if "com.apple.quarantine" in result.stdout:
                erros.append(
                    "chromedriver está em quarentena pelo macOS.\n"
                    f"     Solução: xattr -d com.apple.quarantine {chromedriver_path}"
                )
            else:
                print("  ✅ chromedriver sem quarentena Gatekeeper")
        except FileNotFoundError:
            pass

    # 3. Google Chrome instalado
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome_bin):
        erros.append(
            "Google Chrome não encontrado.\n"
            "     Baixe em: https://www.google.com/chrome/"
        )
    else:
        print("  ✅ Google Chrome instalado")

    # 4. Porta 9222 (Chrome debugger)
    debugger_ativo = False
    try:
        with socket.create_connection(("127.0.0.1", 9222), timeout=2):
            debugger_ativo = True
        print("  ✅ Chrome debugger ativo na porta 9222")
    except (ConnectionRefusedError, OSError):
        avisos.append(
            "Chrome com debugger NÃO está rodando na porta 9222.\n"
            "     Abra um novo Terminal e execute:\n\n"
            f"     {CHROME_CMD}\n\n"
            "     Faça login no Sapiens e volte aqui."
        )

    # 5. Dependências Python
    deps = [
        ("selenium",    "selenium"),
        ("requests",    "requests"),
        ("google-genai","google.genai"),
    ]
    for pacote, import_name in deps:
        try:
            __import__(import_name)
            print(f"  ✅ {pacote}")
        except ImportError:
            erros.append(
                f"Pacote '{pacote}' não instalado.\n"
                f"     Solução: pip3 install {pacote}"
            )

    # 6. Gemini API Key
    if GEMINI_API_KEY == "SUA_CHAVE_GEMINI_AQUI":
        erros.append(
            "GEMINI_API_KEY não configurada.\n"
            "     Edite a variável no topo do script."
        )
    else:
        print("  ✅ GEMINI_API_KEY configurada")

    # ── Relatório ────────────────────────────────────────────
    print()
    if avisos:
        print("⚠️  AVISOS:")
        for a in avisos:
            print(f"  • {a}\n")

    if erros:
        print("❌ ERROS — corrija antes de continuar:\n")
        for i, e in enumerate(erros, 1):
            print(f"  {i}. {e}\n")
        sys.exit(1)

    if not debugger_ativo:
        print("⚠️  Inicie o Chrome, faça login no Sapiens e pressione Enter quando pronto.\n")
    else:
        print("✅ Ambiente OK — iniciando robô...\n")


# ══════════════════════════════════════════════════════════════
#  CAPTURA DO TOKEN JWT
# ══════════════════════════════════════════════════════════════

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
    """
    try {
        let match = document.cookie.match(/(?:^|;\\s*)accessToken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : null;
    } catch(e) { return null; }
    """,
]


def capturar_token(driver) -> Optional[str]:
    """Tenta todas as estratégias de captura do JWT e retorna o token ou None."""
    for script in _TOKEN_SCRIPTS:
        try:
            token = driver.execute_script(script)
            if token and isinstance(token, str) and token.startswith("eyJ"):
                logging.info(f"Token capturado ({token[:20]}...)")
                return token
        except WebDriverException:
            continue
    return None


def _aguardar_relogin(driver) -> Optional[str]:
    """
    Chamado quando o token expirou e não foi possível renová-lo automaticamente.
    Pausa a execução e aguarda o usuário fazer login novamente no Chrome,
    repetindo até obter um token válido ou o usuário desistir.
    """
    print("\n" + "═" * 60)
    print("  ⚠️  TOKEN EXPIRADO — login necessário")
    print("═" * 60)
    print("  1. Vá até o Chrome com o Sapiens aberto.")
    print("  2. Faça login novamente (se necessário).")
    print("  3. Volte aqui e pressione [ENTER] para continuar.")
    print("     (ou digite 'sair' e ENTER para encerrar o robô)\n")

    while True:
        resposta = input("  Pronto? [ENTER / sair]: ").strip().lower()
        if resposta == "sair":
            raise PermissionError("Execução encerrada pelo usuário após expiração do token.")
        if driver:
            token = capturar_token(driver)
            if token:
                print("  ✅ Token capturado com sucesso. Retomando...\n")
                return token
            print("  ❌ Token ainda não encontrado. Verifique se está logado e tente novamente.")
        else:
            # Modo web sem driver: não há como capturar token automaticamente
            raise PermissionError("Token expirado. Sem driver disponível para renovar.")


def aguardar_token(driver, tentativas: int = 5, intervalo: int = 3) -> str:
    """Aguarda o token ficar disponível após login."""
    for i in range(1, tentativas + 1):
        token = capturar_token(driver)
        if token:
            return token
        print(f"  ⏳ Tentativa {i}/{tentativas} — aguardando {intervalo}s...")
        time.sleep(intervalo)
    raise RuntimeError(
        "❌ Token JWT não encontrado. Verifique se o login foi concluído."
    )


# ══════════════════════════════════════════════════════════════
#  CLIENTE HTTP SILENCIOSO
# ══════════════════════════════════════════════════════════════

class SapiensClient:
    """Gerencia requisições HTTP ao backend do Sapiens com retry e renovação de token."""

    def __init__(self, token: str, driver=None):
        self.driver  = driver
        self.session = requests.Session()
        self._set_token(token)

    def _set_token(self, token: str) -> None:
        self.token = token
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })

    def post(self, url: str, payload: dict, tentativas: int = 3) -> dict:
        """Faz POST com retry e renovação de token."""
        for tentativa in range(1, tentativas + 1):
            try:
                resp = self.session.post(url, json=payload, timeout=30)
                if resp.status_code in (200, 201):
                    try:
                        return resp.json()
                    except Exception:
                        return {"_status": resp.status_code, "_text": resp.text[:200]}
                if resp.status_code == 401:
                    print(f"  ⚠️  Token expirado (401) no POST. Tentando renovar...")
                    novo = capturar_token(self.driver) if self.driver else None
                    if not novo or novo == self.token:
                        novo = _aguardar_relogin(self.driver)
                    self._set_token(novo)
                    continue
                if resp.status_code == 429:
                    espera = 10 * tentativa
                    print(f"  ⚠️  Rate limit no POST. Aguardando {espera}s...")
                    time.sleep(espera)
                    continue
                # Outros erros: loga e retorna info
                return {"_status": resp.status_code, "_text": resp.text[:500]}
            except requests.exceptions.Timeout:
                print(f"  ⏱️  Timeout no POST (tentativa {tentativa}). Retentando...")
                time.sleep(5)
            except requests.exceptions.ConnectionError as e:
                logging.error(f"Erro de conexão no POST: {e}")
                time.sleep(10)
        return {"_error": f"Falha após {tentativas} tentativas"}

    def patch(self, url: str, payload: dict, tentativas: int = 3) -> dict:
        """Faz PATCH com retry e renovação de token."""
        for tentativa in range(1, tentativas + 1):
            try:
                resp = self.session.patch(url, json=payload, timeout=30)
                if resp.status_code in (200, 201, 204):
                    try:
                        return resp.json()
                    except Exception:
                        return {"_status": resp.status_code, "_text": resp.text[:200]}
                if resp.status_code == 401:
                    print(f"  ⚠️  Token expirado (401) no PATCH. Tentando renovar...")
                    novo = capturar_token(self.driver) if self.driver else None
                    if not novo or novo == self.token:
                        novo = _aguardar_relogin(self.driver)
                    self._set_token(novo)
                    continue
                if resp.status_code == 422:
                    # Unprocessable: loga payload para diagnóstico
                    try:
                        detalhe = resp.json()
                    except Exception:
                        detalhe = resp.text[:300]
                    return {"_status": 422, "_detalhe": detalhe}
                if resp.status_code == 429:
                    espera = 10 * tentativa
                    print(f"  ⚠️  Rate limit no PATCH. Aguardando {espera}s...")
                    time.sleep(espera)
                    continue
                return {"_status": resp.status_code, "_text": resp.text[:500]}
            except requests.exceptions.Timeout:
                print(f"  ⏱️  Timeout no PATCH (tentativa {tentativa}). Retentando...")
                time.sleep(5)
            except requests.exceptions.ConnectionError as e:
                logging.error(f"Erro de conexão no PATCH: {e}")
                time.sleep(10)
        return {"_error": f"Falha após {tentativas} tentativas"}

    def put(self, url: str, payload: dict, tentativas: int = 3) -> dict:
        """Faz PUT com retry (fallback para PATCH quando necessário)."""
        for tentativa in range(1, tentativas + 1):
            try:
                resp = self.session.put(url, json=payload, timeout=30)
                if resp.status_code in (200, 201, 204):
                    try:
                        return resp.json()
                    except Exception:
                        return {"_status": resp.status_code, "_text": resp.text[:200]}
                if resp.status_code == 401:
                    if self.driver:
                        novo = capturar_token(self.driver)
                        if novo and novo != self.token:
                            self._set_token(novo)
                            continue
                    raise PermissionError("Token expirado.")
                return {"_status": resp.status_code, "_text": resp.text[:500]}
            except requests.exceptions.Timeout:
                time.sleep(5)
            except requests.exceptions.ConnectionError:
                time.sleep(10)
        return {"_error": f"Falha após {tentativas} tentativas"}

    def get(self, url: str, tentativas: int = 3) -> dict:
        for tentativa in range(1, tentativas + 1):
            try:
                resp = self.session.get(url, timeout=30)

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code == 401:
                    print(f"  ⚠️  Token expirado (401) no GET. Tentando renovar...")
                    novo = capturar_token(self.driver) if self.driver else None
                    if not novo or novo == self.token:
                        novo = _aguardar_relogin(self.driver)
                    self._set_token(novo)
                    print("  🔄 Token renovado.")
                    continue

                if resp.status_code == 429:
                    espera = 10 * tentativa
                    print(f"  ⚠️  Rate limit. Aguardando {espera}s...")
                    time.sleep(espera)
                    continue

                resp.raise_for_status()

            except requests.exceptions.Timeout:
                print(f"  ⏱️  Timeout (tentativa {tentativa}). Retentando...")
                time.sleep(5)
            except requests.exceptions.ConnectionError as e:
                logging.error(f"Erro de conexão: {e}. Retentando em 10s...")
                time.sleep(10)

        raise RuntimeError(f"Falha após {tentativas} tentativas: {url}")


# ══════════════════════════════════════════════════════════════
#  NÍVEL 1 — PAUTA
# ══════════════════════════════════════════════════════════════

def buscar_tarefas(client: SapiensClient) -> List[dict]:
    """
    Busca tarefas da caixa de entrada do usuário.
    Usa apenas usuarioResponsavel.id para garantir que só retorna
    tarefas atribuídas ao usuário, sem misturar com outros da pasta.
    Faz paginação automática para trazer todas as tarefas.
    """
    filtro = json.dumps({
        "usuarioResponsavel.id":           f"eq:{ID_USUARIO}",
        "dataHoraConclusaoPrazo":          "isNull",
        "especieTarefa.generoTarefa.nome": "eq:JUDICIAL",
        "folder.id":                       f"eq:{ID_FOLDER}",
    })
    populate = requests.utils.quote(json.dumps([
        "processo",
        "volume",
        "processo.pessoaRepresentada",
        "processo.pessoaRepresentada.pessoa",
    ]))

    tarefas = []
    offset  = 0
    limit   = 50

    while True:
        url   = (
            f"{BASE_URL}/v1/administrativo/tarefa"
            f"?where={requests.utils.quote(filtro)}"
            f"&limit={limit}&offset={offset}"
            f"&populate={populate}"
        )
        dados = client.get(url)
        batch = dados.get("entities", [])
        total = dados.get("total", 0)
        tarefas.extend(batch)

        if len(tarefas) >= total or len(batch) < limit:
            break
        offset += limit

    logging.info(f"{len(tarefas)} tarefa(s) encontrada(s) na pauta (pasta: {ID_FOLDER}).")
    print(f"\n📋 {len(tarefas)} tarefa(s) encontrada(s) na pauta (pasta: {ID_FOLDER}).")
    return tarefas


# ══════════════════════════════════════════════════════════════
#  NÍVEL 2 — JUNTADAS
# ══════════════════════════════════════════════════════════════

def buscar_juntadas(client: SapiensClient, id_processo: str, num_cnj: str = "", classe_processual: str = "") -> List[dict]:
    populate = json.dumps([
        "documento",
        "documento.tipoDocumento",
        "documento.componentesDigitais",
        "documento.origemDados",
        "documento.vinculacaoDocumentoPrincipal",
        "documento.vinculacaoDocumentoPrincipal.documento",
        "documento.vinculacaoDocumentoPrincipal.documento.componentesDigitais",
        "documento.vinculacaoDocumentoPrincipal.documento.juntadaAtual",
    ])
    filtro = json.dumps({"volume.processo.id": f"eq:{id_processo}"})

    # Paginação automática — processos com muitos documentos (ex: 205) precisam de múltiplas páginas
    todas  = []
    offset = 0
    limit  = 100
    while True:
        url = (
            f"{BASE_URL}/v1/administrativo/juntada"
            f"?where={requests.utils.quote(filtro)}"
            f"&limit={limit}&offset={offset}"
            f"&populate={requests.utils.quote(populate)}"
        )
        dados = client.get(url)
        batch = dados.get("entities", [])
        total = dados.get("total", 0)
        todas.extend(batch)
        if len(todas) >= total or len(batch) < limit:
            break
        offset += limit

    relevantes = []
    ignorados_admin   = 0
    ignorados_tipo    = 0

    # Normaliza o número do processo para comparação (só dígitos)
    num_cnj_digits = re.sub(r"[^0-9]", "", num_cnj) if num_cnj else ""

    # Detecta apelação delegada (9999/9199/0000) — usa todas as juntadas sem filtro de tipo
    _eh_apelacao_delegada = (
        bool(re.search(r'\.(9999|9199|0000)$', (num_cnj or "").strip())) and
        "APELA" in (classe_processual or "").upper()
    )
    if _eh_apelacao_delegada:
        print(f"  📋 Processo delegado — coletando todos os documentos para scan de sentença...")

    for j in todas:
        doc         = j.get("documento") or {}
        tipo        = (doc.get("tipoDocumento") or {}).get("nome", "").lower()
        origem      = doc.get("origemDados")
        desc_outros = (doc.get("descricaoOutros") or "").strip()
        desc_digits = re.sub(r"[^0-9]", "", desc_outros)

        # 1. Filtra apenas documentos da integração PJe
        if origem is None:
            ignorados_admin += 1
            continue

        # 2. Petição inicial — sempre inclui (várias variações)
        if any(p in tipo for p in ("petição inicial", "peticao inicial", "petição inicia", "inicial")):
            relevantes.append(j)
            continue

        # 3. Acórdão — sempre inclui
        if "acórdão" in tipo or "acordao" in tipo:
            relevantes.append(j)
            continue

        # 3b. Sentença — sempre inclui
        if "senten" in tipo:
            relevantes.append(j)
            continue

        # 4. Decisão monocrática — inclui apenas decisões (não despachos)
        if tipo == "decisão" or tipo == "decisao":
            relevantes.append(j)
            continue

        # 4b. Volume físico digitalizado com tipo específico "VOLUME"
        if tipo == "volume":
            relevantes.append(j)
            continue

        # 4c. Parecer do MPF — tipo "parecer" ou descrição contendo "parecer"
        if "parecer" in tipo:
            relevantes.append(j)
            continue

        # 5. OUTROS — inclui se for volume digitalizado, intimação de pauta,
        #    ou se a descrição contiver o número do próprio processo (volume migrado)
        if "outros" in tipo:
            num_cnj_sem_zeros = num_cnj_digits.lstrip("0") if num_cnj_digits else ""
            desc_tem_num_processo = (
                num_cnj_digits and
                len(num_cnj_digits) >= 10 and
                (desc_digits == num_cnj_digits or
                 (num_cnj_sem_zeros and desc_digits.startswith(num_cnj_sem_zeros)))
            )
            if _PALAVRAS_OUTROS_RELEVANTES.search(desc_outros) or desc_tem_num_processo:
                relevantes.append(j)
            elif _RE_CERTIDAO_MIGRADA.search(desc_outros):
                # Certidão de Processo Migrado — inclui como marcador de limite de sequencial
                relevantes.append(j)
            else:
                ignorados_tipo += 1
            continue

        # 5b. Tipo específico "certidão" — inclui se for certidão de processo migrado
        if "certid" in tipo:
            if _RE_CERTIDAO_MIGRADA.search(desc_outros) or _RE_CERTIDAO_MIGRADA.search(tipo):
                relevantes.append(j)
            else:
                ignorados_tipo += 1
            continue

        # Tudo mais — ignora
        ignorados_tipo += 1

    print(f"  📎 {len(relevantes)} relevante(s) | {ignorados_admin} admin ignorado(s) | {ignorados_tipo} tipo irrelevante(s) | {len(todas)} total.")
    return relevantes


# ══════════════════════════════════════════════════════════════
#  NÍVEL 3a — CONTEÚDO TEXTUAL
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
#  NÍVEL 3b — DOWNLOAD DE PDF DIGITALIZADO ("OUTROS")
# ══════════════════════════════════════════════════════════════

def baixar_pdf_componente(
    client: SapiensClient,
    id_componente: str,
    nome_arquivo: str,
) -> Optional[Path]:
    """
    Baixa o PDF de um componente digital.
    O Sapiens retorna o binário em base64 dentro do campo 'conteudo' do JSON:
    data:application/pdf;name=arquivo.pdf;charset=utf-8;base64,JVBERi0x...
    """
    DIR_PDFS.mkdir(parents=True, exist_ok=True)
    destino = DIR_PDFS / nome_arquivo

    if destino.exists() and destino.stat().st_size > 0:
        print(f"    📦 PDF já em cache: {destino}")
        return destino

    url = f"{BASE_URL}/v1/administrativo/componente_digital/{id_componente}/download?context=%7B%7D&populate=%5B%5D"

    try:
        resp = client.session.get(url, timeout=120)
        if resp.status_code != 200:
            print(f"    ⚠️  Endpoint retornou {resp.status_code}")
            return None

        dados = resp.json()

        # Salva o fileName original do Sapiens num arquivo .meta para uso na seleção de volumes
        file_name_original = dados.get("fileName", "")
        if file_name_original:
            meta_path = destino.with_suffix(".meta")
            meta_path.write_text(file_name_original, encoding="utf-8")

        conteudo = dados.get("conteudo", "")

        if not conteudo:
            print(f"    ⚠️  Campo 'conteudo' vazio para componente {id_componente}")
            return None

        # Formato: data:application/pdf;name=arquivo.pdf;charset=utf-8;base64,JVBERi0x...
        if ";base64," in conteudo:
            b64_data = conteudo.split(";base64,", 1)[1]
            pdf_bytes = base64.b64decode(b64_data)
        elif conteudo.startswith("JVBERi"):
            # Já é base64 puro sem prefixo data URI
            pdf_bytes = base64.b64decode(conteudo)
        else:
            print(f"    ⚠️  Formato desconhecido de conteudo para componente {id_componente}")
            return None

        with open(destino, "wb") as f:
            f.write(pdf_bytes)

        kb = destino.stat().st_size // 1024
        print(f"    ✅ PDF salvo: {destino} ({kb} KB)")
        return destino

    except Exception as e:
        print(f"    ⚠️  Erro ao baixar componente {id_componente}: {e}")
        return None


def pdf_para_bytes(caminho_pdf: Path) -> bytes:
    with open(caminho_pdf, "rb") as f:
        return f.read()


def extrair_texto_componente_download(client: SapiensClient, id_componente: str) -> str:
    """
    Extrai texto de componentes HTML/texto usando o endpoint /download.
    O campo 'conteudo' pode vir como HTML puro ou data URI de texto.
    """
    url = f"{BASE_URL}/v1/administrativo/componente_digital/{id_componente}/download?context=%7B%7D&populate=%5B%5D"
    try:
        resp = client.session.get(url, timeout=30)
        if resp.status_code != 200:
            return ""
        dados    = resp.json()
        conteudo = dados.get("conteudo", "")
        if not conteudo:
            return ""
        # Remove prefixo data URI se houver
        if ";base64," in conteudo:
            import base64 as _b64
            raw = _b64.b64decode(conteudo.split(";base64,", 1)[1]).decode("utf-8", errors="ignore")
        elif conteudo.startswith("data:") and "," in conteudo:
            raw = conteudo.split(",", 1)[1]
        else:
            raw = conteudo
        # Remove tags HTML e normaliza espaços
        texto = re.sub(r"<[^>]+>", " ", raw)
        texto = re.sub(r"\s{2,}", " ", texto).strip()
        return texto
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════
#  COLETA UNIFICADA (texto + PDFs)
# ══════════════════════════════════════════════════════════════

# ── Classificação das classes processuais ────────────────────
def _classificar_classe(classe: str) -> str:
    """Mapeia o nome da classe processual para um grupo de estratégia."""
    c = (classe or "").upper()
    if "EMBARGO" in c:
        return "embargos"
    if "AGRAVO INTERNO" in c or "AGRAVO REGIMENTAL" in c:
        return "agravo_interno"
    if "AGRAVO" in c:
        return "agravo"
    if any(x in c for x in ("APELAÇÃO", "APELACAO", "REMESSA", "INOMINADO")):
        return "apelacao"
    return "outros"


def _fallback_pre_certidao_migrada(client: SapiensClient, id_processo: str) -> list:
    """
    Fallback residual para apelações sem sentença HTML e sem volumes detectados.

    Faz busca completa e independente (sem depender da lista pré-filtrada),
    usa a "Certidão de Processo Migrado" como delimitador e baixa todos os PDFs
    com numeracaoSequencial entre 2 e seq_certidao (inclusive).
    """
    populate = requests.utils.quote(json.dumps([
        "documento",
        "documento.tipoDocumento",
        "documento.componentesDigitais",
        "documento.origemDados",
    ]))
    filtro = requests.utils.quote(json.dumps({"volume.processo.id": f"eq:{id_processo}"}))

    todas = []
    offset = 0
    limit  = 100
    while True:
        url = (
            f"{BASE_URL}/v1/administrativo/juntada"
            f"?where={filtro}&limit={limit}&offset={offset}"
            f"&populate={populate}"
        )
        dados = client.get(url)
        batch = dados.get("entities", [])
        todas.extend(batch)
        if len(todas) >= dados.get("total", 0) or len(batch) < limit:
            break
        offset += limit

    todas_sorted = sorted(todas, key=lambda x: x.get("numeracaoSequencial") or 0)

    # 1. Localiza a certidão de processo migrado
    seq_certidao = None
    for j in todas_sorted:
        doc  = j.get("documento") or {}
        tipo = (doc.get("tipoDocumento") or {}).get("nome", "").lower()
        desc = (doc.get("descricaoOutros") or "").strip()
        if _RE_CERTIDAO_MIGRADA.search(desc) or _RE_CERTIDAO_MIGRADA.search(tipo):
            seq_certidao = j.get("numeracaoSequencial")
            print(f"    🔖 Certidão de processo migrado encontrada no seq. {seq_certidao} ({desc or tipo})")
            break

    if seq_certidao is None:
        print(f"    ⚠️  Fallback residual: certidão de processo migrado não localizada.")
        return []

    # 2. Coleta PDFs de todos os documentos com seq 2 ≤ seq ≤ seq_certidao
    tarefas = []
    for j in todas_sorted:
        seq = j.get("numeracaoSequencial") or 0
        if seq < 2 or seq > seq_certidao:
            continue

        doc         = j.get("documento") or {}
        tipo        = (doc.get("tipoDocumento") or {}).get("nome", "Documento")
        desc_outros = (doc.get("descricaoOutros") or "").strip()
        comps       = doc.get("componentesDigitais") or []

        for comp in comps:
            id_comp  = comp.get("id")
            mimetype = (comp.get("mimetype") or comp.get("mimeType") or "").lower()
            extensao = (comp.get("extensao") or comp.get("extension") or "").lower()
            if not id_comp:
                continue
            if "pdf" in mimetype or extensao == "pdf":
                nome = f"proc{id_processo}_comp{id_comp}.pdf"
                tarefas.append((str(id_comp), nome, desc_outros, tipo))

    print(f"    📚 Fallback residual: {len(tarefas)} PDF(s) entre seq 2 e {seq_certidao}")
    return tarefas


def _fallback_agravo_movimentos(client: SapiensClient, id_processo: str) -> list:
    """
    Fallback para AGRAVO com conteúdo insuficiente.

    Busca TODAS as juntadas do processo (incluindo movimentos administrativos)
    e usa os movimentos "DISTRIBUÍDO POR SORTEIO" e "RECEBIDO PELO DISTRIBUIDOR"
    como delimitadores de sequencial para coletar os PDFs da petição do agravo.
    Ignora arquivos > 2MB.
    """
    _re_distribuido = re.compile(
        r"DISTRIBU[IÍ][DT][OA]\s+POR\s+(SORTEIO|PREVEN[ÇC][AÃ]O|DEPEND[EÊ]NCIA)",
        re.IGNORECASE,
    )
    _re_recebido = re.compile(
        r"RECEBID[OA]\s+(PELO\s+DISTRIBUID[OA]R|NA\s+DISTRIBUI[ÇC][AÃ]O)",
        re.IGNORECASE,
    )
    LIMITE_2MB = 2 * 1024 * 1024

    # Busca todas as juntadas (sem filtro de tipo — inclui movimentos administrativos)
    populate = requests.utils.quote(json.dumps([
        "documento",
        "documento.tipoDocumento",
        "documento.componentesDigitais",
        "documento.origemDados",
    ]))
    filtro = requests.utils.quote(json.dumps({"volume.processo.id": f"eq:{id_processo}"}))

    todas = []
    offset = 0
    limit  = 100
    while True:
        url = (
            f"{BASE_URL}/v1/administrativo/juntada"
            f"?where={filtro}&limit={limit}&offset={offset}"
            f"&populate={populate}"
        )
        dados = client.get(url)
        batch = dados.get("entities", [])
        todas.extend(batch)
        if len(todas) >= dados.get("total", 0) or len(batch) < limit:
            break
        offset += limit

    todas_sorted = sorted(todas, key=lambda x: x.get("numeracaoSequencial") or 0)

    # Localiza os seqs dos movimentos delimitadores
    seq_distribuido = None
    seq_recebido    = None
    for j in todas_sorted:
        descricao = (j.get("descricao") or "").upper()
        seq       = j.get("numeracaoSequencial") or 0
        if seq_distribuido is None and _re_distribuido.search(descricao):
            seq_distribuido = seq
            print(f"    🎯 Movimento 'distribuído' no seq. {seq}: {(j.get('descricao') or '')[:80]}")
        if seq_distribuido is not None and seq_recebido is None and _re_recebido.search(descricao):
            seq_recebido = seq
            print(f"    🎯 Movimento 'recebido' no seq. {seq}: {(j.get('descricao') or '')[:80]}")
            break

    if seq_distribuido is None:
        print(f"    ⚠️  Fallback AGRAVO: movimento 'distribuído por sorteio' não localizado.")
        return []

    seq_fim = seq_recebido if seq_recebido else (seq_distribuido + 20)
    print(f"    📋 Fallback AGRAVO: coletando PDFs entre seq {seq_distribuido} e {seq_fim - 1}")

    tarefas = []
    for j in todas_sorted:
        seq = j.get("numeracaoSequencial") or 0
        if seq < seq_distribuido or seq >= seq_fim:
            continue
        doc = j.get("documento") or {}
        if doc.get("origemDados") is None:
            continue  # pula movimentos sem documento real
        tipo        = (doc.get("tipoDocumento") or {}).get("nome", "Documento")
        desc_outros = (doc.get("descricaoOutros") or "").strip()
        for comp in (doc.get("componentesDigitais") or []):
            id_comp  = comp.get("id")
            mimetype = (comp.get("mimetype") or comp.get("mimeType") or "").lower()
            extensao = (comp.get("extensao") or comp.get("extension") or "").lower()
            tamanho  = comp.get("tamanho") or 0
            if not id_comp:
                continue
            if "pdf" in mimetype or extensao == "pdf":
                if tamanho and tamanho > LIMITE_2MB:
                    print(f"    ⚠️  PDF > 2MB ignorado: {desc_outros or tipo} ({tamanho // 1024 // 1024}MB)")
                    continue
                nome = f"proc{id_processo}_comp{id_comp}.pdf"
                tarefas.append((str(id_comp), nome, desc_outros, tipo))

    print(f"    📚 Fallback AGRAVO: {len(tarefas)} PDF(s) encontrado(s)")
    return tarefas


def coletar_conteudo_processo(
    client: SapiensClient,
    id_processo: str,
    classe_processual: str = "",
    num_cnj: str = "",
) -> Tuple[str, List[Path]]:
    """
    Retorna (texto_agregado, lista_de_pdfs_baixados).
    Usa estratégia de coleta adaptada à classe processual:
      - embargos: acórdão + sentença apenas
      - agravo: petição inicial + decisão/despacho
      - apelacao: sentença + acórdão + apelação (HTML prioritário, volumes se necessário)
      - outros: comportamento padrão (top 3 volumes + HTML)
    """
    grupo = _classificar_classe(classe_processual)
    print(f"  📂 Classe: {classe_processual or 'N/D'} → estratégia: {grupo.upper()}")

    juntadas = buscar_juntadas(client, id_processo, num_cnj=num_cnj, classe_processual=classe_processual)
    blocos: List[str]  = []
    pdfs:   List[Path] = []

    # ── Filtros por grupo de classe processual ───────────────
    # Define quais tipos de documento são relevantes para cada grupo
    TIPOS_POR_GRUPO = {
        "embargos":      {"acórdão", "sentença", "decisão", "parecer"},
        "agravo":        {"petição inicial", "decisão", "despacho", "outros", "parecer"},
        "agravo_interno": None,  # aceita todos — originário pode ser apelação ou agravo
        "apelacao":      {"petição inicial", "acórdão", "sentença", "decisão", "outros", "parecer", "volume"},
        "outros":        None,  # None = aceita todos os tipos relevantes
    }
    # Detecta apelação delegada — aceita todos os tipos e pula filtro de volumes
    _eh_apelacao_delegada = (
        bool(re.search(r'\.(9999|9199|0000)$', (num_cnj or "").strip())) and
        grupo == "apelacao"
    )
    if _eh_apelacao_delegada:
        print(f"    📋 Apelação delegada — todos os documentos irão para scan de sentença")
    tipos_aceitos = None if _eh_apelacao_delegada else TIPOS_POR_GRUPO.get(grupo)

    # ── Separa componentes por tipo ──────────────────────────
    tarefas_pdf          = []  # (id_comp, nome_arquivo, desc_outros, tipo)
    tarefas_texto        = []  # (id_comp, tipo, desc_outros)
    ignorados            = 0
    ids_peticao_inicial  = set()  # nomes de PDFs que são petição inicial

    # Rastreia se encontramos sentença em HTML (para apelações)
    tem_sentenca_html  = False
    tem_acordao_html   = False

    # Padrão de volume físico digitalizado: _V001_, _V002_, etc.
    _re_volume    = re.compile(r"_[Vv]\d+_", re.IGNORECASE)
    _re_apenso    = re.compile(r"_[Aa]\d+_[Vv]\d+_", re.IGNORECASE)  # exclui apensos A001_V001

    # Marcador de certidão de processo migrado (usado no fallback)
    _re_cert_mig  = re.compile(r"certid[aã]o.*migrado|migrado.*certid[aã]o", re.IGNORECASE)

    for juntada in juntadas:
        doc         = juntada.get("documento") or {}
        tipo        = (doc.get("tipoDocumento") or {}).get("nome", "Documento")
        tipo_lower  = tipo.lower()
        comps       = doc.get("componentesDigitais") or []
        desc_outros = doc.get("descricaoOutros") or ""

        # Aplica filtro por grupo: pula tipos não relevantes para a classe
        if tipos_aceitos is not None:
            tipo_normalizado = tipo_lower.replace("ã", "a").replace("ç", "c").replace("é", "e").replace("ó", "o")
            aceito = any(t in tipo_lower or t in tipo_normalizado for t in tipos_aceitos)
            if not aceito:
                seq_ign = juntada.get("numeracaoSequencial") or "?"
                print(f"    🚫 Ignorado (tipo fora da estratégia): '{tipo}' seq={seq_ign}")
                ignorados += 1
                continue

        for comp in comps:
            id_comp  = comp.get("id")
            mimetype = (comp.get("mimetype") or comp.get("mimeType") or "").lower()
            extensao = (comp.get("extensao") or comp.get("extension") or "").lower()
            if not id_comp:
                continue

            eh_pdf   = "pdf" in mimetype or extensao == "pdf"
            eh_html  = "html" in mimetype or extensao in ("html", "htm")
            eh_texto = "text" in mimetype or extensao in ("txt", "rtf")

            # Rastreia documentos HTML relevantes (para decisão de apelação)
            if eh_html or eh_texto or not mimetype:
                _desc_low = desc_outros.lower()
                if "senten" in tipo_lower or "senten" in _desc_low:
                    tem_sentenca_html = True
                if ("acórd" in tipo_lower or "acordao" in tipo_lower or "acord" in tipo_lower
                        or "acórd" in _desc_low or "acordao" in _desc_low):
                    tem_acordao_html = True

            # Documentos "OUTROS" — baixa normalmente (já filtrados em buscar_juntadas)
            if "outros" in tipo_lower and eh_pdf:
                nome = f"proc{id_processo}_comp{id_comp}.pdf"
                tarefas_pdf.append((str(id_comp), nome, desc_outros, tipo))
                continue

            if eh_pdf:
                nome = f"proc{id_processo}_comp{id_comp}.pdf"
                tarefas_pdf.append((str(id_comp), nome, desc_outros, tipo))
            elif eh_html or eh_texto or not mimetype:
                tarefas_texto.append((str(id_comp), tipo, desc_outros))
            else:
                nome = f"proc{id_processo}_comp{id_comp}.pdf"
                tarefas_pdf.append((str(id_comp), nome, desc_outros, tipo))

    if ignorados:
        print(f"    🚫 {ignorados} documento(s) ignorado(s) pela estratégia {grupo.upper()}")

    if grupo == "apelacao" and _eh_apelacao_delegada:
        # Apelação delegada: documentos já pré-selecionados — vai direto para o scan de sentença
        print(f"    📋 Apelação delegada — enviando todos os PDFs para scan de sentença")
    elif grupo == "apelacao" and not tem_sentenca_html:
        print(f"    📋 Apelação sem sentença HTML — priorizando volumes + sentenças/acórdãos em PDF")
        _re_num_proc = re.compile(r"^\d{17,20}$")
        _re_vol_txt  = re.compile(r"VOLUMES?\s+[\d.]+", re.IGNORECASE)
        _re_cnj_suf  = re.compile(r"\d{4,7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\d+$")   # melhoria 1
        _re_est_vol  = re.compile(r"\d{7}-\d{2}\.\d{4}\.[48]\.\d{2}\.\d{3,4}0\d{1,2}$")  # melhoria 3
        _re_sentenca_desc = re.compile(
            r"senten[cç]a|ac[oó]rd[aã]o|decis[aã]o|julgamento|improcedente|procedente|extinto",
            re.IGNORECASE
        )
        def _eh_sentenca_pdf(t):
            tipo_t = t[3].lower()
            desc_t = (t[2] or "").lower()
            return (
                any(p in tipo_t for p in ("senten", "acórd", "acordao", "acord", "decis"))
                or _re_sentenca_desc.search(desc_t)
            )
        tarefas_pdf = [
            t for t in tarefas_pdf
            if (
                _re_volume.search(t[2] or "")               # padrão _V001_
                or t[3].lower() == "volume"                 # tipo VOLUME
                or _re_num_proc.match((t[2] or "").strip()) # número só dígitos
                or _re_vol_txt.search(t[2] or "")           # padrão VOL/VOLUME N
                or _re_cnj_suf.search((t[2] or "").strip()) # melhoria 1: CNJ+sufixo
                or _re_est_vol.search((t[2] or "").strip()) # melhoria 3: estadual delegado
                or _eh_sentenca_pdf(t)                      # sentença/acórdão em PDF
            )
            and not _re_apenso.search(t[2] or "")           # exclui apensos A001_V001
        ]
        tem_sentenca_pdf = any(_eh_sentenca_pdf(t) for t in tarefas_pdf)
        if tem_sentenca_pdf:
            print(f"    ✅ Sentença/acórdão em PDF detectado — será enviado ao Gemini")
        print(f"    📚 {len(tarefas_pdf)} arquivo(s) selecionado(s) para envio")

        # ── Fallback residual: certidão de processo migrado ──────────
        if not tarefas_pdf:
            print(f"    🔄 Nenhum volume detectado — acionando fallback residual (certidão de migração)")
            tarefas_pdf = _fallback_pre_certidao_migrada(client, id_processo)
    elif grupo == "apelacao" and tem_sentenca_html:
        print(f"    ✅ Apelação com sentença HTML — enviando documentos HTML, sem volumes")
        _re_num_proc2 = re.compile(r"^\d{17,20}$")
        _re_vol_txt2  = re.compile(r"VOLUMES?\s+[\d.]+", re.IGNORECASE)
        tarefas_pdf = [
            t for t in tarefas_pdf
            if not _re_volume.search(t[2] or "")
            and t[3].lower() != "volume"
            and not _re_num_proc2.match((t[2] or "").strip())
            and not _re_vol_txt2.search(t[2] or "")
        ]
    # ── Safety: petição inicial sempre presente ───────────────
    # Se nenhum documento com tipo "petição inicial" foi incluído (nem HTML nem PDF),
    # busca o primeiro PDF das juntadas por numeracaoSequencial — normalmente o Volume 1,
    # que contém a petição inicial nas primeiras páginas.
    _tem_pi_html = any(
        "inici" in t[1].lower() or "petic" in t[1].lower()
        or "inici" in (t[2] or "").lower() or "petic" in (t[2] or "").lower()
        for t in tarefas_texto
    )
    _tem_pi_pdf = any(
        "inici" in t[3].lower() or "petic" in t[3].lower()
        for t in tarefas_pdf
    )
    if not _tem_pi_html and not _tem_pi_pdf:
        _ids_incluidos = {t[0] for t in tarefas_pdf}
        _re_cert  = re.compile(r"certid[aã]o|migrado|migra[cç][aã]o", re.IGNORECASE)
        # V001_1, V001_01, V001_001, v001_1, etc. — primeiro arquivo do Volume 1
        _re_v001  = re.compile(r"_[Vv]0*1[_\-]\d+", re.IGNORECASE)
        # Fallback mais largo: qualquer arquivo do Volume 1 (_V001_, _V0001_, _V01_)
        _re_vol1  = re.compile(r"_[Vv]0*1[_\-]", re.IGNORECASE)

        # Passa 1: procura especificamente V001_1, V001_001, etc.
        # Passa 2 (fallback): qualquer arquivo do Volume 1
        for _re_busca, _label in [(_re_v001, "V001_1"), (_re_vol1, "Volume 1")]:
            _encontrou = False
            for _j in sorted(juntadas, key=lambda x: x.get("numeracaoSequencial") or 999):
                _doc  = _j.get("documento") or {}
                _tipo = (_doc.get("tipoDocumento") or {}).get("nome", "").lower()
                _desc = (_doc.get("descricaoOutros") or "").strip()
                if _re_cert.search(_desc) or _re_cert.search(_tipo):
                    continue
                if not _re_busca.search(_desc):
                    continue
                for _comp in (_doc.get("componentesDigitais") or []):
                    _id   = _comp.get("id")
                    _mime = (_comp.get("mimetype") or _comp.get("mimeType") or "").lower()
                    _ext  = (_comp.get("extensao") or _comp.get("extension") or "").lower()
                    if not _id or str(_id) in _ids_incluidos:
                        continue
                    if "pdf" in _mime or _ext == "pdf":
                        _nome = f"proc{id_processo}_comp{_id}.pdf"
                        tarefas_pdf.insert(0, (str(_id), _nome, _desc, _tipo))
                        print(f"    📎 Petição inicial não localizada — incluindo {_label} "
                              f"(seq {_j.get('numeracaoSequencial')}: {_desc})")
                        _encontrou = True
                        break
                if _encontrou:
                    break
            if _encontrou:
                break

    # ── Downloads de texto (sequencial — rápidos) ────────────
    # Documentos cujo tipo OU descrição contém "senten" têm prioridade (vão na frente)
    _re_senten = re.compile(r"senten", re.IGNORECASE)
    tarefas_texto.sort(
        key=lambda t: 0 if (_re_senten.search(t[1]) or _re_senten.search(t[2] or "")) else 1
    )
    for id_comp, tipo, desc_outros_txt in tarefas_texto:
        rotulo = f"{tipo.upper()} — {desc_outros_txt}" if desc_outros_txt else tipo.upper()
        print(f"    📄 Extraindo texto — {rotulo} (comp {id_comp})...")
        try:
            texto = extrair_texto_componente_download(client, str(id_comp))
            if texto:
                blocos.append(f"=== {rotulo} (ID {id_comp}) ===\n{texto}")
        except Exception as e:
            print(f"    ⚠️  Falha no componente {id_comp}: {e}")

    # ── Fallback AGRAVO: movimentos processuais ───────────────
    # Aciona quando o conteúdo coletado é insuficiente (< 5000 chars e sem PDFs)
    if grupo == "agravo" and not tarefas_pdf and sum(len(b) for b in blocos) < 5000:
        print(f"    🔄 Conteúdo AGRAVO insuficiente — acionando fallback de movimentos processuais")
        tarefas_pdf = _fallback_agravo_movimentos(client, id_processo)
        # PDFs do fallback contêm a petição do agravo/decisão recorrida — não precisam
        # de scan de sentença; marcamos como "iniciais" para pular esse filtro
        for _t in tarefas_pdf:
            ids_peticao_inicial.add(f"proc{id_processo}_comp{_t[0]}.pdf")

    # ── Downloads de PDF (paralelo) ───────────────────────────
    if tarefas_pdf:
        print(f"    ⚡ Baixando {len(tarefas_pdf)} PDF(s) em paralelo ({MAX_WORKERS_DOWNLOAD} workers)...")

        def _baixar(args):
            id_comp, nome, desc_outros, tipo = args
            path = baixar_pdf_componente(client, id_comp, nome)
            return path, desc_outros, tipo, id_comp

        resultados_pdf = {}  # id_comp -> (path, desc_outros)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DOWNLOAD) as executor:
            futuros = {executor.submit(_baixar, t): t for t in tarefas_pdf}
            for futuro in as_completed(futuros):
                try:
                    path, desc_outros, tipo, id_comp = futuro.result()
                    if path:
                        resultados_pdf[id_comp] = (path, desc_outros)
                        print(f"    ✅ PDF salvo: {path.name} ({path.stat().st_size//1024} KB)")
                    else:
                        print(f"    ❌ Falha: comp {id_comp}")
                except Exception as e:
                    print(f"    ⚠️  Erro no download paralelo: {e}")

        # Mantém a ordem original das juntadas
        for id_comp, nome, desc_outros, tipo in tarefas_pdf:
            if id_comp in resultados_pdf:
                path, desc = resultados_pdf[id_comp]
                pdfs.append(path)
                # Lê o fileName original salvo no download (se existir)
                meta_path = path.with_suffix(".meta")
                file_name_original = ""
                if meta_path.exists():
                    try:
                        file_name_original = meta_path.read_text(encoding="utf-8").strip()
                    except Exception:
                        pass
                # Salva desc, tipo e fileName no .meta separados por pipe
                meta_path.write_text(f"{desc}|{tipo}|{file_name_original}", encoding="utf-8")

                # Rastreia petições iniciais pelo tipo
                # EXCETO se for processo 9999/9199 E o fileName tiver sufixo numérico
                # (volume digitalizado cadastrado incorretamente como petição inicial)
                tipo_lower2 = tipo.lower()
                eh_tipo_inicial = any(p in tipo_lower2 for p in ("petição inicial", "peticao inicial", "inicial"))
                eh_proc_delegado = bool(re.search(r'\.(9999|9199)$', (num_cnj or "").strip()))
                eh_volume_disfarcado = (
                    eh_proc_delegado and
                    bool(re.search(r'\d+$', file_name_original.strip())) if file_name_original else False
                )
                if eh_tipo_inicial and not eh_volume_disfarcado:
                    ids_peticao_inicial.add(path.name)

    return "\n\n".join(blocos), pdfs, ids_peticao_inicial, grupo


# ══════════════════════════════════════════════════════════════
#  GEMINI 2.0 (novo SDK: google-genai)
# ══════════════════════════════════════════════════════════════

def configurar_gemini() -> genai.Client:
    """Retorna cliente Gemini usando o novo SDK google-genai."""
    return genai.Client(api_key=GEMINI_API_KEY)


def _extrair_paginas_pdf(caminho_pdf: Path, min_chars_por_pagina: int = 80) -> list:
    """
    Extrai texto página por página de um PDF pesquisável.
    Retorna lista de tuplas (num_pagina, texto).
    Retorna lista vazia se PDF for escaneado ou inválido.
    """
    if not PYPDF_OK or not caminho_pdf.exists():
        return []
    try:
        with open(caminho_pdf, "rb") as fh:
            header = fh.read(5)
        if not header.startswith(b"%PDF"):
            return []
    except Exception:
        return []
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            reader   = PdfReader(str(caminho_pdf))
            num_pags = len(reader.pages)
            if num_pags == 0:
                return []
            paginas = []
            chars_total = 0
            for i, page in enumerate(reader.pages, 1):
                texto = page.extract_text() or ""
                if texto.strip():
                    paginas.append((i, texto.strip()))
                    chars_total += len(texto)
            if not paginas:
                return []
            media = chars_total / num_pags
            if media < min_chars_por_pagina:
                return []
            return paginas
    except Exception:
        return []


def _localizar_sentenca_no_pdf(paginas: list) -> list:
    """
    Localiza as páginas de sentença/acórdão de mérito num PDF
    usando busca direta por palavras-chave — rápido, preciso, gratuito.
    Varre de trás para frente e retorna as 3 últimas ocorrências encontradas.
    Retorna lista de números de página ou lista vazia se não encontrado.
    """
    PADROES = re.compile(
        # Dispositivos de sentença de mérito
        r"\bJULGO\s+(PROCEDENTE|IMPROCEDENTE|EXTINTO|PARCIALMENTE|PROCEDENTE\s+EM\s+PARTE)"
        r"|\bJULGO\s+EXTINTO\s+O\s+PROCESSO"
        r"|\bEXTINGO\s+O\s+PROCESSO"
        r"|\bHOMOLOGO\s+.{0,30}(acordo|desistência|renúncia|pedido)"
        r"|\bDEFIRO\s+O\s+PEDIDO"
        r"|\bINDEFIRO\s+O\s+PEDIDO"
        r"|\bCONDENO\s+.{0,30}(réu|ré|autor|requerido)"
        r"|\bABSOLVO\s+.{0,30}(réu|ré|acusado)"
        r"|\bPRONUNCIO\s+A\s+PRESCRI[CÇ][AÃ]O"          # sentença de prescrição tributária
        r"|\bEXTINGO\s+O\s+FEITO\s+COM\s+RESOLU[CÇ][AÃ]O\s+DO\s+M[EÉ]RITO"  # extinção com mérito
        r"|RAZ[ÕO]ES\s+PELAS\s+QUAIS.{0,50}(JULG|EXTINGO|CONDENO|DEFIRO)"    # fórmula arcaica de dispositivo
        r"|\bCONDENO\s+[AO]\s+(EXEQUENTE|EXECUTAD|AUTOR|R[EÉ]U|REQUERENTE|APELANTE|RECORRENTE)"  # variações de CONDENO
        # Fórmulas do dispositivo
        r"|ANTE\s+O\s+EXPOSTO.{0,150}(JULG|EXTINGO|CONDENO|DEFIRO|HOMOLOGO|PRONUNCIO)"
        r"|PELO\s+EXPOSTO.{0,150}(JULG|EXTINGO|CONDENO|DEFIRO|HOMOLOGO)"
        r"|DIANTE\s+DO\s+EXPOSTO.{0,150}(JULG|EXTINGO|CONDENO|DEFIRO|HOMOLOGO)"
        r"|EM\s+FACE\s+DO\s+EXPOSTO.{0,150}(JULG|EXTINGO|CONDENO|DEFIRO|HOMOLOGO)"
        r"|DO\s+EXPOSTO.{0,150}(JULG|EXTINGO|CONDENO|DEFIRO|HOMOLOGO)"
        r"|POSTO\s+ISSO.{0,150}(JULG|EXTINGO|CONDENO|DEFIRO|HOMOLOGO)"
        r"|ISSO\s+POSTO.{0,150}(JULG|EXTINGO|CONDENO|DEFIRO|HOMOLOGO)"
        # Acórdão
        r"|\bNEGO\s+PROVIMENTO"
        r"|\bDOU\s+PROVIMENTO"
        r"|\bACORDAM\b"
        r"|\bDÁ-SE\s+PROVIMENTO"
        r"|\bNÃO\s+SE\s+CONHECE\s+DO\s+RECURSO"
        r"|\bCONHECE-SE\s+E\s+(NEGA|DÁ)-SE\s+PROVIMENTO"
        r"|SENTEN[CÇ]A\s+(N[°º.]|TIPO|DE\s+MÉRITO|TERMINATIVA)"
        r"|PASSO\s+A\s+DECIDIR",
        re.IGNORECASE | re.DOTALL
    )
    # Varre de trás para frente — pega as 2 últimas ocorrências
    # (evita falsos positivos de decisões interlocutórias nas primeiras páginas)
    encontradas = []
    for num_pag, texto in reversed(paginas):
        if PADROES.search(texto):
            encontradas.append(num_pag)
            if len(encontradas) >= 2:
                break

    if not encontradas:
        print(f"    🚫 Sem sentença/acórdão neste volume")
        return []

    # Retorna em ordem crescente (da menor para a maior página)
    encontradas.sort()
    print(f"    🎯 Sentença/acórdão detectado nas páginas: {encontradas}")
    return encontradas


def _extrair_texto_pdf(caminho_pdf: Path, min_chars_por_pagina: int = 80) -> str:
    """
    Tenta extrair texto de um PDF pesquisável usando pypdf.
    Retorna o texto se o PDF tiver OCR/texto embutido suficiente.
    Retorna string vazia se for escaneado sem texto (deve ser enviado como binário).

    min_chars_por_pagina: mínimo de caracteres médios por página para considerar pesquisável.
    """
    if not PYPDF_OK:
        return ""
    # Valida header antes de tentar extrair
    try:
        with open(caminho_pdf, "rb") as fh:
            header = fh.read(5)
        if not header.startswith(b"%PDF"):
            return ""  # Nao e PDF valido — nao tenta extrair
    except Exception:
        return ""
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            reader    = PdfReader(str(caminho_pdf))
            num_pags  = len(reader.pages)
            if num_pags == 0:
                return ""

            blocos = []
            for page in reader.pages:
                texto = page.extract_text() or ""
                if texto.strip():
                    blocos.append(texto.strip())

            texto_total = "\n".join(blocos)
            media_chars = len(texto_total) / num_pags

            if media_chars >= min_chars_por_pagina:
                return texto_total  # PDF pesquisavel — retorna texto completo
            else:
                return ""  # PDF escaneado sem OCR — deve ser enviado como binario
    except Exception:
        return ""


def _selecionar_pdfs_relevantes(pdfs: List[Path], limite_bytes: int) -> List[Path]:
    """
    Seleciona PDFs para envio ao Gemini priorizando peças mais recentes.
    Lê o .meta (descricaoOutros) e detecta dois padrões:
      - "_V006_001", "_V003_002" -> ordena por volume decrescente (ignora V999 = certidão migração)
      - "DOC 22 - ...", "DOC 1 - ..." -> ordena por número DOC decrescente
    Sem padrão: incluído por último até o limite.
    """
    padrao_vol = re.compile(r"_[Vv](\d+)[_](\d+)", re.IGNORECASE)
    padrao_doc = re.compile(r"\bDOC\s+(\d+)\b", re.IGNORECASE)

    itens = []  # (chave_ordenacao, path, descricao, tipo)

    for p in pdfs:
        meta_path = p.with_suffix(".meta")
        descricao = ""
        tipo_doc  = ""
        if meta_path.exists():
            try:
                conteudo  = meta_path.read_text(encoding="utf-8").strip()
                if "|" in conteudo:
                    descricao, tipo_doc = conteudo.rsplit("|", 1)
                else:
                    descricao = conteudo
            except Exception:
                pass

        # Padrão volume: _V003_001 (ignora V999 = certidão de migração)
        m_vol = padrao_vol.search(descricao or p.name)
        if m_vol:
            vol  = int(m_vol.group(1))
            part = int(m_vol.group(2))
            if vol == 999:
                itens.append((0, p, descricao, "sem_padrao"))
            else:
                itens.append((vol * 10000 + part, p, descricao, "volume"))
            continue

        # Melhoria 1: CNJ sem zeros à esquerda + sufixo de volume
        # Ex: 9167-65.2018.4.01.41001 → sufixo "1" = volume 1
        _re_cnj_sufixo = re.compile(
            r"\d{4,7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}(\d+)$"
        )
        m_cnj_suf = _re_cnj_sufixo.search(descricao.strip())
        if m_cnj_suf:
            vol_n = int(m_cnj_suf.group(1))
            itens.append((vol_n * 10000, p, descricao, "volume"))
            continue

        # Melhoria 3: processos estaduais delegados (9999/9199) com sufixo de volume
        # Ex: 0000592-28.2014.8.18.00501 → sufixo "01" = volume 1
        _re_estadual_vol = re.compile(
            r"\d{7}-\d{2}\.\d{4}\.[48]\.\d{2}\.\d{3,4}0(\d{1,2})$"
        )
        m_est = _re_estadual_vol.search(descricao.strip())
        if m_est:
            vol_n = int(m_est.group(1))
            itens.append((vol_n * 10000, p, descricao, "volume"))
            continue

        # Tipo VOLUME, número só dígitos, ou padrão VOL 1.1 — trata como volume
        _re_num_proc3 = re.compile(r"^\d{17,20}$")
        _re_vol_txt3  = re.compile(r"VOL\s+([\d]+)\.?([\d]*)", re.IGNORECASE)
        m_vol_txt = _re_vol_txt3.search(descricao)
        if m_vol_txt:
            vol_n  = int(m_vol_txt.group(1))
            part_n = int(m_vol_txt.group(2) or 0)
            itens.append((vol_n * 10000 + part_n, p, descricao, "volume"))
            continue
        if descricao.lower() == "volume" or _re_num_proc3.match(descricao.strip()):
            itens.append((1, p, descricao, "volume"))
            continue

        # Padrão DOC N: "DOC 11 - ...", "DOC 1 - ..."
        m_doc = padrao_doc.search(descricao)
        if m_doc:
            itens.append((int(m_doc.group(1)), p, descricao, "doc"))
            continue

        # Sem padrão reconhecido
        itens.append((0, p, descricao, "sem_padrao"))

    # Mais recente primeiro
    itens.sort(key=lambda x: x[0], reverse=True)

    tipos_detectados = {t for _, _, _, t in itens if t != "sem_padrao"}
    if tipos_detectados:
        print(f"    📚 Padrão detectado: {tipos_detectados} — priorizando peças mais recentes")

    # Para volumes físicos: limita aos 3 volumes mais altos
    # Identifica os 3 números de volume distintos mais altos
    MAX_VOLUMES = 3
    volumes_vistos = set()
    volumes_permitidos = set()
    for chave, p, descricao, tipo in itens:
        if tipo == "volume":
            # Extrai número do volume da chave (chave = vol*10000 + part)
            num_vol = chave // 10000
            volumes_vistos.add(num_vol)
    # Pega os 3 maiores
    top_vols = sorted(volumes_vistos, reverse=True)[:MAX_VOLUMES]
    volumes_permitidos = set(top_vols)
    if volumes_vistos:
        print(f"    📚 Volumes no processo: {sorted(volumes_vistos)} — enviando apenas top {MAX_VOLUMES}: {sorted(volumes_permitidos, reverse=True)}")

    selecionados = []
    bytes_usados = 0

    for chave, p, descricao, tipo in itens:
        try:
            # Para volumes: só inclui se estiver nos top 3
            if tipo == "volume":
                num_vol = chave // 10000
                if num_vol not in volumes_permitidos:
                    print(f"    ⏭️  Volume {num_vol} fora do top {MAX_VOLUMES} — pulando: {descricao[:50]}")
                    continue

            tam = p.stat().st_size
            limite_individual = 20 * 1024 * 1024  # 20MB por PDF
            if tam > limite_individual:
                print(f"    ⚠️  PDF muito grande ({tam//1024//1024}MB) — pulando: {descricao or p.name}")
                continue
            if bytes_usados + tam <= limite_bytes:
                selecionados.append(p)
                bytes_usados += tam
                if descricao and tipo != "sem_padrao":
                    print(f"    📌 {descricao[:70]}")
            else:
                label = descricao or p.name
                print(f"    ⚠️  Limite total atingido — pulando: {label[:60]}")
        except Exception:
            pass

    print(f"    📊 PDFs selecionados: {len(selecionados)}/{len(pdfs)} ({bytes_usados//1024//1024}MB de {limite_bytes//1024//1024}MB)")
    return selecionados


def gerar_minuta_parecer(
    cliente_gemini: genai.Client,
    numero_processo: str,
    textos: str,
    pdfs: Optional[List[Path]] = None,
    ids_peticao_inicial: set = None,
    grupo_classe: str = "",
    ente: str = "",
) -> str:
    """
    Envia textos + PDFs ao Gemini 2.0 Flash e retorna a minuta.
    PDFs digitalizados são enviados como bytes inline.
    """
    tem_texto = bool(textos.strip())
    tem_pdfs  = bool(pdfs)

    if not tem_texto and not tem_pdfs:
        return "[Sem conteúdo documental disponível para geração de parecer.]"

    partes = []

    # 1º — Prompt + textos HTML/nativos extraídos (intimações, despachos, decisões)
    # Estes têm prioridade pois contêm a intimação de pauta e peças recentes em texto limpo
    partes.append(
        PROMPT_PARECER.format(
            nup=numero_processo,
            cnj=numero_processo,
            ente=ente or "não informado",
            documentos=textos[:40_000] if tem_texto else "(documentos textuais nao encontrados — ver PDFs abaixo)",
        )
    )
    # 2º — PDFs digitalizados (volumes físicos do processo) virão a seguir

    # Monta o conteúdo para o Gemini:
    # 1º — Textos HTML/nativos já extraídos (leves, contêm intimação/despacho/decisão)
    # 2º — PDFs ordenados por relevância (volumes mais recentes primeiro)
    LIMITE_BYTES      = 50 * 1024 * 1024  # 50MB total para binários
    MAX_PDFS_BINARIOS = 3                  # máximo de PDFs escaneados (sem OCR)

    # Processa PDFs em ordem de relevância:
    # 1º todos os com OCR (viram texto leve)
    # 2º os 3 mais relevantes sem OCR (volumes mais recentes = sentença/acórdão)
    pdfs_binarios   = 0
    pdfs_como_texto = 0
    chars_de_pdfs   = 0

    if tem_pdfs:
        pdfs_selecionados = _selecionar_pdfs_relevantes(pdfs, LIMITE_BYTES)
        total_bytes = 0

        # Primeira passagem: extrai texto de todos os PDFs com OCR
        pdfs_sem_ocr = []
        LIMITE_CHARS_TOTAL = 400_000
        chars_total = len(textos)

        for pdf_path in pdfs_selecionados:
            try:
                t0 = time.time()
                texto_pdf = _extrair_texto_pdf(pdf_path)
                t_ocr = time.time() - t0
                if t_ocr > 1:
                    print(f"    ⏱️  OCR demorou {t_ocr:.1f}s: {pdf_path.name}")

                if texto_pdf:
                    texto_para_gemini = texto_pdf
                    # Petição inicial: fonte 1 = ids_peticao_inicial, fonte 2 = .meta
                    eh_inicial = False
                    if ids_peticao_inicial and pdf_path.name in ids_peticao_inicial:
                        eh_inicial = True
                    else:
                        meta_path2 = pdf_path.with_suffix(".meta")
                        if meta_path2.exists():
                            try:
                                conteudo2  = meta_path2.read_text(encoding="utf-8").strip()
                                partes2    = conteudo2.split("|")
                                tipo_doc2  = partes2[1].lower() if len(partes2) >= 2 else conteudo2.lower()
                                file_name2 = partes2[2] if len(partes2) >= 3 else ""
                                eh_tipo_ini   = any(p in tipo_doc2 for p in ("petição inicial", "peticao inicial", "inicial"))
                                # Só trata como volume disfarcado se for processo 9999/9199
                                eh_proc_del   = bool(re.search(r'\.(9999|9199)$', (numero_processo or "").strip()))
                                eh_vol_disf   = eh_proc_del and bool(re.search(r'\d+$', file_name2.strip())) if file_name2 else False
                                if eh_tipo_ini and not eh_vol_disf:
                                    eh_inicial = True
                            except Exception:
                                pass
                    # Agravos e agravo interno: filtra por tipo relevante
                    # (evita enviar RG, CPF, documentos pessoais)
                    eh_agravo_relevante = False
                    if grupo_classe in ("agravo", "agravo_interno"):
                        meta_path3 = pdf_path.with_suffix(".meta")
                        tipo_doc3 = ""
                        desc_doc3 = ""
                        if meta_path3.exists():
                            try:
                                conteudo3 = meta_path3.read_text(encoding="utf-8").strip()
                                partes3   = conteudo3.split("|")
                                desc_doc3 = partes3[0].lower() if len(partes3) >= 1 else ""
                                tipo_doc3 = partes3[1].lower() if len(partes3) >= 2 else ""
                            except Exception:
                                pass
                        _AGRAVO_REL = re.compile(
                            r"peti[cç][aã]o\s+inicial|inicial"
                            r"|agravo(\s+(de\s+instrumento|interno|regimental))?"
                            r"|decis[aã]o|despacho"
                            r"|ac[oó]rd[aã]o|senten[cç]a"   # para agravo_interno (processo originário)
                            r"|contrarraz[oõ]|recurso"
                            r"|parecer",
                            re.IGNORECASE
                        )
                        if _AGRAVO_REL.search(tipo_doc3) or _AGRAVO_REL.search(desc_doc3):
                            eh_agravo_relevante = True
                        elif not tipo_doc3 and not desc_doc3:
                            eh_agravo_relevante = True  # sem .meta: envia por precaução

                    if eh_inicial:
                        print(f"    📄 Petição inicial em PDF — enviando sem filtro de sentença")
                    elif eh_agravo_relevante:
                        print(f"    📄 Agravo relevante — enviando sem filtro de sentença")
                    else:
                        # Localiza página da sentença por keywords
                        paginas = _extrair_paginas_pdf(pdf_path)
                        if paginas:
                            pags_sentenca = _localizar_sentenca_no_pdf(paginas)
                            if not pags_sentenca:
                                print(f"    🚫 Sem sentença/acórdão — descartando: {pdf_path.name}")
                                continue
                            else:
                                # Monta janelas separadas ao redor de cada ocorrência
                                margem  = 15
                                blocos_trecho = []
                                intervalos = []
                                for pag in pags_sentenca:
                                    idx = next((i for i, (n, _) in enumerate(paginas) if n == pag), 0)
                                    inicio = max(0, idx - margem)
                                    fim    = min(len(paginas), idx + margem + 1)
                                    intervalos.append((inicio, fim))
                                intervalos.sort()
                                fundidos = [intervalos[0]]
                                for ini, fim in intervalos[1:]:
                                    if ini <= fundidos[-1][1]:
                                        fundidos[-1] = (fundidos[-1][0], max(fundidos[-1][1], fim))
                                    else:
                                        fundidos.append((ini, fim))
                                for inicio, fim in fundidos:
                                    trecho_bloco = paginas[inicio:fim]
                                    texto_bloco  = "\n".join(t for _, t in trecho_bloco)
                                    pag_i = trecho_bloco[0][0]
                                    pag_f = trecho_bloco[-1][0]
                                    blocos_trecho.append((pag_i, pag_f, texto_bloco))
                                texto_para_gemini = "\n\n[...trecho omitido...]\n\n".join(
                                    t for _, _, t in blocos_trecho
                                )
                                desc = ", ".join(f"pág {i}-{f}" for i, f, _ in blocos_trecho)
                                print(f"    ✂️  Janelas: {desc} ({len(texto_para_gemini):,} chars)")
                        espaco = max(0, LIMITE_CHARS_TOTAL - chars_total)
                        if espaco > 500:
                            texto_para_gemini = texto_para_gemini[:espaco]
                            print(f"    ✂️  Truncado para {espaco:,} chars: {pdf_path.name}")
                        else:
                            print(f"    ⚠️  Limite atingido — pulando: {pdf_path.name}")
                            pdfs_sem_ocr.append(pdf_path)
                            continue

                    partes.append(f"\n=== CONTEUDO DO PDF: {pdf_path.name} ===\n{texto_para_gemini}\n")
                    chars_de_pdfs += len(texto_para_gemini)
                    chars_total   += len(texto_para_gemini)
                    pdfs_como_texto += 1
                    tam_kb = pdf_path.stat().st_size // 1024
                    print(f"    📝 PDF→texto: {pdf_path.name} ({tam_kb}KB → {len(texto_para_gemini):,} chars)")
                else:
                    # Sem OCR — guarda para segunda passagem
                    try:
                        with open(pdf_path, "rb") as fh:
                            header = fh.read(5)
                        if header.startswith(b"%PDF"):
                            pdfs_sem_ocr.append(pdf_path)
                        else:
                            print(f"    ⚠️  Arquivo nao e PDF valido — ignorando: {pdf_path.name}")
                    except Exception:
                        pass
            except Exception as e:
                print(f"    ⚠️  Falha ao processar {pdf_path.name}: {e}")

        # Segunda passagem: envia até MAX_PDFS_BINARIOS sem OCR (já ordenados por relevância)
        ignorados_binarios = 0
        for pdf_path in pdfs_sem_ocr:
            if pdfs_binarios >= MAX_PDFS_BINARIOS:
                ignorados_binarios += 1
                continue
            try:
                # Valida novamente o header antes de enviar como binário
                with open(pdf_path, "rb") as fh:
                    header = fh.read(5)
                if not header.startswith(b"%PDF"):
                    print(f"    ⚠️  Arquivo invalido (nao e PDF) — descartando: {pdf_path.name}")
                    ignorados_binarios += 1
                    continue

                dados = pdf_para_bytes(pdf_path)
                if total_bytes + len(dados) <= LIMITE_BYTES:
                    partes.append(
                        genai_types.Part.from_bytes(
                            data=dados,
                            mime_type="application/pdf",
                        )
                    )
                    total_bytes += len(dados)
                    pdfs_binarios += 1
                    print(f"    📎 PDF binário: {pdf_path.name} ({len(dados)//1024}KB)")
                else:
                    ignorados_binarios += 1
                    print(f"    ⚠️  Limite atingido — pulando: {pdf_path.name}")
            except Exception as e:
                print(f"    ⚠️  Falha ao processar {pdf_path.name}: {e}")

        if ignorados_binarios:
            print(f"    ℹ️  {ignorados_binarios} PDF(s) escaneado(s) ignorado(s) — limite de {MAX_PDFS_BINARIOS} binários atingido")

    print(
        f"  🤖 Gemini: {len(textos):,} chars texto HTML"
        f" + {chars_de_pdfs:,} chars de PDFs pesquisáveis"
        f" + {pdfs_binarios} PDF(s) escaneado(s)"
        f" — processo {numero_processo}..."
    )

    for tentativa in range(1, 4):
        try:
            resposta = cliente_gemini.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=partes,
                config=genai_types.GenerateContentConfig(temperature=0.1),
            )
            return resposta.text

        except Exception as e:
            msg = str(e)
            # Extrai o tempo de espera sugerido pelo Gemini (ex: "retry in 53.6s")
            match = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)", msg, re.IGNORECASE)
            espera = int(float(match.group(1))) + 5 if match else 30 * tentativa

            eh_transitorio = (
                "429" in msg
                or "503" in msg
                or "502" in msg
                or "500" in msg
                or "RESOURCE_EXHAUSTED" in msg
                or "quota" in msg.lower()
                or "unavailable" in msg.lower()
                or "overloaded" in msg.lower()
            )
            if eh_transitorio:
                print(f"  ⏳ Gemini indisponível ({msg[:80]}). Aguardando {espera}s... ({tentativa}/3)")
                time.sleep(espera)
                continue
            else:
                return f"[Erro ao chamar Gemini: {e}]"

    return "[Erro: Gemini indisponível após 3 tentativas.]"


# ══════════════════════════════════════════════════════════════
#  HELPERS DE SAÍDA
# ══════════════════════════════════════════════════════════════

def _md_para_html(texto: str) -> str:
    """Converte markdown simples para HTML (negrito, parágrafos, listas)."""
    import re as _re
    # Negrito
    texto = _re.sub(r"\*\*(.+?)\*\*", r"<strong></strong>", texto)
    # Itálico
    texto = _re.sub(r"\*(.+?)\*", r"<em></em>", texto)
    # Quebras de linha duplas → parágrafos
    partes = [p.strip() for p in texto.split("\n\n") if p.strip()]
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in partes)


def _extrair_dados_de_minuta(html_path) -> dict:
    """
    Lê uma minuta HTML já salva e extrai os dados estruturados
    para popular a planilha sem precisar chamar o Gemini novamente.
    """
    import re as _re
    try:
        texto = Path(html_path).read_text(encoding="utf-8")
        dados = {}
        # Extrai campos do bloco de dados estruturados que está no HTML
        # HTML usa ficha-label / ficha-valor
        campos = {
            "parte_autora": "Parte Autora",
            "parte_re":     "Parte R",
            "relator":      "Relator",
            "sessao":       "Sess",
            "tipo_acao":    "Tipo de A",
            "acompanhamento": "Acompanhamento",
            "resumo":       "Resumo",
        }
        for campo, label in campos.items():
            pattern = (
                rf'ficha-label[^>]*>[^<]*{_re.escape(label)}[^<]*</div>'
                rf'\s*<div[^>]*ficha-valor[^>]*>(.*?)</div>'
            )
            m = _re.search(pattern, texto, _re.IGNORECASE | _re.DOTALL)
            if m:
                valor = _re.sub(r"<[^>]+>", "", m.group(1)).strip()
                dados[campo] = valor
        return dados
    except Exception:
        return {}


def _gerar_html_minuta(dados_ia: dict, minuta: str, nup: str) -> str:
    """Gera arquivo HTML estilizado para a análise de pauta."""
    d = dados_ia
    cnj        = d.get("cnj") or nup
    tipo       = d.get("tipo_acao") or "N/D"
    autora     = d.get("parte_autora") or "N/D"
    re_        = d.get("parte_re") or "N/D"
    relator    = d.get("relator") or "N/D"
    sessao     = d.get("sessao") or "N/D"
    acomp      = d.get("acompanhamento") or "N/D"
    resumo     = d.get("resumo") or ""
    acomp_cor  = "#16a34a" if acomp.upper() == "SIM" else "#dc2626"
    acomp_bg   = "#dcfce7" if acomp.upper() == "SIM" else "#fee2e2"
    acomp_icon = "✅" if acomp.upper() == "SIM" else "❌"
    corpo_html = _md_para_html(minuta)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{cnj}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Merriweather:ital,wght@0,400;0,700;1,400&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
  :root {{
    --verde-agu: #1a4731;
    --verde-claro: #2d6a4f;
    --dourado: #b8860b;
    --cinza: #f4f4f0;
    --texto: #1a1a1a;
    --borda: #d1d5db;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'IBM Plex Sans', sans-serif;
    background: var(--cinza);
    color: var(--texto);
    min-height: 100vh;
    padding: 0;
  }}
  .topo {{
    background: var(--verde-agu);
    color: white;
    padding: 18px 40px;
    display: flex;
    align-items: center;
    gap: 16px;
    border-bottom: 4px solid var(--dourado);
  }}
  .topo-brasao {{ font-size: 2rem; }}
  .topo-titulo {{ font-size: 0.78rem; letter-spacing: 0.12em; text-transform: uppercase; opacity: 0.85; }}
  .topo-subtitulo {{ font-size: 1.1rem; font-weight: 600; margin-top: 2px; }}
  .container {{ max-width: 960px; margin: 36px auto; padding: 0 24px 60px; }}
  .badge-acomp {{
    display: inline-flex; align-items: center; gap: 8px;
    background: {acomp_bg}; color: {acomp_cor};
    border: 1.5px solid {acomp_cor}; border-radius: 8px;
    padding: 10px 20px; font-weight: 600; font-size: 1rem;
    margin-bottom: 24px;
  }}
  .resumo-tag {{
    display: inline-block; background: #e0e7ef; color: #1e3a5f;
    border-radius: 6px; padding: 6px 14px; font-size: 0.85rem;
    font-style: italic; margin-bottom: 28px;
  }}
  .ficha {{
    background: white; border: 1px solid var(--borda);
    border-radius: 12px; overflow: hidden; margin-bottom: 32px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.07);
  }}
  .ficha-header {{
    background: var(--verde-agu); color: white;
    padding: 14px 24px; font-size: 0.72rem;
    letter-spacing: 0.1em; text-transform: uppercase; font-weight: 600;
  }}
  .ficha-body {{ padding: 0; }}
  .ficha-row {{
    display: grid; grid-template-columns: 180px 1fr;
    border-bottom: 1px solid var(--borda);
  }}
  .ficha-row:last-child {{ border-bottom: none; }}
  .ficha-label {{
    padding: 12px 18px; font-size: 0.8rem; font-weight: 600;
    color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em;
    background: #fafaf8; border-right: 1px solid var(--borda);
  }}
  .ficha-valor {{ padding: 12px 18px; font-size: 0.95rem; line-height: 1.5; }}
  .secao {{ margin-bottom: 32px; }}
  .secao-titulo {{
    font-family: 'Merriweather', serif;
    font-size: 1rem; font-weight: 700; color: var(--verde-agu);
    text-transform: uppercase; letter-spacing: 0.08em;
    border-left: 4px solid var(--dourado);
    padding-left: 12px; margin-bottom: 16px;
  }}
  .corpo-texto {{
    background: white; border: 1px solid var(--borda);
    border-radius: 12px; padding: 28px 32px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.07);
  }}
  .corpo-texto p {{
    font-family: 'Merriweather', serif;
    font-size: 0.97rem; line-height: 1.85;
    margin-bottom: 16px; text-align: justify;
  }}
  .corpo-texto p:last-child {{ margin-bottom: 0; }}
  .corpo-texto strong {{ color: var(--verde-agu); }}
  .rodape {{
    text-align: center; font-size: 0.75rem; color: #9ca3af;
    margin-top: 40px; padding-top: 20px;
    border-top: 1px solid var(--borda);
  }}
  @media print {{
    .topo {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    body {{ background: white; }}
    .ficha-header {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  }}
</style>
</head>
<body>
<div class="topo">
  <div class="topo-brasao">⚖️</div>
  <div>
    <div class="topo-titulo">Advocacia-Geral da União · Análise de Pauta</div>
    <div class="topo-subtitulo">{cnj}</div>
  </div>
</div>
<div class="container">

  <div class="badge-acomp">{acomp_icon} Acompanhamento Especial: {acomp}</div>
  {"<div class='resumo-tag'>📌 " + resumo + "</div>" if resumo else ""}

  <div class="ficha">
    <div class="ficha-header">Dados Processuais</div>
    <div class="ficha-body">
      <div class="ficha-row"><div class="ficha-label">NUP</div><div class="ficha-valor">{nup}</div></div>
      <div class="ficha-row"><div class="ficha-label">Nº CNJ</div><div class="ficha-valor">{cnj}</div></div>
      <div class="ficha-row"><div class="ficha-label">Tipo de Ação</div><div class="ficha-valor">{tipo}</div></div>
      <div class="ficha-row"><div class="ficha-label">Parte Autora</div><div class="ficha-valor">{autora}</div></div>
      <div class="ficha-row"><div class="ficha-label">Parte Ré</div><div class="ficha-valor">{re_}</div></div>
      <div class="ficha-row"><div class="ficha-label">Relator</div><div class="ficha-valor">{relator}</div></div>
      <div class="ficha-row"><div class="ficha-label">Sessão</div><div class="ficha-valor">{sessao}</div></div>
    </div>
  </div>

  <div class="secao">
    <div class="secao-titulo">Análise</div>
    <div class="corpo-texto">{corpo_html}</div>
  </div>

  <div class="rodape">
    Gerado automaticamente pelo Robô Sapiens · AGU · {__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')}
  </div>
</div>
</body>
</html>"""


def _gerar_html_dashboard(resultados: list, tempo_str: str) -> str:
    """Gera dashboard HTML interativo com filtros e ordenação de colunas."""
    total       = len(resultados)
    total_ok    = sum(1 for r in resultados if r.get("minuta_ok"))
    total_acomp = sum(1 for r in resultados if (r.get("dados_ia", {}).get("acompanhamento") or "").upper() == "SIM")
    data_exec   = __import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')

    linhas = ""
    for r in resultados:
        d        = r.get("dados_ia", {})
        cnj      = (d.get("cnj") or r.get("cnj") or "").replace('"', '&quot;')
        nup      = (d.get("nup") or r.get("nup") or "").replace('"', '&quot;')
        tipo     = (d.get("tipo_acao") or "").replace('"', '&quot;')
        autora   = (d.get("parte_autora") or "").replace('"', '&quot;')
        re_      = (d.get("parte_re") or "").replace('"', '&quot;')
        relator  = (d.get("relator") or "").replace('"', '&quot;')
        sessao   = (d.get("sessao") or "").replace('"', '&quot;')
        resumo   = (d.get("resumo") or "").replace('"', '&quot;')
        acomp    = (d.get("acompanhamento") or "").upper()
        ok       = r.get("minuta_ok", False)
        arquivo  = r.get("arquivo", "")
        link_rel = os.path.basename(arquivo) if arquivo else ""

        acomp_badge = '<span class="badge sim">✅ SIM</span>' if acomp == "SIM" else '<span class="badge nao">❌ NÃO</span>'
        status_badge = '<span class="badge ok">Gerada</span>' if ok else '<span class="badge erro">Falha</span>'
        link_html = f'<a href="minutas/{link_rel}" target="_blank">📄 Abrir</a>' if link_rel else "—"
        data_minuta = f"minutas/{link_rel}" if link_rel else ""

        linhas += f"""<tr data-minuta="{data_minuta}">
          <td style="text-align:center;width:36px"><input type="checkbox" class="sel-row" {'disabled' if not link_rel else ''}></td>
          <td data-val="{cnj}"><span class="cnj">{cnj}</span></td>
          <td data-val="{nup}">{nup}</td>
          <td data-val="{tipo}">{tipo}</td>
          <td data-val="{autora}">{autora}</td>
          <td data-val="{re_}">{re_}</td>
          <td data-val="{relator}">{relator}</td>
          <td data-val="{sessao}">{sessao}</td>
          <td data-val="{resumo}" class="resumo">{resumo}</td>
          <td data-val="{acomp}">{acomp_badge}</td>
          <td data-val="{'ok' if ok else 'falha'}">{status_badge}</td>
          <td>{link_html}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Análise de Pauta — AGU</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
  :root {{ --verde:#1a4731; --dourado:#b8860b; --cinza:#f4f4f0; --borda:#d1d5db; --texto:#1a1a1a; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'IBM Plex Sans',sans-serif; background:var(--cinza); color:var(--texto); }}
  .topo {{ background:var(--verde); color:white; padding:18px 40px; border-bottom:4px solid var(--dourado); display:flex; justify-content:space-between; align-items:center; }}
  .topo-esq {{ display:flex; align-items:center; gap:14px; }}
  .topo-titulo {{ font-size:0.72rem; letter-spacing:0.12em; text-transform:uppercase; opacity:0.8; }}
  .topo-subtitulo {{ font-size:1.15rem; font-weight:700; }}
  .topo-dir {{ text-align:right; font-size:0.8rem; opacity:0.75; }}
  .container {{ max-width:1400px; margin:28px auto; padding:0 24px 60px; }}
  .cards {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:24px; }}
  .card {{ background:white; border:1px solid var(--borda); border-radius:12px; padding:20px 24px; box-shadow:0 1px 4px rgba(0,0,0,0.07); }}
  .card-num {{ font-size:2.6rem; font-weight:700; color:var(--verde); line-height:1; }}
  .card-label {{ font-size:0.78rem; text-transform:uppercase; letter-spacing:0.08em; color:#6b7280; margin-top:6px; }}
  .card.destaque .card-num {{ color:#dc2626; }}
  .toolbar {{ display:flex; gap:12px; margin-bottom:14px; align-items:center; flex-wrap:wrap; }}
  .toolbar input {{ flex:1; min-width:220px; padding:9px 14px; border:1px solid var(--borda); border-radius:8px; font-size:0.9rem; font-family:inherit; outline:none; }}
  .toolbar input:focus {{ border-color:var(--verde); box-shadow:0 0 0 3px rgba(26,71,49,0.1); }}
  .toolbar select {{ padding:9px 12px; border:1px solid var(--borda); border-radius:8px; font-size:0.85rem; font-family:inherit; background:white; cursor:pointer; }}
  .contador {{ font-size:0.82rem; color:#6b7280; white-space:nowrap; }}
  .tablewrap {{ overflow-x:auto; border-radius:12px; box-shadow:0 1px 4px rgba(0,0,0,0.07); }}
  table {{ width:100%; border-collapse:collapse; background:white; min-width:900px; }}
  thead {{ background:var(--verde); color:white; position:sticky; top:0; z-index:2; }}
  th {{ padding:12px 14px; text-align:left; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.07em; font-weight:600; white-space:nowrap; cursor:pointer; user-select:none; }}
  th:hover {{ background:rgba(255,255,255,0.1); }}
  th .sort-icon {{ margin-left:4px; opacity:0.5; font-style:normal; }}
  th.asc .sort-icon::after {{ content:"▲"; opacity:1; }}
  th.desc .sort-icon::after {{ content:"▼"; opacity:1; }}
  th:not(.asc):not(.desc) .sort-icon::after {{ content:"⇅"; }}
  td {{ padding:11px 14px; border-bottom:1px solid var(--borda); font-size:0.87rem; vertical-align:middle; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:#f8faf8; }}
  tr.hidden {{ display:none; }}
  .cnj {{ font-family:'IBM Plex Mono',monospace; font-size:0.8rem; color:#1e3a5f; }}
  .resumo {{ font-style:italic; color:#4b5563; font-size:0.83rem; }}
  .badge {{ display:inline-block; padding:3px 10px; border-radius:6px; font-size:0.76rem; font-weight:600; }}
  .badge.sim {{ background:#dcfce7; color:#16a34a; }}
  .badge.nao {{ background:#fee2e2; color:#dc2626; }}
  .badge.ok  {{ background:#dbeafe; color:#1d4ed8; }}
  .badge.erro {{ background:#fef9c3; color:#854d0e; }}
  a {{ color:var(--verde); text-decoration:none; font-weight:500; }}
  a:hover {{ text-decoration:underline; }}
  .rodape {{ text-align:center; font-size:0.75rem; color:#9ca3af; margin-top:32px; }}
  .btn-abrir {{ background:var(--verde); color:white; border:none; border-radius:8px; padding:9px 18px; font-size:0.88rem; font-weight:600; font-family:inherit; cursor:pointer; white-space:nowrap; transition:opacity 0.15s; }}
  .btn-abrir:hover {{ opacity:0.85; }}
  .btn-abrir:disabled {{ background:#9ca3af; cursor:not-allowed; opacity:1; }}
  input[type=checkbox] {{ width:16px; height:16px; accent-color:var(--verde); cursor:pointer; }}
  input[type=checkbox]:disabled {{ cursor:not-allowed; opacity:0.35; }}
  th.col-sel {{ width:36px; text-align:center; cursor:default; }}
  th.col-sel:hover {{ background:inherit; }}
</style>
</head>
<body>
<div class="topo">
  <div class="topo-esq">
    <span style="font-size:2rem">⚖️</span>
    <div>
      <div class="topo-titulo">Advocacia-Geral da União · Pasta Sju Ntrib</div>
      <div class="topo-subtitulo">Análise de Pauta</div>
    </div>
  </div>
  <div class="topo-dir">⏱ {tempo_str}<br>{data_exec}</div>
</div>
<div class="container">
  <div class="cards">
    <div class="card"><div class="card-num">{total}</div><div class="card-label">Processos na Pauta</div></div>
    <div class="card"><div class="card-num">{total_ok}</div><div class="card-label">Análises Geradas</div></div>
    <div class="card destaque"><div class="card-num">{total_acomp}</div><div class="card-label">Acompanhamento Especial</div></div>
  </div>

  <div class="toolbar">
    <input type="text" id="busca" placeholder="🔍 Buscar por CNJ, NUP, partes, resumo..." oninput="filtrar()">
    <select id="filtroAcomp" onchange="filtrar()">
      <option value="">Todos</option>
      <option value="SIM">✅ Acompanhamento SIM</option>
      <option value="NÃO">❌ Acompanhamento NÃO</option>
    </select>
    <select id="filtroStatus" onchange="filtrar()">
      <option value="">Todos os status</option>
      <option value="ok">Análise Gerada</option>
      <option value="falha">Falha</option>
    </select>
    <button class="btn-abrir" id="btn-abrir" onclick="abrirSelecionadas()" disabled>📂 Abrir Minutas Selecionadas (<span id="n-sel">0</span>)</button>
    <span class="contador" id="contador">{total} processo(s)</span>
  </div>

  <div class="tablewrap">
    <table id="tabela">
      <thead>
        <tr>
          <th class="col-sel"><input type="checkbox" id="sel-todos" title="Selecionar todos" onchange="toggleTodos(this)"></th>
          <th onclick="ordenar(1)">Nº CNJ<i class="sort-icon"></i></th>
          <th onclick="ordenar(2)">NUP<i class="sort-icon"></i></th>
          <th onclick="ordenar(3)">Tipo de Ação<i class="sort-icon"></i></th>
          <th onclick="ordenar(4)">Parte Autora<i class="sort-icon"></i></th>
          <th onclick="ordenar(5)">Parte Ré<i class="sort-icon"></i></th>
          <th onclick="ordenar(6)">Relator<i class="sort-icon"></i></th>
          <th onclick="ordenar(7)">Sessão<i class="sort-icon"></i></th>
          <th onclick="ordenar(8)">Resumo<i class="sort-icon"></i></th>
          <th onclick="ordenar(9)">Acompanhamento<i class="sort-icon"></i></th>
          <th onclick="ordenar(10)">Status<i class="sort-icon"></i></th>
          <th>Análise</th>
        </tr>
      </thead>
      <tbody id="corpo">{linhas}</tbody>
    </table>
  </div>
  <div class="rodape">Gerado automaticamente pelo Robô Sapiens · AGU · {data_exec}</div>
</div>
<script>
  let colAtual = -1, dirAtual = 1;

  function ordenar(col) {{
    const tbody = document.getElementById('corpo');
    const ths   = document.querySelectorAll('th');
    if (colAtual === col) {{ dirAtual *= -1; }} else {{ dirAtual = 1; colAtual = col; }}
    ths.forEach(th => th.classList.remove('asc','desc'));
    ths[col].classList.add(dirAtual === 1 ? 'asc' : 'desc');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort((a, b) => {{
      const va = a.cells[col]?.getAttribute('data-val') || '';
      const vb = b.cells[col]?.getAttribute('data-val') || '';
      return va.localeCompare(vb, 'pt', {{numeric:true}}) * dirAtual;
    }});
    rows.forEach(r => tbody.appendChild(r));
    filtrar();
  }}

  function filtrar() {{
    const busca  = document.getElementById('busca').value.toLowerCase();
    const acomp  = document.getElementById('filtroAcomp').value;
    const status = document.getElementById('filtroStatus').value;
    const rows   = document.querySelectorAll('#corpo tr');
    let vis = 0;
    rows.forEach(row => {{
      const texto  = row.innerText.toLowerCase();
      const dAcomp = row.cells[9]?.getAttribute('data-val') || '';
      const dStat  = row.cells[10]?.getAttribute('data-val') || '';
      const ok = (!busca  || texto.includes(busca))
              && (!acomp  || dAcomp === acomp)
              && (!status || dStat  === status);
      row.classList.toggle('hidden', !ok);
      if (ok) vis++;
    }});
    document.getElementById('contador').textContent = vis + ' processo(s)';
    atualizarBotao();
  }}

  function toggleTodos(chk) {{
    document.querySelectorAll('#corpo tr:not(.hidden) .sel-row:not(:disabled)').forEach(cb => {{
      cb.checked = chk.checked;
    }});
    atualizarBotao();
  }}

  function atualizarBotao() {{
    const n = document.querySelectorAll('#corpo .sel-row:checked').length;
    document.getElementById('n-sel').textContent = n;
    document.getElementById('btn-abrir').disabled = n === 0;
  }}

  function abrirSelecionadas() {{
    document.querySelectorAll('#corpo tr').forEach(row => {{
      const cb = row.querySelector('.sel-row');
      if (cb && cb.checked) {{
        const href = row.getAttribute('data-minuta');
        if (href) window.open(href, '_blank');
      }}
    }});
  }}

  document.addEventListener('change', e => {{
    if (e.target.classList.contains('sel-row')) atualizarBotao();
  }});
</script>
</body>
</html>"""

def _extrair_partes(proc_jud: dict) -> str:
    """Monta string 'Polo Ativo x Polo Passivo' a partir do processo judicial."""
    try:
        partes = proc_jud.get("partes") or []
        ativos   = [p.get("nome","") for p in partes if (p.get("polo") or "").upper() == "AT"]
        passivos = [p.get("nome","") for p in partes if (p.get("polo") or "").upper() == "PA"]
        ativo    = ", ".join(filter(None, ativos))   or "N/D"
        passivo  = ", ".join(filter(None, passivos)) or "N/D"
        return f"{ativo} x {passivo}"
    except Exception:
        return "N/D"


def _extrair_bloco(resposta: str, tag_inicio: str, tag_fim: str, tag_fim_alt: str = None) -> str:
    """
    Extrai o conteúdo entre duas tags de bloco na resposta do Gemini.
    Se tag_fim não for encontrada, tenta tag_fim_alt (abertura do próximo bloco),
    para cobrir casos em que o Gemini omite os marcadores de fechamento.
    """
    inicio = resposta.find(tag_inicio)
    if inicio == -1:
        return ""
    conteudo_inicio = inicio + len(tag_inicio)
    fim = resposta.find(tag_fim, conteudo_inicio)
    if fim == -1 and tag_fim_alt:
        fim = resposta.find(tag_fim_alt, conteudo_inicio)
    if fim == -1:
        return ""
    return resposta[conteudo_inicio:fim].strip()


def _parsear_resposta_gemini(resposta: str) -> dict:
    """
    Extrai o bloco de dados estruturados e as seções narrativas geradas pelo Gemini.
    """
    dados = {
        "nup": "", "cnj": "",
        "nome_recorrente": "", "nome_recorrido": "",
        "nome_relator": "", "dados_pauta": "",
        "tipo_originario": "", "tema_identificado": "",
        "tese_precedente": "", "decisao_recorrida": "",
        "docs_suficientes_ia": "SIM",
        # seções narrativas
        "ementa": "", "relatorio": "", "fundamentacao": "", "conclusao": "",
    }
    try:
        inicio = resposta.find("---DADOS_ESTRUTURADOS---")
        fim    = resposta.find("---FIM_DADOS---")
        if inicio == -1 or fim == -1:
            return dados
        bloco = resposta[inicio + len("---DADOS_ESTRUTURADOS---"):fim].strip()
        mapa = {
            "NOME_RECORRENTE":  "nome_recorrente",
            "NOME_RECORRIDO":   "nome_recorrido",
            "NOME_RELATOR":     "nome_relator",
            "DADOS_PAUTA":      "dados_pauta",
            "TIPO_ORIGINARIO":   "tipo_originario",
            "TEMA_IDENTIFICADO": "tema_identificado",
            "TESE_PRECEDENTE":   "tese_precedente",
            "DECISAO_RECORRIDA": "decisao_recorrida",
            "DOCS_SUFICIENTES":  "docs_suficientes_ia",
        }
        for linha in bloco.splitlines():
            if ":" not in linha:
                continue
            chave, _, valor = linha.partition(":")
            chave = chave.strip()
            valor = valor.strip()
            if chave in mapa:
                dados[mapa[chave]] = valor
    except Exception:
        pass

    # Extrai seções narrativas (tag_fim_alt = próximo bloco, caso Gemini omita o fechamento)
    dados["ementa"]        = _extrair_bloco(resposta, "---EMENTA---",        "---FIM_EMENTA---",        "---RELATORIO---")
    dados["relatorio"]     = _extrair_bloco(resposta, "---RELATORIO---",     "---FIM_RELATORIO---",     "---FUNDAMENTACAO---")
    dados["fundamentacao"] = _extrair_bloco(resposta, "---FUNDAMENTACAO---", "---FIM_FUNDAMENTACAO---", "---CONCLUSAO---")
    dados["conclusao"]     = _extrair_bloco(resposta, "---CONCLUSAO---",     "---FIM_CONCLUSAO---")

    return dados


def _extrair_relatorio(resposta: str) -> str:
    """Extrai o bloco RELATORIO da resposta do Gemini (usado para checar minuta_ok)."""
    bloco = _extrair_bloco(resposta, "---RELATORIO---", "---FIM_RELATORIO---")
    if bloco:
        return bloco
    # Fallback: remove o bloco de dados estruturados se presente e retorna o resto
    fim_dados = resposta.find("---FIM_DADOS---")
    if fim_dados != -1:
        return resposta[fim_dados + len("---FIM_DADOS---"):].strip()
    return ""



# ══════════════════════════════════════════════════════════════
#  INTEGRAÇÃO SAPIENS — ESCRITA (observações + minutas)
# ══════════════════════════════════════════════════════════════

def _buscar_tarefa_completa(client: SapiensClient, id_tarefa: str) -> dict:
    """
    Busca o objeto completo de uma tarefa pelo ID.
    Necessário para PUT (que exige o objeto inteiro).
    """
    url = f"{BASE_URL}/v1/administrativo/tarefa/{id_tarefa}?populate=%5B%5D"
    try:
        return client.get(url)
    except Exception as e:
        print(f"    ⚠️  Não foi possível buscar tarefa {id_tarefa}: {e}")
        return {}


def atualizar_observacao_tarefa(
    client: SapiensClient,
    id_tarefa: str,
    texto_observacao: str,
) -> bool:
    """
    Atualiza o campo 'observacao' de uma tarefa no Sapiens.

    Tenta 3 estratégias em ordem:
      1. PATCH com apenas {"observacao": ...}
      2. PATCH com {"observacao": ..., "id": id_tarefa}
      3. PUT com o objeto completo da tarefa atualizado

    Loga o resultado de cada tentativa para diagnóstico.
    Retorna True se alguma estratégia funcionou.
    """
    if DRY_RUN:
        print(f"  [DRY-RUN] Observação suprimida: {texto_observacao[:80]}")
        return True
    if not id_tarefa:
        print("    ⚠️  id_tarefa ausente — não foi possível atualizar observação.")
        return False

    url = f"{BASE_URL}/v1/administrativo/tarefa/{id_tarefa}"

    # ── Estratégia 1: PATCH mínimo ──────────────────────────
    print(f"    📝 [Obs] Tentativa 1 — PATCH mínimo...")
    r1 = client.patch(url, {"observacao": texto_observacao})
    status1 = r1.get("_status") or (200 if "id" in r1 else None)
    print(f"         Status: {r1.get('_status', 'OK')} | Resposta: {str(r1)[:150]}")
    if "_status" not in r1 or r1.get("_status") in (200, 201, 204):
        if "_error" not in r1:
            logging.info("Observação atualizada via PATCH mínimo.")
            return True

    # ── Estratégia 2: PATCH com id explícito ────────────────
    print(f"    📝 [Obs] Tentativa 2 — PATCH com id...")
    r2 = client.patch(url, {"id": int(id_tarefa), "observacao": texto_observacao})
    print(f"         Status: {r2.get('_status', 'OK')} | Resposta: {str(r2)[:150]}")
    if "_status" not in r2 or r2.get("_status") in (200, 201, 204):
        if "_error" not in r2:
            logging.info("Observação atualizada via PATCH com id.")
            return True

    # ── Estratégia 3: PUT com objeto completo ───────────────
    print(f"    📝 [Obs] Tentativa 3 — GET + PUT completo...")
    tarefa_completa = _buscar_tarefa_completa(client, id_tarefa)
    if tarefa_completa:
        tarefa_completa["observacao"] = texto_observacao
        r3 = client.put(url, tarefa_completa)
        print(f"         Status: {r3.get('_status', 'OK')} | Resposta: {str(r3)[:150]}")
        if "_status" not in r3 or r3.get("_status") in (200, 201, 204):
            if "_error" not in r3:
                logging.info("Observação atualizada via PUT completo.")
                return True
    else:
        print(f"         ❌ Não foi possível buscar tarefa para PUT.")

    print(f"    ❌ Nenhuma estratégia funcionou para atualizar observação da tarefa {id_tarefa}.")
    print(f"       ➡  Passe o log acima para ajuste do endpoint.")
    return False


def _montar_texto_observacao(dados_ia: dict, docs_suficientes: bool, ente: str = "") -> str:
    """
    Monta o texto da observação que será inserido na tarefa.

    Formato:
      ROBÔ SAPIENS - {TIPO_ORIGINARIO} // {ENTE} // TEMA: {TEMA} | {DECISAO_RECORRIDA} | TESE: {tese} //
      (TESE incluída apenas se localizada nos documentos)
    Ou, se sem documentos:
      ROBÔ SAPIENS - SEM DOCUMENTOS //
    """
    if not docs_suficientes:
        return "ROBÔ SAPIENS - SEM DOCUMENTOS //"

    def _val(campo: str) -> str:
        """Retorna o valor ou string vazia se for 'não localizado'."""
        v = (dados_ia.get(campo) or "").strip()
        if v.lower().startswith("não localizado") or v.lower().startswith("nao localizado"):
            return ""
        return v.replace("—", "-").replace("–", "-")

    tipo_orig         = _val("tipo_originario")
    tema              = _val("tema_identificado")
    tese              = _val("tese_precedente")
    decisao_recorrida = _val("decisao_recorrida")

    prefixo    = f"ROBÔ SAPIENS - {tipo_orig}" if tipo_orig else "ROBÔ SAPIENS"
    ente_parte = f" // {ente}" if ente else ""
    tema_parte = f" // TEMA: {tema}" if tema else ""

    partes = [prefixo + ente_parte + tema_parte]

    if decisao_recorrida:
        partes.append(decisao_recorrida)

    if tese:
        partes.append(f"TESE: {tese}")

    return " | ".join(partes) + " //"



def _extrair_html_do_conteudo(conteudo_data_uri: str) -> str:
    """
    Decodifica o campo 'conteudo' (data URI base64) retornado pelo Sapiens
    e devolve o HTML puro como string.
    """
    if not conteudo_data_uri:
        return ""
    try:
        if ";base64," in conteudo_data_uri:
            b64 = conteudo_data_uri.split(";base64,", 1)[1]
            return base64.b64decode(b64).decode("utf-8", errors="replace")
        if conteudo_data_uri.startswith("data:") and "," in conteudo_data_uri:
            return conteudo_data_uri.split(",", 1)[1]
    except Exception:
        pass
    return ""


def _relatorio_para_html(texto: str) -> str:
    """
    Converte o texto do relatório gerado pela IA em blocos HTML do CKEditor.

    Regras:
      - Linhas com **Texto** ou *Texto* → <h1>Texto</h1>  (título CKEditor)
      - Linhas vazias             → <p>&nbsp;</p>
      - Demais linhas             → <p class="numerado">Texto</p>

    Remove asteriscos de negrito/itálico residuais no texto comum.
    """
    import re as _re
    blocos = []
    for linha in texto.strip().split("\n"):
        linha_stripped = linha.strip()

        # Linha em branco
        if not linha_stripped:
            blocos.append("<p>&nbsp;</p>")
            continue

        # Título: **Texto** ou apenas linha toda em maiúsculas após **
        m_titulo = _re.match(r"^\*{1,2}(.+?)\*{1,2}$", linha_stripped)
        if m_titulo:
            titulo = m_titulo.group(1).strip()
            blocos.append(f"<h1>{titulo}</h1>")
            continue

        # Linha comum: remove asteriscos residuais e formata como parágrafo simples
        linha_limpa = _re.sub(r"\*+", "", linha_stripped).strip()
        if linha_limpa:
            # Converte marcadores de destaque em negrito com fundo amarelo
            # Suporta: <<ALERTA>>texto<</ALERTA>> e !!DESTAQUE!!texto!!FIM!!
            linha_limpa = _re.sub(
                r'<<ALERTA>>(.*?)<</ALERTA>>',
                r'<strong><span style="background-color: #FFFF00;">\1</span></strong>',
                linha_limpa
            )
            linha_limpa = _re.sub(
                r'!!DESTAQUE!!(.*?)!!FIM!!',
                r'<strong><span style="background-color: #FFFF00;">\1</span></strong>',
                linha_limpa
            )
            blocos.append(f"<p>{linha_limpa}</p>")

    return "\n".join(blocos)


def _injetar_relatorio_no_modelo(html_modelo: str, texto_relatorio: str) -> str:
    """
    Injeta o texto do relatório no HTML do modelo do Sapiens,
    substituindo o marcador 'Em branco...' pelo conteúdo gerado pela IA.

    Preserva integralmente: cabeçalho, número do documento, NUP,
    Interessados, Assuntos, data, assinatura e rodapé.
    Remove o bloco EMENTA do modelo.

    Se o marcador não for encontrado, insere antes do </body>.
    """
    import re as _re

    # Remove o bloco EMENTA (blockquote ou parágrafo)
    html_modelo = _re.sub(
        r'<blockquote[^>]*>\s*EMENTA:?\s*</blockquote>',
        '', html_modelo, flags=_re.IGNORECASE
    )
    # Também remove se vier como parágrafo
    html_modelo = _re.sub(
        r'<p[^>]*>\s*EMENTA:?\s*</p>',
        '', html_modelo, flags=_re.IGNORECASE
    )

    conteudo_html = _relatorio_para_html(texto_relatorio)

    # Tenta substituir o parágrafo "Em branco..."
    padrao = _re.compile(
        r'<p[^>]*class=["\']numerado["\'][^>]*>\s*Em\s+branco\.{0,3}\s*</p>',
        _re.IGNORECASE
    )
    if padrao.search(html_modelo):
        html_final = padrao.sub(conteudo_html, html_modelo, count=1)
        print(f"    📌 Marcador 'Em branco...' encontrado e substituído.")
        return html_final

    # Fallback: insere antes do </body>
    print(f"    ⚠️  Marcador 'Em branco...' não encontrado — inserindo antes de </body>.")
    if "</body>" in html_modelo:
        return html_modelo.replace("</body>", conteudo_html + "\n</body>", 1)

    return html_modelo + "\n" + conteudo_html


def criar_minuta_na_tarefa(
    client: SapiensClient,
    id_tarefa: str,
    texto_relatorio: str,
    dados_ia: dict,
) -> bool:
    """
    Cria um ComponenteDigital de minuta na tarefa e insere o texto do relatório.

    Fluxo confirmado via DevTools:
      1. POST /componente_digital  → cria o componente com modelo=22281, conteudo=null
         Retorna o objeto com id do componente criado.
      2. PATCH /componente_digital/{id}  → envia conteudo em data URI HTML base64
         (mesmo formato do autosave do CKEditor capturado no Network)
    """
    if DRY_RUN:
        print(f"  [DRY-RUN] Minuta suprimida para tarefa {id_tarefa}.")
        return True
    if not id_tarefa:
        print("    ⚠️  id_tarefa ausente — não foi possível criar minuta.")
        return False

    # Garante que dados_ia["relatorio"] tem conteúdo — fallback: texto_relatorio passado
    if not dados_ia.get("relatorio") and texto_relatorio:
        dados_ia = dict(dados_ia)  # cópia para não mutar o original
        dados_ia["relatorio"] = texto_relatorio
    if not dados_ia.get("relatorio"):
        print("    ⚠️  Relatório vazio — minuta não criada.")
        return False

    # ── Passo 1: cria o componente com o modelo (conteudo=null) ─
    payload_post = {
        "fileName":               "MODELO.HTML",
        "hash":                   None,
        "numeracaoSequencial":    None,
        "conteudo":               None,
        "convertidoPdf":          None,
        "dataHoraLockEdicao":     None,
        "dataHoraSoftwareCriacao": None,
        "documento":              None,
        "documentoAvulsoOrigem":  None,
        "documentoOrigem":        None,
        "editavel":               None,
        "extensao":               None,
        "isAttachment":           None,
        "mimetype":               None,
        "modalidadeAlvoInibidor": None,
        "modalidadeTipoInibidor": None,
        "modelo":                 812466,
        "nivelComposicao":        None,
        "processoOrigem":         None,
        "score":                  None,
        "softwareCriacao":        None,
        "tamanho":                None,
        "tarefaOrigem":           int(id_tarefa),
        "tipoDocumento":          None,
        "usernameLockEdicao":     None,
        "versaoEditor":           "ckeditor5",
        "versaoSoftwareCriacao":  None,
        "chaveInibidor":          None,
        "componenteDigitalOrigem": None,
    }

    url_post = f"{BASE_URL}/v1/administrativo/componente_digital?populate=%5B%5D&context=%7B%7D"
    print(f"    📄 [Minuta] Criando componente (modelo=812466, tarefa={id_tarefa})...")
    r_post = client.post(url_post, payload_post)
    print(f"         Resposta: {str(r_post)[:200]}")

    id_comp  = r_post.get("id")
    hash_atual = r_post.get("hash")  # necessário como hashAntigo no PATCH
    if not id_comp:
        print(f"    ❌ Falha ao criar componente (status {r_post.get('_status', '?')}). Minuta não criada.")
        return False

    logging.info(f"Componente criado (id={id_comp}, hash={str(hash_atual)[:16]}...)")

    # ── Passo 2: extrai HTML do modelo e injeta via placeholders ──────
    # O modelo 812466 tem placeholders {{CAMPO}} que substituímos diretamente.
    html_modelo = _extrair_html_do_conteudo(r_post.get("conteudo", ""))

    # Converte seções narrativas para HTML
    relatorio_html     = _relatorio_para_html(dados_ia.get("relatorio", ""))
    fundamentacao_html = _relatorio_para_html(dados_ia.get("fundamentacao", ""))
    conclusao_html     = _relatorio_para_html(dados_ia.get("conclusao", ""))

    # Substitui placeholders
    substituicoes = {
        "{{NUMERO_PROCESSO}}":  str(dados_ia.get("nup") or dados_ia.get("cnj") or ""),
        "{{nome_recorrente}}":  dados_ia.get("nome_recorrente") or "",
        "{{nome_recorrido}}":   dados_ia.get("nome_recorrido") or "",
        "{{nome_relator}}":     dados_ia.get("nome_relator") or "",
        "{{dados_pauta}}":      dados_ia.get("dados_pauta") or "",
        "{{TAG_RELATORIO}}":    relatorio_html,
        "{{TAG_FUNDAMENTACAO}}": fundamentacao_html,
        "{{TAG_CONCLUSÃO}}":    conclusao_html,
        "{{TAG_CONCLUSAO}}":    conclusao_html,  # variante sem acento
    }
    html_final = html_modelo
    for placeholder, valor in substituicoes.items():
        html_final = html_final.replace(placeholder, valor)

    # EMENTA: injeta conteúdo após o texto "EMENTA:" no modelo
    ementa_texto = dados_ia.get("ementa", "").strip()
    if ementa_texto and "EMENTA:" in html_final:
        ementa_html = f" {ementa_texto}"
        html_final = html_final.replace("EMENTA:", f"EMENTA:{ementa_html}", 1)
        print(f"    📌 EMENTA injetada no modelo.")

    conteudo_b64 = base64.b64encode(html_final.encode("utf-8")).decode("ascii")
    data_uri     = f"data:text/html;name=MODELO.HTML;charset=utf-8;base64,{conteudo_b64}"

    patch_payload = {
        "conteudo":     data_uri,
        "hashAntigo":   hash_atual,
        "versaoEditor": "ckeditor5",
    }

    url_patch = f"{BASE_URL}/v1/administrativo/componente_digital/{id_comp}?populate=%5B%22documento%22%5D&context=%7B%7D"
    print(f"    📝 [Minuta] Inserindo texto via PATCH (comp={id_comp})...")
    r_patch = client.patch(url_patch, patch_payload)
    print(f"         Resposta: {str(r_patch)[:200]}")

    if "_error" not in r_patch and r_patch.get("_status") not in (400, 422, 500):
        logging.info("Texto inserido na minuta com sucesso.")
        return True

    print(f"    ❌ Minuta criada mas PATCH do conteúdo falhou (status {r_patch.get('_status', '?')}).")
    print(f"       id_comp={id_comp}")
    return False


def _processar_sapiens_pos_analise(
    client: SapiensClient,
    resultado: dict,
    minuta_texto: str,
) -> None:
    """
    Executa as operações de escrita no Sapiens após a análise de um processo:
      1. Atualiza observação da tarefa
      2. Cria minuta com o texto do relatório

    Recebe 'resultado' (item do array resultados) e 'minuta_texto' (relatório puro).
    Todos os erros são logados mas não interrompem o fluxo — as operações são best-effort.
    """
    id_tarefa      = str(resultado.get("id_tarefa") or "")
    dados_ia       = resultado.get("dados_ia") or {}
    docs_suf       = resultado.get("docs_suficientes", True)
    minuta_ok      = resultado.get("minuta_ok", False)
    num_processo   = resultado.get("nup") or resultado.get("cnj") or id_tarefa
    ente           = resultado.get("ente") or ""
    gemini_falhou  = resultado.get("gemini_falhou", False)

    print(f"\n  🔗 Integrando ao Sapiens: {num_processo}")

    # ── 1. Observação ────────────────────────────────────────
    if gemini_falhou:
        obs_texto = "ROBÔ SAPIENS - FALHA DE COMUNICAÇÃO COM IA //"
    else:
        obs_texto = _montar_texto_observacao(dados_ia, docs_suf, ente)
    print(f"     Observação: {obs_texto}")
    atualizar_observacao_tarefa(client, id_tarefa, obs_texto)

    # ── 2. Minuta ─────────────────────────────────────────────
    if not docs_suf:
        print(f"     ⏭  Sem documentos — minuta não criada.")
        return
    if not minuta_ok or not minuta_texto:
        print(f"     ⏭  Análise incompleta — minuta não criada.")
        return

    criar_minuta_na_tarefa(client, id_tarefa, minuta_texto, dados_ia)


def _salvar_json_processo(resultado: dict) -> None:
    """Salva um arquivo JSON com os dados analisados de um processo."""
    DIR_SAIDA.mkdir(parents=True, exist_ok=True)
    cnj  = resultado.get("cnj") or resultado.get("nup") or str(resultado.get("id_tarefa") or "desconhecido")
    nome = cnj.replace("/", "-").replace(".", "-") + ".json"
    dados = {
        "nup":             str(resultado.get("nup") or ""),
        "cnj":             str(resultado.get("cnj") or ""),
        "ente":            resultado.get("ente") or "",
        "minuta_ok":       resultado.get("minuta_ok", False),
        "docs_suficientes": resultado.get("docs_suficientes", True),
        "id_tarefa":       resultado.get("id_tarefa"),
        "dados_ia":        resultado.get("dados_ia") or {},
    }
    json_path = DIR_SAIDA / nome
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    print(f"  💾 JSON salvo: {json_path.name}")


# ══════════════════════════════════════════════════════════════
#  ORQUESTRADOR PRINCIPAL
# ══════════════════════════════════════════════════════════════

def _iniciar_chrome() -> None:
    """
    Verifica se o Chrome com debugger já está rodando na porta 9222.
    Se não estiver, abre automaticamente e aguarda estar pronto.
    """
    import socket as _socket
    import subprocess as _subprocess

    # Verifica se já está rodando
    try:
        with _socket.create_connection(("127.0.0.1", 9222), timeout=2):
            print("  ✅ Chrome debugger já estava ativo na porta 9222.")
            return
    except (ConnectionRefusedError, OSError):
        pass

    # Abre o Chrome com debugger já na página do Sapiens
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    cmd = [
        chrome_bin,
        "--remote-debugging-port=9222",
        "--user-data-dir=/tmp/sapiens_debug",
        "https://supersapiens.agu.gov.br",
    ]
    print("  🌐 Abrindo Chrome com debugger...")
    _subprocess.Popen(cmd, stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)

    # Aguarda o Chrome ficar disponível (até 15 segundos)
    print("  ⏳ Aguardando Chrome iniciar", end="", flush=True)
    for _ in range(30):
        time.sleep(0.5)
        print(".", end="", flush=True)
        try:
            with _socket.create_connection(("127.0.0.1", 9222), timeout=1):
                print(" ✅")
                return
        except (ConnectionRefusedError, OSError):
            continue

    print()
    raise RuntimeError(
        "❌ Chrome nao ficou disponivel na porta 9222 apos 15s.\n"
        "   Tente abrir manualmente:\n\n"
        f"   {CHROME_CMD}"
    )


def _limpar_cache_antigo(dir_pdfs: Path, dias: int = 15) -> None:
    """Remove PDFs em cache com mais de `dias` dias para conformidade com LGPD."""
    if not dir_pdfs.exists():
        return
    limite = time.time() - dias * 86400
    apagados = 0
    for arquivo in dir_pdfs.iterdir():
        try:
            if arquivo.is_file() and arquivo.stat().st_mtime < limite:
                arquivo.unlink()
                apagados += 1
        except Exception as e:
            print(f"  ⚠️  Falha ao apagar {arquivo.name}: {e}")
    if apagados:
        print(f"  🗑️  LGPD: {apagados} arquivo(s) com mais de {dias} dias removido(s) de {dir_pdfs}")


def main():
    _configurar_log(DIR_BASE)

    print("=" * 60)
    print("  🤖  ROBÔ SAPIENS — AGU Trabalho Zero")
    print("=" * 60)

    # 0. Pré-verificação
    verificar_ambiente()

    # 0.1 Limpeza automática de cache antigo (LGPD: remove PDFs com mais de 15 dias)
    _limpar_cache_antigo(DIR_PDFS)

    # 1. Token: usa env var (modo web) ou captura via Chrome (modo local)
    token_env = os.environ.get("SAPIENS_TOKEN")
    if token_env:
        print("\n🌐 Modo web — usando token fornecido externamente.")
        driver = None
        token  = token_env
    else:
        print("\n🌐 Verificando Chrome...")
        _iniciar_chrome()

        options = Options()
        options.add_experimental_option("debuggerAddress", DEBUGGER_ADDRESS)
        service = Service(ChromeDriverManager().install())
        driver  = webdriver.Chrome(service=service, options=options)

        print("\n" + "─"*60)
        print("  👉 O Chrome foi aberto. Faça login no Sapiens:")
        print(f"     https://supersapiens.agu.gov.br")
        print("─"*60)
        input("\n  Quando estiver logado e a pauta carregada, pressione [ENTER]...\n")

        print("🔑 Capturando token de autenticação...")
        token = aguardar_token(driver)

    # Cliente HTTP silencioso
    client = SapiensClient(token=token, driver=driver)

    # 5. Cliente Gemini
    gemini = configurar_gemini()

    # 6. Pauta
    tarefas   = buscar_tarefas(client)
    resultados = []
    inicio    = time.time()

    for i, tarefa in enumerate(tarefas, 1):
        processo = tarefa.get("processo") or {}

        # O Sapiens retorna o ID dentro do campo "@id": "/v1/administrativo/processo/59631731"
        at_id       = processo.get("@id") or ""
        id_processo = processo.get("id") or (at_id.split("/")[-1] if at_id else None)

        # Número legível: tenta NUP administrativo, depois número do processo judicial
        nup_admin   = processo.get("NUPFormatado") or processo.get("NUP") or ""
        any_dados   = processo.get("any") or {}
        proc_jud    = any_dados.get("processoJudicial") or {}
        num_judicial = proc_jud.get("numeroFormatado") or proc_jud.get("numero") or ""
        num_processo = nup_admin or num_judicial or id_processo

        if not id_processo:
            print(f"\n[{i}] ⚠️  Tarefa sem processo — pulando.")
            # Debug: mostra o que veio para ajudar diagnóstico
            print(f"       Dados da tarefa: {json.dumps(tarefa, ensure_ascii=False)[:300]}")
            continue


        classe_proc  = (proc_jud.get("classeNacional") or {}).get("nome", "")
        num_cnj      = proc_jud.get("numeroFormatado") or proc_jud.get("numero") or ""
        partes_proc  = _extrair_partes(proc_jud)
        pessoa_repr  = (processo.get("pessoaRepresentada") or {})
        ente         = ((pessoa_repr.get("pessoa") or {}).get("nome") or "").strip()

        # Verifica se já existe JSON para este processo — pula se sim
        cnj_safe_check = (num_cnj or str(num_processo)).replace("/", "-").replace(".", "-")
        json_existente = DIR_SAIDA / f"{cnj_safe_check}.json"
        if json_existente.exists():
            print(f"\n[{i}/{len(tarefas)}] ⏭️  {num_processo} — já processado hoje, pulando.")
            try:
                dados_existente = json.loads(json_existente.read_text(encoding="utf-8"))
                resultados.append(dados_existente)
            except Exception:
                pass
            continue

        print(f"\n[{i}/{len(tarefas)}] ⚖️  Processo: {num_processo}")

        # 7. Coleta conteúdo (texto + PDFs) com estratégia por classe

        textos, pdfs, ids_peticao_inicial, grupo_classe = coletar_conteudo_processo(client, str(id_processo), classe_proc, num_cnj=num_cnj)
        print(f"  📊 {len(textos):,} chars de texto + {len(pdfs)} PDF(s)")

        # Avalia se documentos suficientes foram encontrados
        docs_suficientes = bool(textos.strip()) or bool(pdfs)

        # Modo padrão: analisa imediatamente
        resposta_completa = gerar_minuta_parecer(gemini, str(num_processo), textos, pdfs, ids_peticao_inicial=ids_peticao_inicial, grupo_classe=grupo_classe, ente=ente)
        dados_ia  = _parsear_resposta_gemini(resposta_completa)
        minuta    = _extrair_relatorio(resposta_completa)
        minuta_ok = bool(minuta) and not minuta.startswith("[Erro")

        # Detecta falha total do Gemini (erro de comunicação, não falta de documentos)
        gemini_falhou = resposta_completa.startswith("[Erro ao chamar Gemini") or \
                        resposta_completa.startswith("[Erro: Gemini indisponível")

        # Refina docs_suficientes: se Gemini devolveu "sem conteúdo", marca como NÃO
        if "[Sem conteúdo documental" in resposta_completa:
            docs_suficientes = False
        # Sobrescreve com avaliação da IA: se ela concluiu que docs são insuficientes
        if dados_ia.get("docs_suficientes_ia", "SIM").upper().startswith("NÃO") or \
           dados_ia.get("docs_suficientes_ia", "SIM").upper().startswith("NAO"):
            docs_suficientes = False
            minuta_ok = False  # sem docs suficientes não gera minuta
            print(f"  ⚠️  IA sinalizou documentos insuficientes para análise do mérito.")

        resultados.append({
            "nup": num_processo, "cnj": num_cnj,  # sempre do Sapiens JSON
            "partes": partes_proc, "minuta_ok": minuta_ok,
            "arquivo": "", "dados_ia": dados_ia,
            "docs_suficientes": docs_suficientes,
            "id_tarefa": tarefa.get("id"),
            "ente": ente,
            "gemini_falhou": gemini_falhou,
        })

        print(f"\n{'─'*50}")
        print(f"📝 ANÁLISE — {num_processo}")
        print(f"{'─'*50}")
        print(minuta[:800] + ("..." if len(minuta) > 800 else ""))

        if not minuta_ok:
            print(f"  ⚠️  Análise não gerada para {num_processo}")

        # ── Salva JSON do processo ────────────────────────────
        _salvar_json_processo(resultados[-1])

        # ── Integração Sapiens: observação + minuta ──────
        _processar_sapiens_pos_analise(client, resultados[-1], minuta)

    # ── Resumo final ────────────────────────────────────────────
    duracao     = time.time() - inicio
    horas       = int(duracao // 3600)
    minutos     = int((duracao % 3600) // 60)
    segundos    = int(duracao % 60)
    tempo_str   = f"{horas}h {minutos}min {segundos}s" if horas else f"{minutos}min {segundos}s"

    total_ok    = sum(1 for r in resultados if r["minuta_ok"])
    total_nok   = len(resultados) - total_ok

    print(f"\n{'═'*60}")
    print(f"  ✅ ROBÔ SAPIENS — EXECUÇÃO CONCLUÍDA")
    print(f"{'═'*60}")
    print(f"  📋 Processos analisados : {len(resultados)}/{len(tarefas)}")
    print(f"  📝 Minutas geradas      : {total_ok}")
    print(f"  ❌ Falhas               : {total_nok}")
    print(f"  ⏱️  Tempo total          : {tempo_str}")
    print(f"  📁 Pasta de saída       : {DIR_SAIDA.resolve()}")
    print(f"{'═'*60}")

    return resultados


def reprocessar(nup_ou_cnj: str, limpar_cache: bool = False) -> None:
    """
    Reprocessa um processo específico pelo NUP ou CNJ.
    Útil quando a análise falhou ou você quer regerar com novo prompt.
    Uso: python3 robo_sapiens.py 00672.199657/2026-50
         python3 robo_sapiens.py 0000477-67.2005.4.01.3303
    """
    import sys
    busca = nup_ou_cnj.strip()

    print("=" * 60)
    print("  🔄  ROBÔ SAPIENS — REPROCESSAMENTO")
    print(f"  🔍  Buscando: {busca}")
    print("=" * 60)

    verificar_ambiente()

    # Token: usa env var (modo web) ou captura via Chrome (modo local)
    token_env = os.environ.get("SAPIENS_TOKEN")
    if token_env:
        print("\n🌐 Modo web — usando token fornecido externamente.")
        driver = None
        token  = token_env
    else:
        print("\n🌐 Verificando Chrome...")
        _iniciar_chrome()

        options = Options()
        options.add_experimental_option("debuggerAddress", DEBUGGER_ADDRESS)
        service = Service(ChromeDriverManager().install())
        driver  = webdriver.Chrome(service=service, options=options)

        print("\n" + "─"*60)
        print("  👉 Faça login no Sapiens se necessário.")
        print("─"*60)
        input("\n  Pressione [ENTER] quando estiver pronto...\n")

        print("🔑 Capturando token...")
        token = aguardar_token(driver)

    client = SapiensClient(token=token, driver=driver)
    gemini = configurar_gemini()

    # Busca todas as tarefas e encontra o processo pelo NUP ou CNJ
    tarefas = buscar_tarefas(client)
    tarefa_alvo = None

    for tarefa in tarefas:
        processo    = tarefa.get("processo") or {}
        nup_admin   = processo.get("NUPFormatado") or processo.get("NUP") or ""
        any_dados   = processo.get("any") or {}
        proc_jud    = any_dados.get("processoJudicial") or {}
        num_cnj     = proc_jud.get("numeroFormatado") or proc_jud.get("numero") or ""

        # Busca flexível: ignora pontuação e maiúsculas/minúsculas
        def _normalizar(s):
            return re.sub(r"[^0-9a-zA-Z]", "", s).lower()

        if _normalizar(busca) in (_normalizar(nup_admin), _normalizar(num_cnj)):
            tarefa_alvo = tarefa
            break

    if not tarefa_alvo:
        print(f"\n❌ Processo '{busca}' não encontrado na pauta.")
        print("   Verifique o NUP ou CNJ e tente novamente.")
        sys.exit(1)

    # Reprocessa o processo encontrado
    processo    = tarefa_alvo.get("processo") or {}
    at_id       = processo.get("@id") or ""
    id_processo = processo.get("id") or (at_id.split("/")[-1] if at_id else None)
    nup_admin   = processo.get("NUPFormatado") or processo.get("NUP") or ""
    any_dados   = processo.get("any") or {}
    proc_jud    = any_dados.get("processoJudicial") or {}
    num_cnj     = proc_jud.get("numeroFormatado") or proc_jud.get("numero") or ""
    num_processo = nup_admin or num_cnj or id_processo

    print(f"\n✅ Processo encontrado: {num_processo}")
    print(f"   CNJ: {num_cnj}")

    # Limpa cache do processo se solicitado
    if limpar_cache and id_processo:
        import shutil as _shutil
        apagados = 0
        for f in DIR_PDFS.glob(f"proc{id_processo}_*"):
            f.unlink()
            apagados += 1
        if apagados:
            print(f"  🗑️  {apagados} arquivo(s) de cache apagado(s) para o processo {id_processo}")
        else:
            print(f"  ℹ️  Nenhum cache encontrado para o processo {id_processo}")

    print(f"\n⬇️  Coletando documentos...")
    classe_proc  = (proc_jud.get("classeNacional") or {}).get("nome", "")
    pessoa_repr  = (processo.get("pessoaRepresentada") or {})
    ente_rep     = ((pessoa_repr.get("pessoa") or {}).get("nome") or "").strip()
    textos, pdfs, ids_peticao_inicial, grupo_classe = coletar_conteudo_processo(client, str(id_processo), classe_proc, num_cnj=num_cnj)
    print(f"  📊 {len(textos):,} chars + {len(pdfs)} PDF(s)")

    print(f"\n🤖 Enviando ao Gemini...")
    resposta_completa = gerar_minuta_parecer(gemini, str(num_processo), textos, pdfs, ids_peticao_inicial=ids_peticao_inicial, grupo_classe=grupo_classe, ente=ente_rep)
    dados_ia  = _parsear_resposta_gemini(resposta_completa)
    minuta    = _extrair_relatorio(resposta_completa)
    minuta_ok = bool(minuta) and not minuta.startswith("[Erro")

    # Sobrescreve docs_suficientes com avaliação da IA
    docs_suf = bool(textos.strip()) or bool(pdfs)
    if dados_ia.get("docs_suficientes_ia", "SIM").upper().startswith(("NÃO", "NAO")):
        docs_suf  = False
        minuta_ok = False
        print(f"  ⚠️  IA sinalizou documentos insuficientes para análise do mérito.")

    # Prévia
    print(f"\n{'─'*50}")
    print(f"📝 ANÁLISE — {num_processo}")
    print(f"{'─'*50}")
    print(minuta[:800] + ("..." if len(minuta) > 800 else ""))

    # Salva
    # ── Integração Sapiens: observação + minuta (sempre) ────
    resultado_rep = {
        "nup": num_processo, "cnj": num_cnj,
        "minuta_ok": minuta_ok, "dados_ia": dados_ia,
        "docs_suficientes": docs_suf,
        "id_tarefa": tarefa_alvo.get("id"),
        "ente": ente_rep,
    }

    if not minuta_ok:
        print(f"\n  ⚠️  Falha na geração da análise: {minuta[:200]}")

    _processar_sapiens_pos_analise(client, resultado_rep, minuta)


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]

    # --limpar-tudo: apaga TODO o cache de PDFs independentemente da idade
    # (a limpeza automática por LGPD já remove arquivos com mais de 15 dias a cada execução)
    if "--limpar-tudo" in args:
        if DIR_PDFS.exists():
            import shutil as _shutil
            _shutil.rmtree(DIR_PDFS)
            print(f"🗑️  Cache de PDFs apagado: {DIR_PDFS}")
        else:
            print("ℹ️  Nenhum cache encontrado.")
        sys.exit(0)

    # --dry-run: executa tudo mas suprime qualquer escrita no Sapiens
    if "--dry-run" in args:
        print("⚠️  MODO DRY-RUN — nenhuma escrita será feita no Sapiens.")
        args = [a for a in args if a != "--dry-run"]

    # Filtra flags conhecidas
    limpar_cache = "--limpar-cache" in args
    args = [a for a in args if a != "--limpar-cache"]

    if args:
        # Modo reprocessamento: python3 robo_sapiens.py [NUP ou CNJ] [--limpar-cache]
        reprocessar(args[0], limpar_cache=limpar_cache)
    else:
        # Modo normal: varre toda a pauta
        main()
