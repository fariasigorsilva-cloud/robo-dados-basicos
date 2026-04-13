# HANDOFF — ROBÔ MIGRAÇÃO DE SETOR

## Estado do projeto em 27/03/2026

---

## 1. VISÃO GERAL

Script Python `migracao_setor.py` que automatiza a alteração do campo **"Setor Atual"** nos Dados Básicos de processos no Super Sapiens (AGU). Busca tarefas abertas em uma pasta específica e, para cada processo vinculado, envia o PUT correto via API para migrar o setor.

Além do script CLI, existe o aplicativo web standalone `app_dados_basicos.py` — uma interface gráfica moderna que pode ser usada sem terminal e sem expor o código-fonte.

**Localização:** `/Users/fsigor/Desktop/ROBÔ DADOS BÁSICOS/`

---

## 2. COMO RODAR

### Script CLI (`migracao_setor.py`)

```bash
# Dry Run (padrão) — apenas loga o que seria feito, sem alterar nada
cd '/Users/fsigor/Desktop/ROBÔ DADOS BÁSICOS' && caffeinate -i python3 migracao_setor.py

# Modo real — altera de fato o setorAtual nos processos
cd '/Users/fsigor/Desktop/ROBÔ DADOS BÁSICOS' && caffeinate -i python3 migracao_setor.py --executar
```

### Aplicativo web standalone (`app_dados_basicos.py`)

```bash
cd '/Users/fsigor/Desktop/ROBÔ DADOS BÁSICOS' && python3 app_dados_basicos.py
```

O servidor sobe na porta **7772** e abre o browser automaticamente em `http://127.0.0.1:7772`.
Para encerrar: `Ctrl+C` no terminal.

---

## 3. CONFIGURAÇÕES DO SCRIPT CLI

```python
BASE_URL      = "https://supersapiensbackend.agu.gov.br"
ID_USUARIO    = "20008"
ID_FOLDER     = "125719"     # pasta de origem das tarefas
SETOR_DESTINO = 62140        # ID do Protocolo DCJUD1
DRY_RUN       = "--executar" not in sys.argv
```

---

## 4. FLUXO DE EXECUÇÃO (CLI)

```
1. verificar_ambiente()              ← reutiliza do robo_sapiens.py
2. _iniciar_chrome()                 ← reutiliza do robo_sapiens.py
3. aguardar_token()                  ← reutiliza do robo_sapiens.py
4. buscar_tarefas_migracao()         ← filtra por pasta 125719 + usuário + abertas
5. Para cada tarefa:
   a. buscar_processo_completo()     ← GET ?populate=["populateAll"] → todos os campos
   b. _montar_payload_put()          ← constrói payload mínimo e limpo
   c. alterar_setor_processo()       ← PUT ?populate=[]&context={} com novo setorAtual
```

---

## 5. DETALHE CRÍTICO — O PAYLOAD DO PUT

Este foi o principal obstáculo durante o desenvolvimento. Existem quatro armadilhas no endpoint de alteração de Dados Básicos:

### Armadilha 1 — parâmetros da URL do PUT
O PUT **obrigatoriamente** deve incluir `?populate=%5B%5D&context=%7B%7D`.
Sem esses parâmetros, o Sapiens interpreta a requisição como "conversão de Dossiê Judicial" (operação restrita a administradores) e retorna:
```
422 — "Apenas Administradores poderão converter Dossiês Judiciais!"
```

### Armadilha 2 — GET simples não retorna todos os campos necessários
O GET sem populate não devolve campos como `classificacao`, `especieProcesso`, `modalidadeMeio`, `procedencia`, `setorAtual`, `setorInicial`. Enviar o PUT sem esses campos também causa 422.
**Solução:** usar GET com `?populate=["populateAll"]` para obter o objeto completo.

### Armadilha 3 — campos relacionais devem ser IDs, não objetos
O GET com `populateAll` retorna os campos relacionais como objetos completos:
```json
"classificacao": {"@type": "Classificacao", "id": 324, "nome": "...", ...}
```
O PUT espera apenas o inteiro: `"classificacao": 324`.
Enviar o objeto inteiro causa 422 por campo inválido.
**Solução:** função `_extrair_id()` que converte dicts para seu `id`.

### Armadilha 4 — payload não pode conter campos extras do Sapiens
Campos como `@type`, `@id`, `@context`, `uuid`, `NUPFormatado`, `any`, `hasBookmark`, `criadoEm`, `atualizadoEm` **não devem** ir no PUT.
**Solução:** payload construído a partir de whitelist explícita (`_CAMPOS_ESCALARES` + `_CAMPOS_RELACIONAIS`), baseada na captura de rede real do front-end.

### Payload final — campos enviados

| Campo | Tipo | Observação |
|-------|------|------------|
| NUP | string | — |
| titulo | string | — |
| descricao | string/null | — |
| dataHoraAbertura | string/null | — |
| dataHoraDesarquivamento | string/null | — |
| dataHoraPrazoResposta | string/null | — |
| valorEconomico | float/null | — |
| semValorEconomico | bool | — |
| protocoloEletronico | bool | — |
| visibilidadeExterna | bool | — |
| hasFundamentacaoRestricao | bool/null | — |
| tipoProtocolo | int | escalar |
| unidadeArquivistica | int | escalar |
| classificacao | int | relacional → extrair `.id` |
| configuracaoNup | int/null | relacional → extrair `.id` |
| especieProcesso | int | relacional → extrair `.id` |
| modalidadeFase | int/null | relacional → extrair `.id` |
| modalidadeMeio | int | relacional → extrair `.id` |
| procedencia | int | relacional → extrair `.id` |
| **setorAtual** | int | **campo alterado** → sempre `SETOR_DESTINO` |
| setorInicial | int | relacional → extrair `.id` |

---

## 6. PROTEÇÕES IMPLEMENTADAS

- **Dry Run por padrão:** sem `--executar`, nenhuma requisição de escrita é feita.
- **Skip automático:** processos cujo `setorAtual` já é `SETOR_DESTINO` são pulados.
- **try/except por tarefa:** falha em um processo não interrompe os demais.
- **Paginação automática:** percorre todas as páginas, sem limite de 50 tarefas.
- **Pausa entre requisições:** `time.sleep(0.4)` para não saturar a API.

---

## 7. HISTÓRICO DE ERROS RESOLVIDOS

### Erros da API

| Erro | Causa | Solução |
|------|-------|---------|
| `422 Apenas Administradores...` | URL do PUT sem `populate=[]&context={}` | Adicionar parâmetros obrigatórios |
| `422 especieProcesso inválida` (e outros) | GET com `populateAll` enviava objetos completos | `_montar_payload_put` com whitelist + `_extrair_id` |
| `setorAtual: None` no log | GET simples não retorna `setorAtual` | Migrar para GET com `populateAll` |
| `422 configuracaoNup inválida` | Campo estava em `_CAMPOS_ESCALARES` mas é relacional | Movido para `_CAMPOS_RELACIONAIS` |
| `422 modalidadeFase inválida` | Campo estava em `_CAMPOS_ESCALARES` mas é relacional | Movido para `_CAMPOS_RELACIONAIS` |

### Erros do app web (`app_dados_basicos.py`)

| Erro | Causa | Solução |
|------|-------|---------|
| `EXC_CRASH SIGABRT / TkpInit` | `customtkinter` usa Tk 8.5 incompatível com macOS 15 Sequoia | App reescrito como servidor web (`http.server` + HTML/JS) |
| Botões não funcionavam (nenhum onclick respondia) | `'\n'` dentro de string Python gerava newline literal no JS, quebrando a tag `<script>` inteira | Corrigido para `'\\n'` no template Python |
| Requisições bloqueavam / travavam | `HTTPServer` single-threaded: conexão keep-alive segurava o servidor | Adicionado `ThreadingMixIn` para tratar conexões em threads separadas |
| Browser carregava versão antiga | Sem `Cache-Control` nem `Content-Length` na resposta HTML | Adicionados `Cache-Control: no-cache` e `Content-Length` correto |
| Erros de fetch silenciosos | `api()` sem `.catch()` — rejeições de Promise sem handler | Adicionado `.catch()` que retorna `{ok: false, erro: '...'}` |

---

## 8. RESULTADO DA PRIMEIRA EXECUÇÃO REAL (24/03/2026)

```
12 tarefas encontradas na pasta 125719
  ✅  7 alteradas com sucesso
  ✅  2 já estavam no setor 62140 (nenhuma ação)
  ❌  3 falharam (configuracaoNup — corrigido depois)
Tempo total: 21.6s
```

---

## 9. DEPENDÊNCIAS

```bash
pip3 install requests selenium --break-system-packages
```

Python: `/Library/Developer/CommandLineTools/usr/bin/python3` (3.9)
Chrome: `/Applications/Google Chrome.app`
`robo_sapiens.py` em `/Users/fsigor/Desktop/ROBÔ SAPIENS - PAUTA/` (usado pelo CLI, não pelo app web)

---

## 10. ADAPTAÇÃO PARA OUTRAS MIGRAÇÕES

Para reutilizar o **script CLI** em outra migração de setor, altere apenas:

```python
ID_FOLDER     = "XXXXX"   # pasta com as tarefas desejadas
SETOR_DESTINO = XXXXX     # ID do setor de destino
```

Para usar o **app web**, basta digitar o ID da Pasta e o Setor de Destino na interface — não é necessário alterar código.

Para descobrir o ID de um setor: no Sapiens, altere manualmente o "Setor Atual" de qualquer processo, capture no DevTools (F12 → Network) o PUT para `/v1/administrativo/processo/{id}` e leia o campo `setorAtual` no payload enviado.

---

## 11. ARQUIVOS NA PASTA DE TRABALHO

```
/Users/fsigor/Desktop/ROBÔ DADOS BÁSICOS/
├── migracao_setor.py          ← script CLI (integrado ao launcher Robo Sapiens.py)
├── app_dados_basicos.py       ← aplicativo web standalone (macOS + Windows)
├── requirements_app.txt       ← dependências
├── build_mac.sh               ← gera .app para macOS via PyInstaller
├── build_windows.bat          ← gera .exe para Windows via PyInstaller
└── .migracao_done.json        ← gerado ao término, lido pelo launcher web

/Users/fsigor/Desktop/ROBÔ SAPIENS - PAUTA/
└── robo_sapiens.py            ← biblioteca compartilhada (NÃO MEXER)
```

---

## 12. APLICATIVO WEB STANDALONE (`app_dados_basicos.py`)

### Arquitetura

O app é um **servidor HTTP local** (`http.server` + `ThreadingMixIn`) rodando na porta 7772. Serve uma página HTML/JS moderna que o browser abre automaticamente. Toda a comunicação entre o frontend (browser) e o backend (Python) é feita via `fetch()` para rotas locais (`/abrir_chrome`, `/capturar_token`, `/estado`, `/iniciar`).

**Não usa Tk, customtkinter nem nenhuma biblioteca de GUI.** Funciona em qualquer sistema que tenha Python 3.9+ e Chrome.

### Diferenças em relação ao script CLI

| Aspecto | CLI (`migracao_setor.py`) | App web (`app_dados_basicos.py`) |
|---|---|---|
| Interface | Terminal | Browser (HTML/JS moderno) |
| ID do usuário | Hardcoded (`20008`) | Descoberto automaticamente do JWT |
| ID da pasta | Hardcoded (`125719`) | Digitado pelo usuário na tela |
| Código visível | Sim | Não (distribuível como executável) |
| Plataforma | macOS | macOS + Windows |
| Dependência externa | `robo_sapiens.py` | Standalone completo |

### Como o app descobre o ID do usuário
1. Abre Chrome com `--remote-debugging-port=9222`
2. Selenium conecta via `debuggerAddress` (sem abrir nova janela)
3. Executa scripts no `localStorage` para capturar o token JWT
4. Decodifica o payload JWT (base64) — procura campos `id`, `usuarioId`, `userId`
5. Fallback: chama a API nos endpoints `/v1/usuario/me`, `/v1/usuario/perfil`

### Fluxo de uso
```
1. Abrir Chrome      → Chrome com porta de debug 9222 + URL do Sapiens
2. Login no Sapiens  → usuário faz login normalmente
3. Capturar Token    → Selenium captura JWT, descobre ID do usuário
4. Informar ID da Pasta e Setor de Destino
5. Escolher Dry Run ou Executar
6. Iniciar Migração  → log em tempo real no terminal da página
```

### Estado global do servidor (`_estado`)
```python
_estado = {
    "fase":         "idle",   # idle | capturando | conectado | executando | concluido | erro
    "token":        None,
    "id_usuario":   None,
    "nome_usuario": None,
    "log":          [],
    "contadores":   {"alterados": 0, "pulados": 0, "falhas": 0},
    "erro":         None,
}
```
O frontend faz polling em `/estado` a cada 900ms durante a execução para atualizar o terminal e os contadores na tela.

### Rotas do servidor

| Rota | Método | Função |
|------|--------|--------|
| `/` | GET | Serve o HTML da interface |
| `/abrir_chrome` | GET | Abre Chrome com `--remote-debugging-port=9222` |
| `/capturar_token` | GET | Inicia thread que conecta via Selenium e captura JWT |
| `/estado` | GET | Retorna JSON com `_estado` atual |
| `/iniciar` | GET | Inicia thread de migração com parâmetros `pasta`, `setor`, `modo` |

### Build (gerar executável sem código visível)

**macOS:**
```bash
cd '/Users/fsigor/Desktop/ROBÔ DADOS BÁSICOS'
chmod +x build_mac.sh && ./build_mac.sh
# → dist/Dados Básicos AGU.app
```

**Windows:**
```bat
cd "caminho\para\ROBÔ DADOS BÁSICOS"
build_windows.bat
REM → dist\Dados Basicos AGU.exe
```

> **Atenção:** os scripts de build ainda referenciam `--collect-all customtkinter`, que não é mais necessário. Se for gerar um novo executável, remover essa flag do `build_mac.sh` e `build_windows.bat`.

### Integração com o launcher web
O app GUI é **independente** do launcher `Robo Sapiens.py`. O botão "🔄 MIGRAR SETORES — DCJUD1" no launcher web dispara o script CLI (`migracao_setor.py`), não o app web.

---

## 13. ARMADILHA CRÍTICA DO TEMPLATE HTML EM PYTHON

Ao editar o HTML/JS embutido no arquivo `.py` (string `HTML = """..."""`):

**NUNCA usar `'\n'` dentro de strings JavaScript no template.**

```python
# ❌ ERRADO — Python interpreta \n como newline literal, quebrando o JS
s.textContent = linha + '\n';

# ✅ CORRETO — Python gera o texto \n no JS (dois caracteres: \ e n)
s.textContent = linha + '\\n';
```

Esse bug quebra a tag `<script>` inteira: **todos** os onclick, funções e variáveis deixam de existir. O sintoma é que nenhum elemento da página responde a cliques, sem nenhum erro visível no browser.

**Regra geral:** qualquer caractere de escape JS (`\n`, `\t`, `\r`, `\\`) dentro do template Python precisa de barra dupla (`\\n`, `\\t`, `\\r`, `\\\\`).
