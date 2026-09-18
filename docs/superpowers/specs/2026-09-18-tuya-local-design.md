# Tuya local: página de liga/desliga pela LAN

Data: 2026-09-18 · Status: aprovada pelo João em 2026-09-18

## Objetivo

Uma página aberta no celular (instalada como PWA na tela inicial do iPhone) com
um bloco por aparelho Tuya. Tocar alterna liga/desliga. Fala direto com os
aparelhos pela LAN via tinytuya, sem o app da Tuya. Nuvem e controle local
convivem; nada é bloqueado no DNS.

Ficam para as próximas versões, que já estão certas: brilho, cor, cenas,
timers e histórico de consumo. O acesso fora da LAN entrou em 2026-09-18
pelo Tailscale (ver "Acesso"). **A página não tem login e não vai ter:** é
decisão fixa do João, não pendência. A v1 não
implementa nenhum deles, mas também não pode atrapalhar a entrada deles.
Por isso o cache guarda os DPs crus e a escrita é genérica (ver
"Preparado para as próximas versões").

## Aparelhos

Fonte única: `devices.json` gerado pelo `tinytuya wizard` (id, key, ip, versão,
categoria, mapping). Gitignored, permissão 600. A página mostra só as
categorias abaixo; o resto (hubs IR `wnykq`, entradas IR, sensores das
baterias) fica de fora automaticamente.

| Categoria | Grupo na página | DP liga/desliga | Aparelhos hoje |
|---|---|---|---|
| `tdq` | Interruptores | `1` | Banheiro, Corredor, Cozinha, Mesa, Sala, Lavanderia |
| `dj` | Lâmpadas | `20` | Abajur, L1, L2, Spot |
| `dd` | Fitas LED | `20` | Fita Led TV, Fita Monitor Centro/Direito/Esquerda |
| `cz` | Tomadas | `1` | PC, Repelente (mostram watts: DP `19`, dividido por 10) |

Todos os 16 com IP responderam a `status()` em 2026-09-18 com a versão do
`devices.json` (3.3, 3.4 e 3.5 misturados). A Lavanderia está offline e sem
IP no arquivo. Uma thread de descoberta chama `tinytuya.find_device(id)` a
cada 60 s até achar o aparelho, e aí ele passa a ser consultado. Não usamos
`address='Auto'`: ele varre a rede por ~18 s dentro do construtor e dá erro
se o aparelho estiver offline, e isso derrubaria o servidor na partida.

A tomada PC não tem confirmação: um toque alterna, como os outros (decisão do
João).

## Arquitetura

Três arquivos em `~/homelab/tuya-local/`:

- `app.py`: `http.server` da stdlib (`ThreadingHTTPServer`) + tinytuya.
- `index.html`: página única com HTML e CSS inline; o JS de tela também é inline.
- `codec.js`: módulo com a tradução entre os DPs e a tela (testado com `node codec.test.mjs`).
- `manifest.json`: nome, `display: standalone` e cores, para virar PWA.

### Servidor (`app.py`)

- Carrega o `devices.json`, filtra pelas 4 categorias e cria um
  `tinytuya.Device` por aparelho (timeout de socket 3 s, 1 tentativa).
- **Cache em memória:** `{id: {online, dps}}`, com todos os DPs crus que o
  aparelho devolve. `on` e `watts` são calculados na hora de responder.
- **Poll:** thread daemon que percorre os aparelhos em sequência e dorme 10 s
  entre as voltas. Um aparelho que falha fica `online: false` e não
  atrasa os outros além do próprio timeout.
- **Uma conexão por aparelho:** um lock global envolve cada chamada ao
  aparelho (`status` ou `set_value`). O lock vale para uma chamada, não
  para a volta inteira, então um toque espera no máximo um timeout (3 s).
  Se o tempo de resposta incomodar, trocar por um lock por aparelho.
- **Endpoints:**
  - `GET /` → `index.html`; `GET /manifest.json`.
  - `GET /api/state` → lista `[{id, name, group, dp, on, online, watts, dps}]`
    na ordem dos grupos acima e alfabética dentro deles.
  - `POST /api/set/<id>` com corpo `{"dp": "20", "value": true}` →
    `set_value(dp, value)`, atualiza o cache com a resposta do aparelho e
    devolve o item. Na v1 a página só manda o DP de liga/desliga. Erro do
    aparelho → 502 com a mensagem do tinytuya. Um DP que não está no
    `mapping` do aparelho → 400.
- Escuta só em `192.168.0.2:8090` (IP da LAN do host). O `app.py` não
  escuta no Tailscale nem na internet. O acesso pelo Tailscale é um proxy
  (ver "Acesso"). O ufw está ativo, então a porta precisa de
  `ufw allow from 192.168.0.0/24 to any port 8090 proto tcp`.

### Página (`index.html`)

- Grade de blocos grandes, com título de cada grupo. O bloco ligado fica
  aceso, o offline fica apagado e não aceita toque. As tomadas mostram os
  watts.
- Tocar faz `POST /api/set/<id>` com o DP de liga/desliga e `!on`. O
  bloco fica em "aguardando" até a resposta e mostra o erro se falhar.
- Busca `/api/state` a cada 5 s enquanto a aba está visível
  (`visibilitychange`).
- Meta tags para o iOS (`apple-mobile-web-app-capable`, viewport) e o
  link para o manifest. Sem service worker e sem ícone próprio por
  enquanto.

## Preparado para as próximas versões

A v1 já deixa estas portas abertas, sem implementar nada além disso:

- **Brilho, cor e efeitos (feito em 2026-09-18):** a página ganhou o cartão
  expandido estilo iOS (segurar o bloco), prototipado em `/proto` e
  aprovado pelo João. O `/api/set/<id>` passou a receber `{"dps": {...}}`
  e grava tudo num `set_multiple_values`, porque modo + cor precisam ir
  juntos. O `view()` expõe `codes` (código → DP), e o `check()` valida o
  range dos `Enum`. A tradução DP ↔ tela fica em `codec.js`, testado com
  `node codec.test.mjs`: cor `hhhhssssvvvv`, brilho da fita = v, e os 8
  efeitos são montados no formato do aparelho, porque os de fábrica ficam
  no app da Tuya e não no aparelho. "Boa noite" confere byte a byte com o
  valor lido do Abajur. Os 4 efeitos de branco valem só para as lâmpadas.
  A página manda no máximo uma escrita por vez por aparelho e junta o que
  chega durante um arrasto, com 250 ms entre os envios.
- **Timers (feito em 2026-09-18):** usa o `countdown_1` do próprio aparelho
  (`9` nos interruptores e tomadas, `26` nas lâmpadas; as fitas não têm).
  Ao zerar, o aparelho inverte o estado. O `view()` expõe `timer_dp`, e o
  `check()` passou a respeitar o min/max do `mapping`. Na página, tocar e
  segurar um bloco abre um painel (`<dialog>`) com 15 min, 30 min, 1 h,
  2 h, um campo de minutos e "Cancelar timer". Esse painel é onde o brilho,
  a cor e os efeitos vão entrar.
- **Histórico de consumo (feito em 2026-09-18):** o poll grava uma média
  por minuto por tomada em `history.db` (SQLite, fora do git). O kWh sai
  integrando a potência, porque o `add_ele` zera sozinho.
  `GET /api/history/<id>?range=24h|7d|30d` devolve 24 h em baldes de 5 min
  (W), 7 d por hora (W) e 30 d em kWh por dia local. No cartão da tomada
  aparecem o total, a média e um gráfico SVG feito à mão: área para W,
  colunas para kWh, arrastar o dedo mostra o valor e há uma tabela para
  leitor de tela. A série é âmbar `#b88600`, validado contra o cartão
  escuro, porque o amarelo dos controles é claro demais para marca de
  dado. Um buraco nos dados quebra a linha. Sem R$ e sem limpeza de
  dados antigos por enquanto (~1 milhão de linhas por ano).

## Acesso

- **Em casa:** `http://192.168.0.2:8090`.
- **De qualquer lugar, com o Tailscale ligado:**
  `https://ubuntuserver.ainu-stairs.ts.net:8090`. Esse endereço é o
  melhor para instalar na Tela de Início, porque funciona nos dois lugares.
- A configuração é
  `sudo tailscale serve --bg --https=8090 http://192.168.0.2:8090`, no
  mesmo padrão das portas 5001 e 8443 do host. Ela fica salva no
  tailscaled e sobrevive a reboot. Para conferir: `tailscale serve status`.
- **Sem login, por decisão:** a tailnet só tem a conta do João e os
  aparelhos dele. Quem chega pelo `.ts.net` já foi autenticado pelo
  Tailscale, e quem chega pela LAN está dentro de casa. Não propor tela
  de login. Se um dia a tailnet for compartilhada, o controle é pelas ACLs
  do Tailscale, não pela página.

## Cenas da casa (desenho aprovado em 2026-09-18)

- **Página:** seção "Cenas" no topo, com blocos só com o nome (sem ícone,
  decisão do João) e um "＋ Nova" no fim. Tocar ativa a cena; segurar abre
  o editor no cartão expandido.
- **Editor completo:** nome, lista de aparelhos com o estado de cada um,
  "＋ Adicionar aparelho" (entra com o estado atual), ✕ para remover,
  Excluir e Salvar. Tocar num aparelho abre **os mesmos controles do
  cartão** (pílula, cores, branco, efeitos, ⏻), mas escrevendo num
  rascunho, sem mexer na casa. Timer não entra em cena.
- **O que a cena guarda por aparelho:** só os DPs do estado, via
  `stateDps()` do `codec.js`. Desligado guarda só o liga/desliga. Cena
  (DP 25) só é salva quando o modo é cena, porque a fita entra em modo
  cena com qualquer escrita nesse DP.
- **Servidor:** `scenes.json` na pasta (dado, fora do git).
  `GET/PUT /api/scenes` com a lista inteira, validada com o `check_dps`
  de cada aparelho. `POST /api/scenes/<id>/run` aplica aparelho por
  aparelho e devolve `{"failed": [nomes]}`.

## Operação

- Unit `/etc/systemd/system/tuya-local.service`: `User=joao`,
  `WorkingDirectory=/home/joao/homelab/tuya-local`,
  `ExecStart=.venv/bin/python app.py`, `Restart=on-failure`. A instalação
  precisa de sudo, que o João roda.
- A unit fica versionada em `tuya-local.service`, e a instalação é um `cp`.
- Dependências: `.venv` do projeto, só com o `tinytuya`.
- Novo aparelho ou troca de chave: rodar `tinytuya wizard` de novo na pasta
  (as credenciais estão no `tinytuya.json`) e reiniciar a unit.

## Riscos conhecidos

- **IP mudou:** se o DHCP trocar o IP de um aparelho, ele aparece offline.
  A correção é reservar o IP no roteador ou rodar o wizard de novo. Só
  vou automatizar se acontecer.
- **Isolamento de rede:** os IoT estavam isolados até 2026-09-18. Se o
  isolamento voltar, a página mostra tudo offline.
- **Nenhuma escrita testada ainda:** a leitura foi testada, a escrita
  não. O primeiro teste de liga/desliga é com o João em casa.

## Testes

- `python app.py --selftest`: `assert`s na tabela categoria → (grupo, DP),
  no filtro que exclui os IR, na conversão de watts e na recusa de DP
  fora do `mapping`.
- Aceitação manual: todos os blocos aparecem, e tocar em cada grupo liga e
  desliga de verdade. A Lavanderia aparece depois que o João reiniciar ela.
