# Tuya local: página de liga/desliga pela LAN

Data: 2026-09-18 · Status: aprovado em conversa, aguardando revisão desta spec

## Objetivo

Uma página aberta no celular (instalada como PWA na tela inicial do iPhone) com
um bloco por aparelho Tuya. Tocar alterna liga/desliga. Fala direto com os
aparelhos pela LAN via tinytuya, sem o app da Tuya. Nuvem e controle local
convivem; nada é bloqueado no DNS.

Ficam para as próximas versões, que já estão certas: brilho, cor, cenas,
timers, autenticação, acesso fora da LAN e histórico de consumo. A v1 não
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
IP no arquivo: ela usa `address='Auto'` (tinytuya localiza pelo broadcast).

A tomada PC não tem confirmação: um toque alterna, como os outros (decisão do
João).

## Arquitetura

Três arquivos em `~/homelab/tuya-local/`:

- `app.py`: `http.server` da stdlib (`ThreadingHTTPServer`) + tinytuya.
- `index.html`: página única com HTML, CSS e JS inline.
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
  - `GET /api/state` → lista `[{id, name, group, on, online, watts, dps}]`
    na ordem dos grupos acima e alfabética dentro deles.
  - `POST /api/set/<id>` com corpo `{"dp": "20", "value": true}` →
    `set_value(dp, value)`, atualiza o cache com a resposta do aparelho e
    devolve o item. Na v1 a página só manda o DP de liga/desliga. Erro do
    aparelho → 502 com a mensagem do tinytuya. Um DP que não está no
    `mapping` do aparelho → 400.
- Escuta só em `192.168.0.2:8090` (IP da LAN do host). Não escuta no
  Tailscale nem na internet.

### Página (`index.html`)

- Grade de blocos grandes, com título de cada grupo. O bloco ligado fica
  aceso, o offline fica apagado e não aceita toque. As tomadas mostram os
  watts.
- Tocar faz `POST /api/set/<id>` com o DP de liga/desliga e `!on`. O bloco fica em "aguardando" até a
  resposta e mostra o erro se falhar.
- Busca `/api/state` a cada 5 s enquanto a aba está visível
  (`visibilitychange`).
- Meta tags para o iOS (`apple-mobile-web-app-capable`, viewport) e o
  link para o manifest. Sem service worker e sem ícone próprio por
  enquanto.

## Preparado para as próximas versões

A v1 já deixa estas portas abertas, sem implementar nada além disso:

- **Brilho, cor e cenas:** o servidor já aceita qualquer DP do `mapping`,
  e o `/api/state` já entrega os DPs crus. Adicionar esses controles é
  trabalho só da página. O `mapping` do `devices.json` traz os limites
  (`bright_value_v2` 10–1000, `colour_data_v2` em HSV etc.).
- **Timers:** os aparelhos já têm `countdown_1` / DP `26`. Isso cabe no
  mesmo `/api/set`.
- **Autenticação e acesso fora da LAN:** mudam juntos. O candidato
  natural é passar a escutar também no Tailscale (100.68.157.30) e
  adicionar a autenticação nesse momento, não antes.
- **Histórico de consumo:** o poll já lê `cur_power` a cada 10 s. Gravar
  num SQLite, no mesmo padrão do `/opt/scripts/telemetria.db`, é um passo
  a mais no mesmo loop.

## Operação

- Unit `/etc/systemd/system/tuya-local.service`: `User=joao`,
  `WorkingDirectory=/home/joao/homelab/tuya-local`,
  `ExecStart=.venv/bin/python app.py`, `Restart=on-failure`. A instalação
  precisa de sudo, que o João roda.
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
