#!/usr/bin/env python3
"""
buscar_do.py — Monitor DO-SP v4
Estratégia definitiva: POST direto para edicao_download com hdnFormato=json
Baixa a edição completa em JSON para cada dia e filtra pelos termos.
Sem navegador, sem dependências externas — só Python padrão.
"""

import json, time, gzip
import urllib.request, urllib.parse, urllib.error
from datetime import date, timedelta
from pathlib import Path

# ── Configuração ──────────────────────────────────────────
KEYWORDS = [
    "transferência de potencial construtivo",
    "certidão de transferência de potencial",
    "declaração de potencial construtivo",
    "termo de compromisso",
    "atestado de conservação",
    "CONPRESP",
    "projeto de restauro",
    "imóvel tombado",
    "bem tombado",
    "ZEPEC",
]

# None = histórico completo desde 01/03/2023
# Mude para 1 após a primeira execução histórica
DIAS_ATRAS = None

SAIDA    = Path("docs/resultados.json")
BASE_URL = "https://diariooficial.prefeitura.sp.gov.br/md_epubli_controlador.php"
# ─────────────────────────────────────────────────────────


def baixar_edicao_json(data: date) -> list | dict | None:
    """
    Faz POST para edicao_download com hdnFormato=json
    e retorna o conteúdo JSON da edição daquele dia.
    """
    data_str = data.strftime("%d/%m/%Y")

    form_data = urllib.parse.urlencode({
        "acao":               "edicao_download",
        "hdnDtaEdicao":       data_str,
        "hdnTipoEdicao":      "C",
        "hdnBolEdicaoGerada": "false",
        "hdnIdEdicao":        "",
        "hdnInicio":          "0",
        "hdnFormato":         "json",
    }).encode("utf-8")

    headers = {
        "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept":       "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Referer":      BASE_URL + "?acao=diario_aberto&formato=A",
        "Origin":       "https://diariooficial.prefeitura.sp.gov.br",
    }

    req = urllib.request.Request(
        BASE_URL + "?acao=edicao_download",
        data=form_data,
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()

            # Descomprime se necessário
            enc = resp.headers.get("Content-Encoding", "")
            if enc == "gzip":
                raw = gzip.decompress(raw)

            if not raw or len(raw) < 10:
                return None

            # Tenta decodificar como JSON
            for encoding in ("utf-8", "latin-1"):
                try:
                    text = raw.decode(encoding)
                    # Ignora respostas HTML (sem resultados para o dia)
                    if text.strip().startswith("<"):
                        return None
                    return json.loads(text)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
            return None

    except urllib.error.HTTPError as e:
        if e.code not in (404, 500):
            print(f"    HTTP {e.code}", end=" ")
        return None
    except Exception:
        return None


def extrair_texto(obj, profundidade=0) -> str:
    """Extrai todo o texto de uma estrutura JSON recursivamente."""
    if profundidade > 8:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float, bool)):
        return str(obj)
    if isinstance(obj, list):
        return " ".join(extrair_texto(i, profundidade+1) for i in obj)
    if isinstance(obj, dict):
        return " ".join(extrair_texto(v, profundidade+1) for v in obj.values())
    return ""


def filtrar_edicao(dados: list | dict, data: date) -> list[dict]:
    """
    Recebe o JSON da edição e retorna as matérias que contêm
    pelo menos um dos termos monitorados.
    """
    resultados = []
    data_str   = data.strftime("%d/%m/%Y")

    # Normaliza: garante que temos uma lista de itens
    if isinstance(dados, dict):
        # Tenta encontrar a lista de matérias dentro do dict
        items = None
        for chave in ["materias", "materia", "items", "data",
                      "content", "results", "publicacoes"]:
            if chave in dados and isinstance(dados[chave], list):
                items = dados[chave]
                break
        if items is None:
            # Usa os valores do dict como lista
            items = list(dados.values()) if dados else []
    elif isinstance(dados, list):
        items = dados
    else:
        return []

    for item in items:
        texto_completo = extrair_texto(item).lower()
        if not texto_completo or len(texto_completo) < 20:
            continue

        # Verifica termos
        termo_encontrado = None
        for kw in KEYWORDS:
            if kw.lower() in texto_completo:
                termo_encontrado = kw
                break

        if not termo_encontrado:
            continue

        # Extrai campos da matéria
        if isinstance(item, dict):
            titulo = (
                item.get("titulo") or item.get("title") or
                item.get("ementa") or item.get("assunto") or
                item.get("descricao") or ""
            )
            orgao = (
                item.get("orgao") or item.get("secretaria") or
                item.get("unidade") or item.get("departamento") or
                item.get("setor") or ""
            )
            link = (
                item.get("link") or item.get("url") or
                item.get("href") or ""
            )
            # Texto completo para o trecho
            trecho = extrair_texto(item)[:500]
        else:
            titulo = texto_completo[:150]
            orgao  = ""
            link   = ""
            trecho = texto_completo[:500]

        # Filtra títulos que são textos de navegação
        textos_nav = [
            "filtros do resultado", "você está vendo",
            "exibindo", "próxima página", "busca avançada"
        ]
        if any(t in titulo.lower() for t in textos_nav):
            continue

        resultados.append({
            "termo_busca":     termo_encontrado,
            "titulo":          str(titulo)[:200].strip(),
            "orgao":           str(orgao)[:150].strip(),
            "data_publicacao": data_str,
            "link":            str(link),
            "trecho":          trecho,
            "coletado_em":     date.today().isoformat(),
        })

    return resultados


def dias_para_buscar(inicio: date, fim: date) -> list[date]:
    """Retorna todos os dias (seg–sáb) entre início e fim."""
    dias = []
    atual = inicio
    while atual <= fim:
        if atual.weekday() < 6:  # 0=seg a 5=sáb (DO publica ter-sáb)
            dias.append(atual)
        atual += timedelta(days=1)
    return dias


def main():
    hoje   = date.today()
    inicio = date(2023, 3, 1) if DIAS_ATRAS is None \
             else hoje - timedelta(days=DIAS_ATRAS - 1)

    dt_ini = inicio.strftime("%d/%m/%Y")
    dt_fim = hoje.strftime("%d/%m/%Y")

    print(f"🗞  Monitor DO-SP v4 | {dt_ini} → {dt_fim}")
    print(f"🔑 {len(KEYWORDS)} termos monitorados")

    dias   = dias_para_buscar(inicio, hoje)
    print(f"📅 {len(dias)} dias para verificar\n")

    todos       = []
    vistos      = set()
    dias_com_ed = 0
    dias_sem_ed = 0

    for i, dia in enumerate(dias):
        # Log de progresso a cada 30 dias
        if i % 30 == 0:
            print(f"  [{i+1:4d}/{len(dias)}] {dia.strftime('%d/%m/%Y')} "
                  f"— {len(todos)} resultado(s) até agora...")

        dados = baixar_edicao_json(dia)

        if dados is not None:
            dias_com_ed += 1
            encontrados  = filtrar_edicao(dados, dia)
            for r in encontrados:
                chave = r["link"] or (r["titulo"] + r["data_publicacao"])
                if chave not in vistos:
                    vistos.add(chave)
                    todos.append(r)
        else:
            dias_sem_ed += 1

        time.sleep(0.4)  # gentileza com o servidor

    print(f"\n📊 Resumo:")
    print(f"   Edições encontradas: {dias_com_ed}")
    print(f"   Dias sem edição:     {dias_sem_ed}")
    print(f"   Resultados únicos:   {len(todos)}")

    # Ordena por data decrescente
    todos.sort(key=lambda r: r.get("data_publicacao", ""), reverse=True)

    # Salva
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gerado_em":  hoje.isoformat(),
        "periodo":    {"inicio": dt_ini, "fim": dt_fim},
        "total":      len(todos),
        "resultados": todos,
    }
    SAIDA.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"✅ Salvo em {SAIDA}")


if __name__ == "__main__":
    main()
