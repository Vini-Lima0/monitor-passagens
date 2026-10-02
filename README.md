# Monitor de passagens (Google Voos → Telegram)

Pesquisa no Google Voos, sempre na aba **Menores preços**, ida e volta saindo de Brasília
(ou da `origem` que você informar). Cada viagem do `config.yaml` pode ser:

- **Datas fixas:** `ida` e `volta` (ex.: 18/02 a 24/02).
- **Mês inteiro:** `mes` e `dias`. Testa toda ida dentro do mês, com volta = ida + (dias − 1):
  viagem de 10 dias = sai dia 1, volta dia 10. A volta pode cair no mês seguinte.

Avisos no Telegram:
- 🔔 **Alerta imediato** quando aparece ida e volta igual ou abaixo do `preco_alvo`.
  Só alerta de novo se o preço cair ainda mais, ou se subir acima do alvo e depois voltar a cair.
- 📊 **Resumo diário** (a partir da `hora_resumo`): menor preço, variação, top 5 datas.

Roda no GitHub Actions a cada 4 horas. O histórico fica em `dados/historico.csv`.

## Por que o navegador abre "com janela"
Sem janela (modo *headless*), o Google mostra preços mais altos. Na mesma busca e no mesmo IP,
deu R$ 527 sem janela e R$ 480 com janela, igual ao Chrome normal. Por isso o robô sempre abre
com janela. No GitHub, ela vai para uma tela virtual (`xvfb-run`): nada aparece em lugar nenhum.

O workflow **Diagnóstico** (aba Actions → Run workflow) compara os modos no servidor e guarda
prints. Use quando os preços parecerem diferentes dos que você vê no seu Chrome.

## Configurar uma viagem
Edite `config.yaml` e faça commit. A próxima execução já usa os novos valores.

## GitHub
> ⚠️ Por enquanto os workflows estão em `workflows_pendentes/` e ainda não rodam. Para ativar,
> mova-os para `.github/workflows/`. Os passos estão no `CLAUDE.md`.

1. Settings → Secrets and variables → Actions: crie `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.
   Sem eles o monitor roda em modo teste: os preços aparecem no log, nada é enviado nem salvo.
2. Aba Actions → "Monitor de passagens" → **Run workflow** para testar.

Um repositório privado tem 2.000 minutos grátis por mês. Cada execução leva uns 3 minutos com
datas fixas (mais uns 2 minutos por viagem de mês inteiro); a cada 4 horas dá umas 6 execuções
por dia.

## Rodar no Mac (testes)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env              # preencha o token e o chat id
.venv/bin/python monitor.py --teste-telegram
.venv/bin/python monitor.py --sem-telegram
```
No Mac, uma janela do Chromium (navegador próprio do robô, separado do seu Chrome) aparece
durante a busca.
