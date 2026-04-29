#!/usr/bin/env python3
"""
buscar_do.py — Monitor do Diário Oficial da Prefeitura de São Paulo
Estratégia: baixa a edição completa em JSON para cada dia útil
e filtra pelo texto das matérias. Muito mais confiável que scraping.
"""

import json, time, re
from datetime import date, timedelta
from pathlib import Path
import urllib.request
import urllib.error

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
# Coloque 1 para voltar ao modo diário após a primeira execução
DIAS_ATRAS = None

SAIDA = Path("docs/resultados.json")

# URL base para download do JSON de cada edição por data
# O portal do DO-SP publica cada edição em JSON acessível por data
BASE_JSON  = "https://diariooficial.prefeitura.sp.gov.br/md_epubli_memoria_arquivo.php"
BASE_EDICAO = "https://diariooficial.prefeitura.sp.gov.br/md_epubli_controlador.php"
# ─────────────────────────────────────────────────────────


def dias_uteis(inicio: date, fim: date) -> list[date]:
    """Retorna lista de dias úteis (seg–sex) entre início e fim."""
    dias = []
    atual = inicio
    while atual <= fim:
        if atual.weekday() < 5:  # 0=seg, 4=sex
            dias.append(atual)
        atual += timedelta(days=1)
    return dias


def buscar_edicao_json(data: date) -> dict | None:
    """
    Tenta baixar o JSON da edição do DO para uma data específica.
    O portal disponibiliza cada edição em múltiplos formatos.
    """
    # Formatos de URL que o portal usa para acesso por data
    ano = data.year
    mes = str(data.month).zfill(2)
    dia = str(data.day).zfill(2)

    # URL do JSON da edição diária
    url = (
        f"{BASE_EDICAO}?acao=edicao_consultar"
        f"&data={dia}%2F{mes}%2F{ano}&formato=J"
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NB-Monitor/2.0; +https://github.com)",
        "Accept": "application/json, text/html, */*",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read()
            # Tenta decodificar como JSON
            try:
                return json.loads(content.decode("utf-8"))
            except Exception:
                try:
                    return json.loads(content.decode("latin-1"))
                except Exception:
                    return None
    except Exception:
        return None


def buscar_via_pesquisa(termo: str, dt_ini: str, dt_fim: str) -> list[dict]:
    """
    Fallback: usa a URL de pesquisa do portal e extrai links/textos do HTML.
    Funciona mesmo sem JavaScript pois pega a resposta bruta.
    """
    import urllib.parse

    params = urllib.parse.urlencode({
        "acao": "materias_pesquisar",
        "chave": termo,
        "versao_diario": "1",
        "tipo_resultado": "0",
        "periodo": "2",
        "data_inicio": dt_ini,
        "data_fim": dt_fim,
    })

    url = f"{BASE_EDICAO}?{params}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NB-Monitor/2.0)",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
            try:
                html = raw.decode("utf-8")
            except Exception:
                html = raw.decode("latin-1", errors="replace")
    except Exception as e:
        print(f"    Erro HTTP: {e}")
        return []

    resultados = []

    # Extrai matérias do HTML — padrão do portal DO-SP
    # Cada matéria aparece como bloco com título, data, órgão e link
    blocos = re.findall(
        r'<(?:li|div|article)[^>]*class="[^"]*resultado[^"]*"[^>]*>(.*?)</(?:li|div|article)>',
        html, re.DOTALL | re.IGNORECASE
    )

    # Fallback: extrai todos os links de matérias
    if not blocos:
        links = re.findall(
            r'href="([^"]*(?:materia_ver|materia_id|edicao_ver)[^"]*)"[^>]*>(.*?)</a>',
            html, re.DOTALL | re.IGNORECASE
        )
        for href, texto in links:
            texto_limpo = re.sub(r'<[^>]+>', ' ', texto).strip()
            if len(texto_limpo) < 10:
                continue
            link = href if href.startswith("http") else \
                f"https://diariooficial.prefeitura.sp.gov.br/{href}"
            resultados.append({
                "termo_busca":     termo,
                "titulo":          texto_limpo[:200],
                "orgao":           "",
                "data_publicacao": "",
                "link":            link,
                "trecho":          texto_limpo[:400],
                "coletado_em":     date.today().isoformat(),
            })
        return resultados

    for bloco in blocos:
        texto = re.sub(r'<[^>]+>', ' ', bloco)
        texto = re.sub(r'\s+', ' ', texto).strip()
        if len(texto) < 10:
            continue

        link_m = re.search(r'href="([^"]*)"', bloco)
        link = ""
        if link_m:
            href = link_m.group(1)
            link = href if href.startswith("http") else \
                f"https://diariooficial.prefeitura.sp.gov.br/{href}"

        titulo_m = re.search(
            r'<(?:h[2-4]|strong)[^>]*>(.*?)</(?:h[2-4]|strong)>',
            bloco, re.DOTALL | re.IGNORECASE
        )
        titulo = re.sub(r'<[^>]+>', '', titulo_m.group(1)).strip() \
            if titulo_m else texto[:150]

        resultados.append({
            "termo_busca":     termo,
            "titulo":          titulo[:200],
            "orgao":           "",
            "data_publicacao": "",
            "link":            link,
            "trecho":          texto[:500],
            "coletado_em":     date.today().isoformat(),
        })

    return resultados


def filtrar_edicao_json(dados: dict, data: date) -> list[dict]:
    """
    Filtra uma edição completa em JSON pelos termos de interesse.
    """
    resultados = []
    data_str = data.strftime("%d/%m/%Y")

    # O JSON do DO-SP tem estrutura: lista de matérias com texto, título, órgão
    # Normaliza diferentes estruturas possíveis
    materias = []
    if isinstance(dados, list):
        materias = dados
    elif isinstance(dados, dict):
        for chave in ["materias", "materia", "items", "data", "content", "results"]:
            if chave in dados and isinstance(dados[chave], list):
                materias = dados[chave]
                break
        if not materias:
            # Tenta pegar qualquer lista dentro do dict
            for v in dados.values():
                if isinstance(v, list) and len(v) > 0:
                    materias = v
                    break

    for mat in materias:
        if not isinstance(mat, dict):
            continue

        # Extrai texto de campos comuns
        campos_texto = []
        for campo in ["titulo", "title", "texto", "text", "conteudo",
                      "content", "ementa", "descricao", "body", "materia"]:
            val = mat.get(campo, "")
            if isinstance(val, str) and val.strip():
                campos_texto.append(val)

        texto_completo = " ".join(campos_texto).lower()
        if not texto_completo:
            continue

        # Verifica se algum termo está presente
        termo_encontrado = None
        for kw in KEYWORDS:
            if kw.lower() in texto_completo:
                termo_encontrado = kw
                break

        if not termo_encontrado:
            continue

        # Extrai campos relevantes
        titulo = (mat.get("titulo") or mat.get("title") or
                  mat.get("ementa") or "")[:200]
        orgao  = (mat.get("orgao") or mat.get("secretaria") or
                  mat.get("unidade") or mat.get("departamento") or "")
        link   = (mat.get("link") or mat.get("url") or
                  mat.get("href") or "")

        resultados.append({
            "termo_busca":     termo_encontrado,
            "titulo":          titulo,
            "orgao":           str(orgao)[:150],
            "data_publicacao": data_str,
            "link":            str(link),
            "trecho":          " ".join(campos_texto)[:500],
            "coletado_em":     date.today().isoformat(),
        })

    return resultados


def main():
    hoje   = date.today()
    inicio = date(2023, 3, 1) if DIAS_ATRAS is None \
             else hoje - timedelta(days=DIAS_ATRAS - 1)

    dt_ini_str = inicio.strftime("%d/%m/%Y")
    dt_fim_str = hoje.strftime("%d/%m/%Y")

    print(f"🗞  Monitor DO-SP | {dt_ini_str} → {dt_fim_str}")
    print(f"🔑 {len(KEYWORDS)} termos\n")

    todos  = []
    vistos = set()

    # ── Estratégia 1: JSON por edição diária ──────────────
    dias = dias_uteis(inicio, hoje)
    print(f"📅 {len(dias)} dias úteis para verificar\n")

    sucesso_json = 0
    for i, dia in enumerate(dias):
        if i % 20 == 0:
            print(f"  📖 Processando: {dia.strftime('%d/%m/%Y')} "
                  f"({i+1}/{len(dias)})...")
        dados = buscar_edicao_json(dia)
        if dados:
            encontrados = filtrar_edicao_json(dados, dia)
            for r in encontrados:
                chave = r["link"] or r["titulo"]
                if chave not in vistos:
                    vistos.add(chave)
                    todos.append(r)
            sucesso_json += 1
        time.sleep(0.3)

    print(f"\n  ✓ JSON direto: {sucesso_json}/{len(dias)} edições lidas")
    print(f"  → {len(todos)} resultado(s) via JSON\n")

    # ── Estratégia 2: Pesquisa por termo (fallback/complemento) ──
    print("🔎 Pesquisa por termo (complemento)...")
    for kw in KEYWORDS:
        print(f"  → {kw!r}...", end=" ", flush=True)
        try:
            res = buscar_via_pesquisa(kw, dt_ini_str, dt_fim_str)
            novos = 0
            for r in res:
                chave = r["link"] or r["titulo"]
                if chave not in vistos:
                    vistos.add(chave)
                    todos.append(r)
                    novos += 1
            print(f"{novos} novo(s)")
        except Exception as e:
            print(f"erro: {e}")
        time.sleep(1.0)

    # Ordena por data desc
    todos.sort(key=lambda r: r.get("data_publicacao", ""), reverse=True)

    # Salva
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gerado_em":  hoje.isoformat(),
        "periodo":    {"inicio": dt_ini_str, "fim": dt_fim_str},
        "total":      len(todos),
        "resultados": todos,
    }
    SAIDA.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n✅ {len(todos)} resultado(s) salvos em {SAIDA}")


if __name__ == "__main__":
    main()
