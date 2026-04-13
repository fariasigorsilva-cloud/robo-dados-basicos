"""
migracao_setor.py
Robô de migração de setor — altera o campo 'setorAtual' nos Dados Básicos
de processos no Super Sapiens AGU.

Lógica:
  1. Busca tarefas abertas do usuário na pasta de destino.
  2. Para cada tarefa, captura o ID do processo vinculado.
  3. Faz GET no /v1/administrativo/processo/{id} com populateAll.
  4. Altera APENAS o campo 'setorAtual' para o novo setor.
  5. Faz PUT com o payload completo atualizado.

Modos de execução:
  python3 migracao_setor.py           → Dry Run (apenas loga, NÃO altera nada)
  python3 migracao_setor.py --executar → Modo real (altera de fato)

Dependência: deve estar na mesma pasta que robo_sapiens.py.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Importa as funções e classes reutilizáveis do robô principal
import sys
sys.path.insert(0, "/Users/fsigor/Desktop/ROBÔ SAPIENS - PAUTA")
from robo_sapiens import (
    SapiensClient,
    verificar_ambiente,
    aguardar_token,
    _iniciar_chrome,
)


# ══════════════════════════════════════════════════════════════
#  CONFIGURAÇÕES — ajuste aqui conforme a operação desejada
# ══════════════════════════════════════════════════════════════

BASE_URL        = "https://supersapiensbackend.agu.gov.br"
DEBUGGER_ADDRESS = "127.0.0.1:9222"

# Usuário e pasta de origem das tarefas
ID_USUARIO = "20008"
ID_FOLDER  = "125719"

# Setor de destino: Protocolo DCJUD1
SETOR_DESTINO = 62140

# Dry Run por padrão — passe --executar para alterações reais
DRY_RUN = "--executar" not in sys.argv


# ══════════════════════════════════════════════════════════════
#  BUSCA DE TAREFAS
# ══════════════════════════════════════════════════════════════

def buscar_tarefas_migracao(client: SapiensClient) -> list[dict]:
    """
    Busca todas as tarefas abertas do usuário na pasta configurada.
    Faz paginação automática para garantir que nenhum processo seja perdido
    mesmo em pastas com muitas tarefas.
    """
    filtro = json.dumps({
        "usuarioResponsavel.id": f"eq:{ID_USUARIO}",
        "dataHoraConclusaoPrazo": "isNull",
        "folder.id":             f"eq:{ID_FOLDER}",
    })
    populate = requests.utils.quote(json.dumps(["processo"]))

    tarefas = []
    offset  = 0
    limit   = 50

    while True:
        url = (
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

    print(f"\n📋 {len(tarefas)} tarefa(s) encontrada(s) na pasta {ID_FOLDER}.")
    return tarefas


# ══════════════════════════════════════════════════════════════
#  BUSCA DO PROCESSO
# ══════════════════════════════════════════════════════════════

# Campos escalares: enviados diretamente como estão no GET populateAll.
_CAMPOS_ESCALARES = {
    "NUP", "alterarChave", "dadosRequerimento",
    "dataHoraAbertura", "dataHoraDesarquivamento", "dataHoraPrazoResposta",
    "descricao", "emTramitacaoExterna", "hasFundamentacaoRestricao",
    "lembreteArquivista", "localizador", "nupInvalido",
    "outroNumero", "processoOrigem", "processoOrigemIncluirDocumentos",
    "protocoloEletronico", "requerimento", "semValorEconomico",
    "temProcessoOrigem", "tipoProtocolo", "titulo", "unidadeArquivistica",
    "validaNup", "valorEconomico", "visibilidadeExterna",
}

# Campos relacionais: chegam como objetos {"id": X, ...} no GET populateAll
# e devem ser enviados como inteiros no PUT.
_CAMPOS_RELACIONAIS = {
    "classificacao", "configuracaoNup", "especieProcesso", "modalidadeFase",
    "modalidadeMeio", "procedencia", "setorAtual", "setorInicial",
}


def buscar_processo_completo(client: SapiensClient, id_processo: str | int) -> dict:
    """
    Faz GET com populateAll para obter todos os campos — incluindo os relacionais
    (classificacao, especieProcesso, setorAtual, etc.) que o GET simples não retorna.
    O payload para o PUT será construído separadamente a partir deste objeto.
    """
    url = (
        f"{BASE_URL}/v1/administrativo/processo/{id_processo}"
        f"?populate={requests.utils.quote(json.dumps(['populateAll']))}"
    )
    return client.get(url)


def _extrair_id(valor) -> int | None:
    """Extrai o ID de um campo relacional, seja ele um dict ou já um inteiro."""
    if isinstance(valor, dict):
        return valor.get("id")
    return valor


def _montar_payload_put(dados: dict, novo_setor: int) -> dict:
    """
    Constrói o payload mínimo exato que o front-end envia no PUT de Dados Básicos,
    baseado na captura de rede do formulário. Envia apenas os campos que o Sapiens
    espera, na forma correta (escalares diretos, relacionais como inteiros).
    """
    payload = {}

    for campo in _CAMPOS_ESCALARES:
        payload[campo] = dados.get(campo)

    for campo in _CAMPOS_RELACIONAIS:
        payload[campo] = _extrair_id(dados.get(campo))

    # Sobrescreve o setor com o valor desejado
    payload["setorAtual"] = novo_setor

    return payload


# ══════════════════════════════════════════════════════════════
#  ALTERAÇÃO DO SETOR
# ══════════════════════════════════════════════════════════════

def alterar_setor_processo(client: SapiensClient, id_processo: str | int, payload: dict) -> dict:
    """
    Envia o PUT com o payload mínimo e limpo.
    Os parâmetros populate=[] e context={} são obrigatórios — sem eles o Sapiens
    interpreta a requisição como 'conversão de dossiê judicial' (restrita a admins).
    """
    url = f"{BASE_URL}/v1/administrativo/processo/{id_processo}?populate=%5B%5D&context=%7B%7D"
    return client.put(url, payload)


# ══════════════════════════════════════════════════════════════
#  FLUXO PRINCIPAL POR TAREFA
# ══════════════════════════════════════════════════════════════

def processar_tarefa(client: SapiensClient, tarefa: dict, indice: int, total: int, contadores: dict) -> None:
    """
    Executa o ciclo completo para uma tarefa:
      - extrai o ID do processo;
      - busca o objeto completo;
      - registra o setor atual no log;
      - altera o campo setorAtual (ou apenas loga em dry run);
      - reporta o resultado e atualiza os contadores globais.

    Qualquer exceção é capturada localmente para que uma falha isolada
    não interrompa o processamento dos demais processos na fila.
    """
    try:
        # Extrai identificadores da tarefa
        processo_ref = tarefa.get("processo") or {}
        at_id        = processo_ref.get("@id") or ""
        id_processo  = processo_ref.get("id") or (at_id.split("/")[-1] if at_id else None)
        id_tarefa    = tarefa.get("id") or "?"

        if not id_processo:
            print(f"  [{indice}/{total}] ⚠️  Tarefa {id_tarefa} sem processo vinculado — pulando.")
            contadores["pulados"] += 1
            return

        print(f"\n{'─'*60}")
        print(f"  [{indice}/{total}] Tarefa: {id_tarefa} | Processo ID: {id_processo}")

        # Busca o objeto completo do processo
        dados_processo = buscar_processo_completo(client, id_processo)

        # Extrai o NUP para identificação no log
        nup = (
            dados_processo.get("NUP")
            or dados_processo.get("nup")
            or dados_processo.get("numeroProcesso")
            or dados_processo.get("numero")
            or "NUP não encontrado"
        )

        # setorAtual vem como objeto {id, nome, ...} no populateAll — extrai só o ID
        setor_atual_id = _extrair_id(dados_processo.get("setorAtual"))

        print(f"  NUP: {nup}")
        print(f"  Setor atual: {setor_atual_id}  →  Setor destino: {SETOR_DESTINO}")

        if setor_atual_id and str(setor_atual_id) == str(SETOR_DESTINO):
            print(f"  ✅ Processo já está no setor {SETOR_DESTINO} — nenhuma ação necessária.")
            contadores["pulados"] += 1
            return

        # Monta payload mínimo com exatamente os campos que o front-end envia no PUT
        payload = _montar_payload_put(dados_processo, SETOR_DESTINO)

        if DRY_RUN:
            print(
                f"  🟡 [DRY RUN] NUP {nup} — setorAtual seria alterado de "
                f"{setor_atual_id} para {SETOR_DESTINO}. Nenhuma requisição enviada."
            )
            print(f"  🔍 [DRY RUN] Payload limpo: {json.dumps(payload, ensure_ascii=False)}")
            contadores["pulados"] += 1
            return

        # Modo real: envia o PUT
        resposta = alterar_setor_processo(client, id_processo, payload)

        status = resposta.get("_status") or resposta.get("id")
        if "_error" in resposta or resposta.get("_status", 0) >= 400:
            print(f"  ❌ Falha ao alterar NUP {nup}: {resposta}")
            contadores["falhas"] += 1
        else:
            print(f"  ✅ NUP {nup} — setorAtual alterado para {SETOR_DESTINO} com sucesso. (retorno: {status})")
            contadores["alterados"] += 1

    except Exception as erro:
        print(f"  ❌ Erro inesperado na tarefa {tarefa.get('id', '?')}: {erro}")
        contadores["falhas"] += 1


# ══════════════════════════════════════════════════════════════
#  PONTO DE ENTRADA
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  🤖  ROBÔ MIGRAÇÃO DE SETOR — AGU Super Sapiens")
    print("=" * 60)

    if DRY_RUN:
        print("\n  🟡 MODO DRY RUN ATIVO — nenhuma alteração será feita.")
        print("     Para executar de verdade, rode:")
        print("     python3 migracao_setor.py --executar\n")
    else:
        print("\n  🔴 MODO REAL — processos serão ALTERADOS no Sapiens.\n")

    # Pré-verificação de ambiente (Chrome, dependências, etc.)
    verificar_ambiente()

    # Inicia Chrome com debugger se necessário
    print("\n🌐 Verificando Chrome...")
    _iniciar_chrome()

    # Conecta ao Chrome via debugger remoto
    options = Options()
    options.add_experimental_option("debuggerAddress", DEBUGGER_ADDRESS)
    service = Service(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=options)

    # Aguarda login manual do usuário
    print("\n" + "─" * 60)
    print("  👉 Certifique-se de estar logado no Sapiens:")
    print("     https://supersapiens.agu.gov.br")
    print("─" * 60)
    input("\n  Quando estiver logado, pressione [ENTER]...\n")

    # Captura o token JWT do Chrome
    print("🔑 Capturando token de autenticação...")
    token  = aguardar_token(driver)
    client = SapiensClient(token=token, driver=driver)

    # Busca as tarefas da pasta configurada
    tarefas = buscar_tarefas_migracao(client)

    if not tarefas:
        print("\n  ℹ️  Nenhuma tarefa encontrada. Encerrando.")
        return

    total      = len(tarefas)
    inicio     = time.time()
    contadores = {"alterados": 0, "pulados": 0, "falhas": 0}

    for indice, tarefa in enumerate(tarefas, 1):
        processar_tarefa(client, tarefa, indice, total, contadores)
        # Pausa curta entre requisições para não saturar a API
        time.sleep(0.5)

    elapsed = time.time() - inicio
    print(f"\n{'='*60}")
    print(f"  ✅ Concluído. {total} tarefa(s) processada(s) em {elapsed:.1f}s.")
    print(f"     Alterados: {contadores['alterados']} | Pulados: {contadores['pulados']} | Falhas: {contadores['falhas']}")

    if DRY_RUN:
        print("  🟡 Nenhuma alteração foi feita (modo dry run).")
        print("     Rode com --executar para aplicar as mudanças.")

    print("=" * 60)

    # Grava arquivo de status para o launcher web detectar que o robô terminou
    status_path = Path(__file__).parent / ".migracao_done.json"
    status_path.write_text(
        json.dumps({
            "alterados": contadores["alterados"],
            "pulados":   contadores["pulados"],
            "falhas":    contadores["falhas"],
            "dry_run":   DRY_RUN,
            "timestamp": time.strftime("%H:%M:%S"),
        }),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
