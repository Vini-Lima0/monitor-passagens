"""Monitor de passagens aéreas (Google Voos → Telegram).

Uso:
    python monitor.py                 # busca, alerta e (se for a hora) manda o resumo diário
    python monitor.py --resumo        # força o envio do resumo nesta execução
    python monitor.py --sem-telegram  # só mostra no terminal, não envia nada
    python monitor.py --teste-telegram
    python monitor.py --limite 3      # testa só as 3 primeiras datas de cada viagem
"""
import argparse
import csv
import html
import json
import os
import sys
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

from google_voos import GoogleVoos, Resultado

RAIZ = Path(__file__).parent
DADOS = RAIZ / "dados"
ESTADO = DADOS / "estado.json"
HISTORICO = DADOS / "historico.csv"
ERROS = DADOS / "erros"
FUSO = ZoneInfo("America/Sao_Paulo")
DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


# ---------------- utilidades ----------------

def carregar_env():
    """Lê um arquivo .env local (no GitHub os valores vêm dos Secrets)."""
    arq = RAIZ / ".env"
    if not arq.exists():
        return
    for linha in arq.read_text().splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            k, v = linha.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def reais(v) -> str:
    return "—" if v is None else "R$ " + f"{v:,.0f}".replace(",", ".")


def dia_fmt(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d:%d/%m} ({DIAS_SEMANA[d.weekday()]})"


def rota_fmt(v) -> str:
    return f"{'/'.join(v['origem'])} → {'/'.join(v['destino'])}"


def lista(x):
    return x if isinstance(x, list) else [x]


# ---------------- Telegram ----------------

def enviar_telegram(texto: str, ativo=True):
    if not ativo:
        print("\n[telegram desativado] mensagem:\n" + texto + "\n")
        return
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        sys.exit("Faltam TELEGRAM_TOKEN e/ou TELEGRAM_CHAT_ID (Secrets do GitHub ou arquivo .env).")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": texto, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    if not r.ok:
        print("Erro ao enviar para o Telegram:", r.status_code, r.text, file=sys.stderr)
    r.raise_for_status()


# ---------------- datas ----------------

def combinacoes(v, hoje: date):
    """Datas fixas (ida/volta) ou toda ida dentro do mês escolhido, com volta = ida + (dias - 1)."""
    if v.get("ida"):
        ida = date.fromisoformat(str(v["ida"]))
        volta = date.fromisoformat(str(v["volta"])) if v.get("ida_e_volta", True) else None
        return [(ida, volta)] if ida > hoje else []
    ano, mes = map(int, str(v["mes"]).split("-"))
    ultimo = monthrange(ano, mes)[1]
    pares = []
    for dia in range(1, ultimo + 1):
        ida = date(ano, mes, dia)
        if ida <= hoje:
            continue
        if not v.get("ida_e_volta", True):
            pares.append((ida, None))
            continue
        for n in lista(v["dias"]):
            pares.append((ida, ida + timedelta(days=int(n) - 1)))
    return pares


# ---------------- estado / histórico ----------------

def ler_estado():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text())
    return {"viagens": {}, "ultimo_resumo": None}


def salvar_estado(estado):
    DADOS.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=2))


def gravar_historico(agora: datetime, nome: str, resultados):
    DADOS.mkdir(exist_ok=True)
    novo = not HISTORICO.exists()
    with HISTORICO.open("a", newline="") as f:
        w = csv.writer(f)
        if novo:
            w.writerow(["data_hora", "viagem", "ida", "volta", "preco", "companhia",
                        "escalas", "horario_ida", "erro"])
        for r in resultados:
            w.writerow([agora.isoformat(timespec="minutes"), nome, r.ida, r.volta or "",
                        r.preco or "", r.companhia, r.escalas, r.horario, r.erro])


# ---------------- mensagens ----------------

def linha_voo(r: dict) -> str:
    datas = dia_fmt(r["ida"]) + (f" → {dia_fmt(r['volta'])}" if r.get("volta") else "")
    extra = " · ".join(x for x in (r.get("companhia"), r.get("escalas"),
                                   f"sai {r['horario']}" if r.get("horario") else "") if x)
    return (f'<b>{reais(r["preco"])}</b> · {datas}\n'
            f'   {html.escape(extra)}\n'
            f'   🔗 <a href="{html.escape(r["link"])}">Ver voo no Google Voos</a>')


def msg_alerta(v, abaixo):
    abaixo = sorted(abaixo, key=lambda r: r["preco"])
    partes = [
        "🔔 <b>Passagem abaixo do seu preço!</b>",
        f"✈️ {html.escape(v['nome'])}",
        f"🎯 Alvo: {reais(v['preco_alvo'])}",
        "",
    ]
    partes += [linha_voo(r) for r in abaixo[:8]]
    if len(abaixo) > 8:
        partes.append(f"… e mais {len(abaixo) - 8} datas abaixo do alvo.")
    return "\n".join(partes)


def msg_resumo(config, estado, hoje: date):
    partes = [f"📊 <b>Resumo diário — {hoje:%d/%m/%Y}</b>"]
    for v in config["viagens"]:
        if not v.get("ativa", True):
            continue
        ev = estado["viagens"].get(v["nome"])
        partes.append("")
        partes.append(f"✈️ <b>{html.escape(v['nome'])}</b>")
        if not ev or not ev.get("ultima_busca"):
            partes.append("   ainda sem dados")
            continue
        resultados = [r for r in ev["ultima_busca"]["resultados"] if r["preco"]]
        if not resultados:
            partes.append("   ⚠️ a última busca não trouxe preços")
            continue
        melhor = min(r["preco"] for r in resultados)
        anterior = ev.get("preco_ultimo_resumo")
        variacao = ""
        if anterior:
            dif = melhor - anterior
            variacao = (" (= ontem)" if dif == 0 else
                        f" ({'▼' if dif < 0 else '▲'} {reais(abs(dif))} vs. último resumo)")
        partes.append(f"   Menor agora: <b>{reais(melhor)}</b>{variacao}")
        partes.append(f"   Alvo: {reais(v.get('preco_alvo'))} · "
                      f"menor já visto: {reais(ev.get('menor_historico', {}).get('preco'))}")
        partes.append(f"   Datas pesquisadas: {len(ev['ultima_busca']['resultados'])} · "
                      f"atualizado {ev['ultima_busca']['quando'][11:16]}")
        partes.append("   Top 5:")
        for r in sorted(resultados, key=lambda r: r["preco"])[:5]:
            partes.append("   " + linha_voo(r).replace("\n", "\n   "))
        ev["preco_ultimo_resumo"] = melhor
    return "\n".join(partes)


# ---------------- execução ----------------

def processar_viagem(v, gv: GoogleVoos, estado, agora, telegram, limite=None):
    nome = v["nome"]
    pares = combinacoes(v, agora.date())
    if limite:
        pares = pares[:limite]
    print(f"\n=== {nome}: {len(pares)} combinações ===")
    if not pares:
        print("   nenhuma data futura nesse mês — viagem ignorada")
        return

    resultados: list[Resultado] = []
    for i, (ida, volta) in enumerate(pares, 1):
        r = gv.buscar(v["origem"], v["destino"], ida, volta)
        resultados.append(r)
        print(f"  [{i:>2}/{len(pares)}] {dia_fmt(r.ida)} → {dia_fmt(r.volta) or '-'}: "
              f"{reais(r.preco)} {r.companhia} {r.escalas} {r.erro}")

    if telegram:
        gravar_historico(agora, nome, resultados)
    ev = estado["viagens"].setdefault(nome, {})
    dicts = [r.to_dict() for r in resultados]
    ev["ultima_busca"] = {"quando": agora.isoformat(timespec="minutes"), "resultados": dicts}

    validos = [r for r in dicts if r["preco"]]
    if not validos:
        # Avisa no máximo uma vez por dia se o Google parar de responder.
        if ev.get("ultimo_aviso_erro") != agora.date().isoformat():
            enviar_telegram(f"⚠️ <b>{html.escape(nome)}</b>: nenhuma busca retornou preço "
                            f"({resultados[0].erro or 'sem detalhes'}). Vou tentar de novo "
                            "na próxima execução.", telegram)
            ev["ultimo_aviso_erro"] = agora.date().isoformat()
        return

    melhor = min(validos, key=lambda r: r["preco"])
    if not ev.get("menor_historico") or melhor["preco"] < ev["menor_historico"]["preco"]:
        ev["menor_historico"] = {**melhor, "quando": agora.isoformat(timespec="minutes")}

    alvo = v.get("preco_alvo")
    if alvo is None:
        return
    abaixo = [r for r in validos if r["preco"] <= alvo]
    if not abaixo:
        ev["ultimo_alerta_preco"] = None  # voltou acima do alvo: o próximo achado alerta de novo
        return
    # Só alerta de novo se o menor preço caiu em relação ao último alerta (evita repetir).
    ultimo = ev.get("ultimo_alerta_preco")
    if ultimo is None or melhor["preco"] < ultimo:
        enviar_telegram(msg_alerta(v, abaixo), telegram)
        ev["ultimo_alerta_preco"] = melhor["preco"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resumo", action="store_true", help="força o resumo diário agora")
    ap.add_argument("--sem-telegram", action="store_true")
    ap.add_argument("--teste-telegram", action="store_true")
    ap.add_argument("--limite", type=int, help="máximo de datas por viagem (para testes)")
    ap.add_argument("--headless", action="store_true",
                    help="roda sem janela (NÃO recomendado: o Google mostra preços mais altos)")
    args = ap.parse_args()

    carregar_env()
    tem_telegram = bool(os.environ.get("TELEGRAM_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))
    telegram = not args.sem_telegram and tem_telegram
    if not args.sem_telegram and not tem_telegram and not args.teste_telegram:
        print("⚠️  Telegram ainda não configurado (faltam TELEGRAM_TOKEN / TELEGRAM_CHAT_ID): "
              "rodando em modo teste — os preços aparecem aqui no log, nada é enviado nem salvo.")
    if args.teste_telegram:
        enviar_telegram("✅ Monitor de passagens conectado ao Telegram.")
        print("Mensagem de teste enviada.")
        return

    config = yaml.safe_load((RAIZ / "config.yaml").read_text())
    for v in config["viagens"]:
        v["origem"] = lista(v.get("origem") or config.get("origem_padrao", ["BSB"]))
        v["destino"] = lista(v["destino"])
    estado = ler_estado()
    agora = datetime.now(FUSO)
    ERROS.mkdir(parents=True, exist_ok=True)

    # Em modo --sem-telegram nada é salvo, para os testes não bagunçarem o histórico.
    salvar = salvar_estado if telegram else (lambda _e: None)
    with GoogleVoos(headless=args.headless, pasta_erros=str(ERROS)) as gv:
        for v in config["viagens"]:
            if v.get("ativa", True):
                processar_viagem(v, gv, estado, agora, telegram, args.limite)
                salvar(estado)

    hoje = agora.date().isoformat()
    hora_resumo = int(config.get("hora_resumo", 8))
    if args.resumo or (agora.hour >= hora_resumo and estado.get("ultimo_resumo") != hoje):
        enviar_telegram(msg_resumo(config, estado, agora.date()), telegram)
        if telegram:
            estado["ultimo_resumo"] = hoje
    salvar(estado)


if __name__ == "__main__":
    main()
