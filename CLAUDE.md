# Monitor de passagens — contexto para o Claude Code

Robô que pesquisa passagens no Google Voos (sempre na aba **"Menores preços"**) e avisa no
Telegram. Roda no GitHub Actions a cada 4 h. O uso está descrito no `README.md`.

## Como o usuário quer
- Conversar em português, em linguagem simples (o usuário não é desenvolvedor).
- Origem **sempre Brasília (BSB)**, salvo se ele disser outra (`origem_padrao` no config).
- **Sempre ida e volta**, salvo se ele pedir só ida (`ida_e_volta: false`).
- "N dias de viagem" = **dias corridos**: ida dia 1 → volta dia N (volta = ida + N − 1).
- Mês inteiro: testar toda ida dentro do mês; a volta pode cair no mês seguinte.
  Também há o modo datas fixas (`ida` / `volta`).
- Telegram: **alerta imediato** quando o preço fica igual ou abaixo do `preco_alvo`, mais um
  **resumo diário** (`hora_resumo`, horário de Brasília). Cada voo leva o link
  "🔗 Ver voo no Google Voos".

## Descobertas técnicas (não desfazer)
- **Sem janela (headless) o Google mostra preços mais altos.** Em 02/10/2026, mesma URL e mesmo
  IP: R$ 527 headless × R$ 480 com janela, igual ao Chrome do usuário (normal ou anônimo).
  User-agent falso, `navigator.webdriver=false` e Google Chrome headless **não** resolvem.
  Por isso `GoogleVoos(headless=False)` é o padrão e o workflow roda com `xvfb-run`.
- A URL usa o parâmetro `tfs` (protobuf em base64), montado em `google_voos.montar_url`.
  Campo 14 = 1, campo 16 e `tfu=EgoIABAAGAAgAigB` abrem direto a aba "Menores preços". O código
  ainda confere `aria-selected` e clica na aba se for preciso.
- Os preços são lidos dos `aria-label` das linhas (`li [aria-label*="Reais brasileiros"]`), com a
  página em `hl=pt-BR`. É preciso esperar o texto "Buscando" sumir da aba.
- Destinos: "Rio de Janeiro" do Google = `/m/06gmr` (GIG + SDU); São Paulo = GRU, CGH, VCP;
  Recife = REC.
- No Mac não dá para esconder a janela: o macOS ignora `--window-position` negativo.

## Preços de referência (Mac do usuário, 02/10/2026; ida 18/02/2027, volta 24/02/2027)
| BSB → | Menor preço |
|---|---|
| São Paulo | R$ 480 (Gol 06:55 → CGH, sem escalas) |
| Rio de Janeiro | R$ 495 (Gol 12:30 → SDU, sem escalas) |
| Recife | R$ 489 (Gol 14:30, 1 parada); sem escalas: Azul R$ 549 |

Use esses números para conferir se a execução no GitHub vê os mesmos preços.

## Status e próximos passos
- [x] Código, configuração e workflows prontos e testados no Mac.
- [x] Telegram testado. Secrets `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` já cadastrados no repositório.
- [ ] **Ativar os workflows.** Eles estão em `workflows_pendentes/` porque o login do `gh` não
      tinha o escopo `workflow` (o GitHub recusa o push de `.github/workflows/` sem ele):
  1. O usuário roda `gh auth refresh -h github.com -s workflow` e autoriza no navegador.
  2. `mkdir -p .github/workflows && git mv workflows_pendentes/*.yml .github/workflows/`,
     depois commit e push.
- [ ] Rodar o workflow **Diagnóstico** (`gh workflow run diagnostico.yml`) e comparar com os
      preços de referência. Se o servidor vir preços maiores, testar alternativas (por exemplo
      `canal="chrome"`) antes de confiar nos alertas.
- [ ] Rodar o **Monitor de passagens** manualmente e confirmar que a mensagem chega no Telegram.
- [ ] Perguntar ao usuário: preço-alvo de cada viagem (hoje `null`), se inclui Recife, e se quer
      datas fixas ou o mês inteiro.
- [ ] Segurança: o token do bot já passou por chats. Depois que tudo funcionar, sugerir gerar um
      novo no @BotFather (`/revoke`) e atualizar o Secret com `gh secret set TELEGRAM_TOKEN`.
