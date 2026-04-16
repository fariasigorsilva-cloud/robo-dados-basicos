# Robô Sapiens — Triagem de Pauta
### Documento de Apresentação e Transferência de Conhecimento

---

## O que é

O **Robô Sapiens** é um assistente automatizado de triagem de pauta judicial desenvolvido internamente para a AGU. Ele lê os processos da pauta do dia no Super Sapiens, usa Inteligência Artificial (Google Gemini) para analisar o conteúdo de cada processo, e entrega dois produtos diretamente no sistema:

1. **Minuta de parecer** — rascunho estruturado (ementa, relatório, fundamentação, conclusão) pronto para revisão e assinatura.
2. **Observação na tarefa** — linha resumida com tipo do processo, status para a AGU (Favorável / Desfavorável / Parcialmente Favorável) e tema jurídico identificado.

O procurador abre o Sapiens e encontra o trabalho inicial já feito — só precisa revisar, ajustar e assinar.

---

## Problema que resolve

Numa pauta com 10, 15 ou 20 processos marcados para julgamento, o procurador precisa:
- Abrir cada processo
- Ler sentença, acórdão, petição inicial
- Identificar a tese
- Verificar se há precedente favorável ou desfavorável à AGU
- Redigir o parecer oral ou escrito

O robô faz as etapas de leitura e rascunho automaticamente, liberando o procurador para focar na análise crítica e na sustentação oral.

---

## Como funciona (fluxo simplificado)

```
1. Robô abre o Chrome (Super Sapiens já logado)
         ↓
2. Captura o token de autenticação automaticamente
         ↓
3. Busca todas as tarefas abertas na pasta de pauta
         ↓
4. Para cada processo:
   a) Identifica a classe processual (Apelação, Agravo, Embargos...)
   b) Baixa os documentos relevantes (sentença, acórdão, petição inicial,
      volumes digitalizados) — PDFs e HTMLs
   c) Envia o conteúdo ao Gemini com um prompt jurídico especializado
   d) Gemini retorna: ementa, relatório, fundamentação, conclusão,
      status para a AGU, tema, tese de precedente
   e) Robô cria a minuta no Sapiens e atualiza a observação da tarefa
         ↓
5. Ao final: relatório de execução (processos analisados, minutas geradas, falhas)
```

---

## Estratégias por classe processual

O robô adapta o que baixa conforme a classe do processo:

| Classe | O que busca |
|--------|-------------|
| **Apelação Cível** | Sentença (HTML ou PDF), acórdão, petição inicial, volumes digitalizados |
| **Agravo de Instrumento** | Petição do agravo, decisão recorrida |
| **Agravo Interno / Regimental** | Todos os documentos (originário pode ser qualquer classe) |
| **Embargos à Execução** | Acórdão, sentença, decisão |
| **Outros** | Comportamento padrão — todos os documentos relevantes |

Para **processos migrados** (processo físico digitalizado): o robô localiza a "Certidão de Processo Migrado" como delimitador e baixa todos os PDFs do processo físico que precedem essa certidão.

---

## O que a IA analisa e entrega

O Gemini recebe os documentos do processo e retorna:

| Campo | Descrição |
|-------|-----------|
| **TIPO_ORIGINARIO** | Ex.: Apelação Cível, Agravo de Instrumento |
| **TEMA_IDENTIFICADO** | Tema jurídico específico do processo |
| **TESE_PRECEDENTE** | Precedente identificado (STJ, STF, TRF) se houver |
| **STATUS** | `Favorável`, `Desfavorável` ou `Parcialmente Favorável` para a AGU |
| **EMENTA** | Ementa do parecer |
| **RELATÓRIO** | Histórico processual resumido |
| **FUNDAMENTAÇÃO** | Análise jurídica com aplicação ao caso |
| **CONCLUSÃO** | Posição final do procurador |

O **STATUS** é determinado sempre do ponto de vista da AGU/ente representado (não da parte contrária). Por exemplo: se a sentença foi de improcedência do pedido do particular, o status é **Favorável** para a AGU.

---

## Temas com tratamento especial

O robô reconhece automaticamente temas que **não precisam de acompanhamento especial** (ex.: execuções fiscais, multas ambientais por dosimetria, INSS em geral) e temas que **merecem atenção** (ex.: ações regressivas por violência doméstica, temas ANS específicos, casos com repercussão geral).

Essa lista de temas é editável diretamente no script — qualquer procurador pode ajustar conforme a especialidade da unidade.

---

## Infraestrutura e dependências

| Item | Detalhe |
|------|---------|
| **Linguagem** | Python 3.9 |
| **Sistema** | macOS (Chrome deve estar aberto e logado no Sapiens) |
| **IA** | Google Gemini 2.5 Flash Lite (via API) |
| **Autenticação** | Token JWT capturado automaticamente do Chrome (porta 9222) |
| **Arquivo de config** | `.env` na pasta do robô (chave de API Gemini, ID do usuário) |
| **Saída** | Pasta `saida_sapiens/YYYY-MM-DD/` com JSONs de cada processo |
| **Logs** | Pasta `logs/` com arquivo rotativo diário |

**Não requer servidor** — roda direto no computador do procurador.

---

## Como executar

```bash
# 1. Abrir o Chrome com o Sapiens logado (já configurado automaticamente)
# 2. Na pasta do robô, executar:

python3 robo_sapiens.py

# Para testar sem gravar nada no Sapiens:
python3 robo_sapiens.py --dry-run
```

Se o token expirar durante a execução, o robô **pausa e aguarda** — o procurador faz login novamente e pressiona Enter para continuar.

---

## O que o procurador precisa fazer após o robô rodar

1. Abrir o Sapiens na pasta de pauta
2. Para cada processo: abrir a tarefa → ver a observação (resumo rápido) → abrir a minuta gerada
3. **Revisar** o rascunho — especialmente fundamentação e conclusão
4. Ajustar trechos conforme necessário
5. Assinar e movimentar

O robô **não assina** e **não movimenta** processos. A decisão final é sempre do procurador.

---

## Limitações conhecidas

- Processos com documentos apenas em imagem escaneada (sem texto) podem ter análise prejudicada
- Processos muito volumosos (PDFs > 20MB por arquivo) são ignorados automaticamente
- A qualidade do rascunho depende da qualidade e completude dos documentos disponíveis no Sapiens
- Temas muito específicos ou novos podem não ser reconhecidos pela lista de temas

---

## Histórico e evolução

O robô foi desenvolvido e refinado ao longo de diversas sessões de trabalho. As principais funcionalidades adicionadas progressivamente:

- **v1** — Triagem básica com Gemini, criação de minuta no Sapiens
- **Estratégias por classe** — Apelação, Agravo, Embargos com coleta de documentos diferenciada
- **Fallback para agravo** — Quando documentos insuficientes, busca a petição pelos movimentos processuais "distribuído por sorteio" / "recebido pelo distribuidor"
- **Fallback para processos migrados** — Localiza certidão de migração e baixa todo o processo físico digitalizado
- **Token resiliente** — Pausa e aguarda relogin em vez de encerrar com erro
- **Petição inicial garantida** — Sempre inclui o primeiro volume (V001) no contexto da IA
- **JSON por processo** — Cada análise salva em arquivo individual para rastreabilidade

---

## Contato

Desenvolvido por: **Procurador Federal — AGU**
Script principal: `robo_sapiens.py`
Pasta: `ROBÔ SAPIENS - PAUTA/`
