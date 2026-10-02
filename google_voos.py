"""Busca no Google Voos, sempre na aba "Menores preços"."""
import base64
import re
import time
from dataclasses import dataclass, asdict
from datetime import date

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

# Parâmetro que o próprio Google Voos coloca na URL ao clicar em "Menores preços".
TFU_MENORES_PRECOS = "EgoIABAAGAAgAigB"


# ---------- montagem do parâmetro tfs (protobuf codificado em base64) ----------

def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _campo_int(campo: int, valor: int) -> bytes:
    return _varint(campo << 3) + _varint(valor)


def _campo_bytes(campo: int, valor) -> bytes:
    if isinstance(valor, str):
        valor = valor.encode()
    return _varint(campo << 3 | 2) + _varint(len(valor)) + valor


def _local(codigo: str) -> bytes:
    # 1 = aeroporto (ex.: "GRU"); 3 = cidade do Google (ex.: "/m/06gmr" = Rio de Janeiro)
    tipo = 3 if codigo.startswith("/m/") or codigo.startswith("/g/") else 1
    return _campo_int(1, tipo) + _campo_bytes(2, codigo)


def _trecho(dia: date, origens, destinos) -> bytes:
    b = _campo_bytes(2, dia.isoformat())
    b += b"".join(_campo_bytes(13, _local(o)) for o in origens)
    b += b"".join(_campo_bytes(14, _local(d)) for d in destinos)
    return b


def montar_url(origens, destinos, ida: date, volta: date | None) -> str:
    trechos = _campo_bytes(3, _trecho(ida, origens, destinos))
    if volta:
        trechos += _campo_bytes(3, _trecho(volta, destinos, origens))
    tipo_viagem = 1 if volta else 2  # 1 = ida e volta, 2 = só ida
    msg = (
        _campo_int(1, 28) + _campo_int(2, 2) + trechos
        + _campo_int(8, 1)            # 1 adulto
        + _campo_int(9, 1)            # econômica
        + _campo_int(14, 1)           # aba "Menores preços"
        + _campo_bytes(16, _campo_int(1, (1 << 64) - 1))
        + _campo_int(19, tipo_viagem)
    )
    tfs = base64.urlsafe_b64encode(msg).decode().rstrip("=")
    return (f"https://www.google.com/travel/flights/search?tfs={tfs}"
            f"&tfu={TFU_MENORES_PRECOS}&hl=pt-BR&gl=BR&curr=BRL")


# ---------- leitura da página ----------

@dataclass
class Resultado:
    ida: str
    volta: str | None
    preco: int | None
    companhia: str = ""
    escalas: str = ""
    horario: str = ""
    link: str = ""
    erro: str = ""

    def to_dict(self):
        return asdict(self)


def _so_digitos(txt: str) -> int | None:
    d = re.sub(r"\D", "", txt)
    return int(d) if d else None


def _ler_voo(aria: str) -> dict | None:
    m = re.search(r"([\d.]+)\s+Reais brasileiros", aria)
    if not m:
        return None
    voo = {"preco": _so_digitos(m.group(1)), "companhia": "", "escalas": "", "horario": ""}
    if "Voo direto" in aria:
        voo["escalas"] = "sem escalas"
    else:
        p = re.search(r"(\d+)\s+parada", aria)
        if p:
            n = int(p.group(1))
            voo["escalas"] = f"{n} parada" + ("s" if n > 1 else "")
    c = re.search(r"Voo (?:direto )?d[aoe]s? (.+?)(?: com \d+ parada|\.)", aria)
    if c:
        voo["companhia"] = c.group(1).strip()
    h = re.search(r"às (\d{1,2}:\d{2})", aria)
    if h:
        voo["horario"] = h.group(1)
    return voo


class GoogleVoos:
    def __init__(self, headless: bool = False, pasta_erros: str | None = None,
                 canal: str | None = None):
        # IMPORTANTE: sem janela (headless) o Google mostra preços mais altos — testado em
        # 02/10/2026: R$ 527 sem janela x R$ 480 com janela, mesma busca e mesmo IP.
        # Por isso o padrão é abrir com janela; no GitHub Actions ela vai para uma tela
        # virtual (xvfb-run).
        self.headless = headless
        self.pasta_erros = pasta_erros
        self.canal = canal  # None = Chromium do Playwright; "chrome" = Google Chrome instalado

    def __enter__(self):
        args = ["--disable-blink-features=AutomationControlled"]
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            channel=self.canal,
            ignore_default_args=["--enable-automation"],
            args=args,
        )
        self._ctx = self._browser.new_context(
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1400, "height": 1000},
        )
        self.page: Page = self._ctx.new_page()
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    def _passar_consentimento(self):
        # Servidores fora do Brasil às vezes caem na tela de cookies do Google.
        if "consent.google" not in self.page.url:
            return
        for texto in ("Rejeitar tudo", "Reject all"):
            botao = self.page.get_by_role("button", name=texto)
            if botao.count():
                botao.first.click()
                self.page.wait_for_load_state("domcontentloaded")
                return

    def _garantir_aba_menores_precos(self):
        abas = self.page.get_by_role("tab")
        abas.first.wait_for(timeout=25000)
        aba = self.page.get_by_role("tab", name=re.compile("Menores preços|Cheapest"))
        if aba.get_attribute("aria-selected") != "true":
            aba.click()
            self.page.wait_for_timeout(1000)
        if aba.get_attribute("aria-selected") != "true":
            raise RuntimeError('não consegui selecionar a aba "Menores preços"')
        return aba

    def _esperar_resultados(self, aba, limite_s=40):
        inicio = time.time()
        time.sleep(0.8)
        while time.time() - inicio < limite_s:
            if "Buscando" not in aba.inner_text():
                break
            time.sleep(0.4)
        self.page.wait_for_timeout(800)

    def buscar(self, origens, destinos, ida: date, volta: date | None, tentativas=2) -> Resultado:
        url = montar_url(origens, destinos, ida, volta)
        res = Resultado(ida=ida.isoformat(), volta=volta.isoformat() if volta else None,
                        preco=None, link=url)
        for tentativa in range(1, tentativas + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
                self._passar_consentimento()
                aba = self._garantir_aba_menores_precos()
                self._esperar_resultados(aba)

                voos = []
                rotulos = self.page.eval_on_selector_all(
                    'li [aria-label*="Reais brasileiros"]',
                    "els => els.map(e => e.getAttribute('aria-label'))")
                for rotulo in rotulos:
                    voo = _ler_voo(rotulo or "")
                    if voo and voo["preco"]:
                        voos.append(voo)
                if voos:
                    # em empate de preço, prefere a linha com companhia/horário preenchidos
                    melhor = min(voos, key=lambda v: (v["preco"], not v["companhia"]))
                    res.preco = melhor["preco"]
                    res.companhia = melhor["companhia"]
                    res.escalas = melhor["escalas"]
                    res.horario = melhor["horario"]
                else:
                    # Sem lista de voos: usa o "a partir de R$ X" da própria aba, se houver.
                    m = re.search(r"R\$\s*([\d.]+)", aba.inner_text())
                    res.preco = _so_digitos(m.group(1)) if m else None
                    if res.preco is None:
                        res.erro = "nenhum voo encontrado"
                res.erro = res.erro if res.preco is None else ""
                return res
            except (PWTimeout, RuntimeError) as e:
                res.erro = str(e).splitlines()[0][:200]
                if self.pasta_erros:
                    try:
                        self.page.screenshot(
                            path=f"{self.pasta_erros}/erro_{res.ida}_{tentativa}.png")
                    except Exception:
                        pass
                time.sleep(3 * tentativa)
        return res
