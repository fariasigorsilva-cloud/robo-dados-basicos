# Handoff Técnico — Robô Sapiens (Pauta)
> Use este arquivo para iniciar uma nova conversa no Claude Code com contexto completo.
> Apresente-o junto com o arquivo `robo_sapiens.py`.

---

## Contexto

Projeto de automação jurídica desenvolvido por um Procurador Federal da AGU.
O script lê a pauta do Super Sapiens, baixa documentos dos processos, envia ao Gemini e grava minuta + observação de volta no sistema.

**Arquivo principal:** `/Users/fsigor/Desktop/ROBÔ SAPIENS - PAUTA/robo_sapiens.py`
**Tamanho:** ~3.300 linhas | Python 3.9 | macOS

---

## Stack técnico

| Camada | Detalhe |
|--------|---------|
| HTTP | `requests` — chama a API REST do Sapiens diretamente (sem frontend) |
| Autenticação | Token JWT capturado via Selenium/Chrome (porta 9222 debugger) |
| IA | Google Gemini 2.5 Flash Lite via `google-genai` SDK |
| PDFs | `pypdf` para extração de texto; OCR via Tesseract como fallback |
| Paralelismo | `ThreadPoolExecutor` para download de PDFs (4 workers) |
| Config | `.env` na pasta do script: `GEMINI_API_KEY`, `ID_USUARIO`, `ID_FOLDER` |
| Saída | `saida_sapiens/YYYY-MM-DD/*.json` + `saida_sapiens/pdfs/` (cache compartilhado) |
| Logs | `logs/robo_pauta_YYYY-MM-DD.log` (rotativo, 5MB) |

---

## API Sapiens (backend)

```
BASE_URL = "https://supersapiensbackend.agu.gov.br"
```

**Endpoints usados:**

```
GET  /v1/administrativo/tarefa           → busca tarefas da pasta de pauta
GET  /v1/administrativo/juntada          → lista documentos do processo
GET  /v1/administrativo/componente_digital/{id}/conteudo  → baixa HTML/texto
GET  /v1/administrativo/componente_digital/{id}/download  → baixa PDF
POST /v1/administrativo/componente_digital?populate=[]&context={}  → cria componente (minuta)
PATCH /v1/administrativo/componente_digital/{id}?populate=["documento"]&context={}  → insere texto
PATCH /v1/administrativo/tarefa/{id}?populate=[]&context={}  → atualiza observação
```

**Autenticação:** header `Authorization: Bearer {token}`.
O token expira e o robô pausa pedindo relogin (função `_aguardar_relogin`).

**Criação de minuta:** fluxo em dois passos:
1. POST cria o componente com modelo 812466 → retorna `id` e `hash`
2. PATCH envia o HTML em base64 como data URI: `data:text/html;name=MODELO.HTML;charset=utf-8;base64,...`
   - Exige `hashAntigo` do passo 1 e `versaoEditor: "ckeditor5"`

---

## Estrutura de funções principais

```
main()
├── verificar_ambiente()          → checa Python, chromedriver, dependências
├── _iniciar_chrome()             → abre Chrome com debugger se não estiver aberto
├── aguardar_token(driver)        → captura JWT do Chrome
├── SapiensClient                 → classe com .get() .post() .patch() + retry de token
├── buscar_tarefas(client)        → GET tarefas da pasta ID_FOLDER
│
├── [para cada tarefa]:
│   ├── buscar_juntadas(client, id_processo, num_cnj, classe)
│   │   └── filtra juntadas relevantes (sentença, acórdão, petição, volumes, outros)
│   │
│   ├── coletar_conteudo_processo(client, id_processo, classe, num_cnj)
│   │   ├── determina grupo (apelacao / agravo / embargos / outros)
│   │   ├── TIPOS_POR_GRUPO → filtro de tipos aceitos por classe
│   │   ├── loop de juntadas → separa tarefas_texto e tarefas_pdf
│   │   ├── estratégia apelação sem sentença HTML → filtra volumes + sentença em PDF
│   │   ├── estratégia apelação com sentença HTML → envia HTMLs direto
│   │   ├── _fallback_agravo_movimentos()  → busca PDFs pelos movimentos processuais
│   │   ├── _fallback_pre_certidao_migrada() → busca PDFs antes da certidão de migração
│   │   ├── safety: petição inicial (V001_1) sempre incluída
│   │   ├── downloads paralelos (PDF) + sequenciais (HTML/texto)
│   │   └── retorna (texto_agregado, lista_pdfs, ids_peticao_inicial, grupo)
│   │
│   ├── gerar_minuta_parecer(gemini, num_processo, textos, pdfs, ...)
│   │   ├── monta contexto: texto HTML + trechos de PDFs pesquisáveis + PDFs escaneados
│   │   ├── scan de PDFs: _localizar_sentenca_no_pdf() → janela de ±15 páginas
│   │   └── chama Gemini com PROMPT_PARECER
│   │
│   ├── _parsear_resposta_gemini(resposta)  → extrai campos estruturados
│   ├── _salvar_json_processo(resultado)    → JSON em saida_sapiens/YYYY-MM-DD/
│   │
│   └── _processar_sapiens_pos_analise()
│       ├── atualizar_observacao_tarefa()   → PATCH na tarefa
│       └── criar_minuta_na_tarefa()        → POST + PATCH componente digital
│
└── _gerar_relatorio_html(resultados, ...)  → HTML de resumo da execução do dia
```

---

## Lógica de estratégias por classe processual

```python
TIPOS_POR_GRUPO = {
    "embargos":       {"acórdão", "sentença", "decisão", "parecer"},
    "agravo":         {"petição inicial", "decisão", "despacho", "outros", "parecer"},
    "agravo_interno": None,  # aceita todos
    "apelacao":       {"petição inicial", "acórdão", "sentença", "decisão", "outros", "parecer", "volume"},
    "outros":         None,
}
```

**Apelação — hierarquia de estratégias (em ordem de prioridade):**
1. `tem_sentenca_html = True` → usa documentos HTML diretamente (descarta volumes)
2. `tem_sentenca_html = False` → filtra volumes por padrão de filename (`_V001_`, `_V002_`, etc.) e sentença em PDF
3. Apelação delegada (CNJ termina em `.9999`/`.9199`/`.0000`) → aceita todos os docs
4. Fallback: nenhum volume encontrado → `_fallback_pre_certidao_migrada()` (busca independente na API)

**`tem_sentenca_html`** é ativado quando qualquer documento HTML/texto tem `"senten"` no tipo OU na `descricaoOutros`.

**Agravo — fallback:**
Se conteúdo < 5.000 chars e sem PDFs → `_fallback_agravo_movimentos()`:
- Busca todas as juntadas (incluindo administrativas)
- Localiza seq do movimento "DISTRIBUÍDO POR SORTEIO"
- Coleta PDFs < 2MB entre esse seq e o "RECEBIDO PELO DISTRIBUIDOR"
- Marca esses PDFs em `ids_peticao_inicial` para pular scan de sentença

---

## Prompt ao Gemini

O prompt (`PROMPT_PARECER`) retorna blocos delimitados:

```
---DADOS_ESTRUTURADOS---
NOME_RECORRENTE: ...
NOME_RECORRIDO: ...
NOME_RELATOR: ...
DADOS_PAUTA: ...
TIPO_ORIGINARIO: ...
TEMA_IDENTIFICADO: ...
TESE_PRECEDENTE: ...
STATUS: [Favorável/Desfavorável - explicação curta]
DOCS_SUFICIENTES: SIM|NÃO
---FIM_DADOS---

---EMENTA--- ... ---FIM_EMENTA---
---RELATORIO--- ... ---FIM_RELATORIO---
---FUNDAMENTACAO--- ... ---FIM_FUNDAMENTACAO---
---CONCLUSAO--- ... ---FIM_CONCLUSAO---
```

**Parsing:** `_parsear_resposta_gemini()` usa `_extrair_bloco(tag_inicio, tag_fim, tag_fim_alt)`.
O `tag_fim_alt` é a abertura do próximo bloco — fallback para quando o Gemini omite a tag de fechamento.

**STATUS:** sempre do ponto de vista do ente representado (na maioria réu). "Improcedente" = favorável à AGU.

---

## Minuta no Sapiens

**Modelo:** 812466 (CKEditor5)

**Placeholders substituídos no HTML do modelo:**
```
{{NUMERO_PROCESSO}}
{{nome_recorrente}}
{{nome_recorrido}}
{{nome_relator}}
{{dados_pauta}}
{{TAG_RELATORIO}}
{{TAG_FUNDAMENTACAO}}
{{TAG_CONCLUSÃO}}  (e variante {{TAG_CONCLUSAO}})
```

**EMENTA:** injetada diretamente após o texto literal `"EMENTA:"` no HTML do modelo.

---

## Observação da tarefa

Formato gravado no campo `observacao` da tarefa:

```
ROBÔ SAPIENS - {TIPO_ORIGINARIO} // {ENTE} // TEMA: {tema} | TESE: {tese} | STATUS: {status} //
```

Se sem documentos: `ROBÔ SAPIENS - SEM DOCUMENTOS //`

---

## Scan de PDFs para sentença

Para apelação sem sentença HTML, cada PDF passa por:
1. `_extrair_paginas_pdf()` → texto por página via pypdf (ou OCR Tesseract se escaneado)
2. `_localizar_sentenca_no_pdf()` → busca keywords de sentença/acórdão
3. Se encontrado: extrai janela de ±15 páginas ao redor → envia esse trecho ao Gemini
4. Se não encontrado: descarta o PDF (exceto petição inicial e PDFs do fallback do agravo)

**Limite:** 20MB por PDF. PDFs > 20MB são ignorados.

---

## Cache e LGPD

- PDFs ficam em `saida_sapiens/pdfs/` — reutilizados entre execuções (evita redownload)
- Limpeza automática: PDFs com mais de 15 dias são deletados a cada execução (`_limpar_cache_antigo`)
- JSONs ficam em `saida_sapiens/YYYY-MM-DD/` — um por processo por dia
- Processos já com JSON no dia são pulados automaticamente (`já processado — pulando`)

---

## Variáveis de ambiente (.env)

```
GEMINI_API_KEY=...        # obrigatório
ID_USUARIO=20008          # ID do usuário no Sapiens
ID_FOLDER=125702          # ID da pasta de pauta
SAPIENS_TOKEN=...         # opcional: modo web (sem Chrome/Selenium)
```

---

## Padrões de código que o Claude deve seguir

- Não quebrar a assinatura de retorno de `coletar_conteudo_processo` sem atualizar todos os callers
- Tuplas de `tarefas_pdf`: sempre `(id_comp, nome_arquivo, desc_outros, tipo)` — 4 elementos
- Tuplas de `tarefas_texto`: sempre `(id_comp, tipo, desc_outros)` — 3 elementos
- Funções de fallback fazem busca própria na API — não dependem da lista `juntadas` pré-filtrada
- `ids_peticao_inicial` (set de nomes de arquivo) é o mecanismo para pular scan de sentença
- `_aguardar_relogin(driver)` é chamado automaticamente pelos métodos de `SapiensClient` ao receber 401/403

---

## Comandos úteis

```bash
# Execução normal
python3 robo_sapiens.py

# Sem gravar nada no Sapiens
python3 robo_sapiens.py --dry-run

# Limpar cache de PDFs manualmente
python3 -c "
from pathlib import Path
import time
dir_pdfs = Path('/Users/fsigor/Desktop/ROBÔ SAPIENS - PAUTA/saida_sapiens/pdfs')
for f in dir_pdfs.iterdir():
    if f.is_file():
        f.unlink()
print('Cache limpo.')
"

# Reprocessar todos (apagar JSONs do dia para não pular)
rm /Users/fsigor/Desktop/ROBÔ\ SAPIENS\ -\ PAUTA/saida_sapiens/$(date +%Y-%m-%d)/*.json
```
