"""Diagnóstico: roda as mesmas buscas com navegadores diferentes e compara os preços.

Serve para conferir se o servidor (ex.: GitHub Actions) está vendo os mesmos preços que
você vê no seu Chrome. Os prints de cada busca ficam em dados/erros/diag_*.png.
"""
import json
from datetime import date
from pathlib import Path

import requests

from google_voos import GoogleVoos

PASTA = Path(__file__).parent / "dados" / "erros"
BUSCAS = [
    ("São Paulo", ["BSB"], ["GRU", "CGH", "VCP"], date(2027, 2, 18), date(2027, 2, 24)),
    ("Rio", ["BSB"], ["/m/06gmr"], date(2027, 2, 18), date(2027, 2, 24)),
]
MODOS = [
    ("chromium_janela", dict(headless=False)),
    ("chrome_janela", dict(headless=False, canal="chrome")),
    ("chromium_sem_janela", dict(headless=True)),
]


def main():
    PASTA.mkdir(parents=True, exist_ok=True)
    try:
        ip = requests.get("https://ipinfo.io/json", timeout=10).json()
        print(f"Servidor: {ip.get('city')}, {ip.get('country')} ({ip.get('org')})")
    except Exception as e:
        print("Não consegui ver o IP:", e)

    tabela = []
    for modo, kw in MODOS:
        try:
            with GoogleVoos(pasta_erros=str(PASTA), **kw) as gv:
                for nome, o, d, ida, volta in BUSCAS:
                    r = gv.buscar(o, d, ida, volta)
                    gv.page.screenshot(path=str(PASTA / f"diag_{modo}_{nome.replace(' ', '_')}.png"))
                    tabela.append((modo, nome, r.preco, r.companhia, r.horario, r.erro))
        except Exception as e:
            tabela.append((modo, "-", None, "", "", f"falhou ao abrir: {str(e).splitlines()[0][:120]}"))

    print("\nRESULTADO DO DIAGNÓSTICO")
    for modo, nome, preco, cia, hora, erro in tabela:
        print(f"  {modo:<20} {nome:<10} {('R$ ' + str(preco)) if preco else '—':<9} {cia} {hora} {erro}")
    print("\nJSON:", json.dumps(tabela, ensure_ascii=False))


if __name__ == "__main__":
    main()
