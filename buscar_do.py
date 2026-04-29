#!/usr/bin/env python3
"""
buscar_do.py — Coleta resultados do Diário Oficial de SP
Roda no GitHub Actions todo dia às 8h (horário de Brasília)
Salva resultado em docs/resultados.json para o dashboard ler
"""

import json, os, time
from datetime import date, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

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

DIAS_ATRAS = None       # None = busca histórico completo desde 01/03/2023
                        # Coloque um número (ex: 1) para voltar ao modo diário
BASE_URL   = "https://diariooficial.prefeitura.sp.gov.br/md_epubli_controlador.php"
SAIDA      = Path("docs/resultados.json")
# ─────────────────────────────────────────────────────────


def buscar_termo(page, termo: str, data_inicio: str, data_fim: str) -> list[dict]:
    """Usa o Playwright (navegador real) para buscar um termo no DO."""
    url = (
        f"{BASE_URL}?acao=materias_pesquisar"
        f"&chave={termo}"
        f"&versao_diario=1"
        f"&tipo_resultado=0"
        f"&periodo=2"
        f"&data_inicio={data_inicio}"
        f"&data_fim={data_fim}"
    )

    page.goto(url, wait_until="networkidle", timeout=30000)

    # Aguarda os resultados carregarem (o site usa JS)
    try:
        page.wait_for_selector("li.resultado-item, .resultado, article", timeout=8000)
    except Exception:
        pass  # Pode não haver resultados — segue em frente

    resultados = []

    # Tenta múltiplos seletores possíveis do portal
    for seletor in ["li.resultado-item", ".resultado", "article.materia", ".materia-item"]:
        items = page.query_selector_all(seletor)
        if items:
            for item in items:
                texto = item.inner_text() or ""
                if len(texto.strip()) < 15:
                    continue

                # Link
                link = ""
                a = item.query_selector("a")
                if a:
                    href = a.get_attribute("href") or ""
                    link = href if href.startswith("http") else f"https://diariooficial.prefeitura.sp.gov.br/{href}"

                # Título
                titulo_el = item.query_selector("h2,h3,h4,strong,.titulo")
                titulo = titulo_el.inner_text().strip() if titulo_el else texto[:150]

                # Órgão
                orgao_el = item.query_selector("[class*='orgao'],[class*='unidade'],[class*='secretaria']")
                orgao = orgao_el.inner_text().strip() if orgao_el else ""

                # Data
                data_el = item.query_selector("[class*='data'],[class*='date']")
                data_pub = data_el.inner_text().strip() if data_el else ""

                resultados.append({
                    "termo_busca":     termo,
                    "titulo":          titulo[:200],
                    "orgao":           orgao,
                    "data_publicacao": data_pub,
                    "link":            link,
                    "trecho":          texto.strip()[:600],
                    "coletado_em":     date.today().isoformat(),
                })
            break  # Achou resultados com este seletor, para de tentar

    return resultados


def main():
    hoje   = date.today()
    if DIAS_ATRAS is None:
        inicio = date(2023, 3, 1)  # historico completo
    else:
        inicio = hoje - timedelta(days=DIAS_ATRAS - 1)
    dt_ini = inicio.strftime("%d/%m/%Y")
    dt_fim = hoje.strftime("%d/%m/%Y")

    print(f"🗞  Buscando DO-SP | {dt_ini} → {dt_fim}")
    print(f"🔑 {len(KEYWORDS)} termos\n")

    todos   = []
    vistos  = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx     = browser.new_context(
            user_agent="Mozilla/5.0 (compatible; NB-Monitor/1.0)",
            locale="pt-BR"
        )
        page = ctx.new_page()

        for kw in KEYWORDS:
            print(f"  🔎 {kw!r} …", end=" ", flush=True)
            try:
                res  = buscar_termo(page, kw, dt_ini, dt_fim)
                novos = 0
                for r in res:
                    chave = r["link"] or r["titulo"]
                    if chave not in vistos:
                        vistos.add(chave)
                        todos.append(r)
                        novos += 1
                print(f"{novos} resultado(s)")
            except Exception as e:
                print(f"ERRO: {e}")
            time.sleep(1.2)  # gentileza com o servidor

        browser.close()

    # Salva resultado
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gerado_em":  hoje.isoformat(),
        "periodo":    {"inicio": dt_ini, "fim": dt_fim},
        "total":      len(todos),
        "resultados": todos,
    }
    SAIDA.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ {len(todos)} resultado(s) salvos em {SAIDA}")


if __name__ == "__main__":
    main()
