# Tuya local: plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Página no celular que liga e desliga os aparelhos Tuya da casa direto pela LAN.

**Architecture:** Um `app.py` (stdlib `ThreadingHTTPServer` + tinytuya) é o único
dono das conexões com os aparelhos. Uma thread consulta todos a cada 10 s e
guarda os DPs crus em cache. Outra thread procura pelo broadcast os aparelhos
sem IP. A página `index.html` lê `/api/state` e escreve via `/api/set/<id>`.

**Tech Stack:** Python 3.14, tinytuya 1.20.0 (já no `.venv`), HTML/CSS/JS puro, systemd.

**Spec:** `docs/superpowers/specs/2026-09-18-tuya-local-design.md`

## Global Constraints

- Diretório: `/home/joao/homelab/tuya-local`. Python sempre via `.venv/bin/python`.
- Dependências: só stdlib + `tinytuya`. Nada de framework web nem pacote novo.
- Escuta só em `192.168.0.2:8090`.
- Nunca commitar `devices.json`, `tinytuya.json`, `tuya-raw.json`, `snapshot.json` (já estão no `.gitignore`; confira `git status` antes de cada commit).
- **Nunca ligar ou desligar aparelho de verdade durante a implementação.** A escrita real só no Task 5, com o João em casa. Os testes de `POST` usam só caminhos que dão 400/404.
- Categorias e DPs: `tdq`→Interruptores/`1`, `dj`→Lâmpadas/`20`, `dd`→Fitas LED/`20`, `cz`→Tomadas/`1`. A ordem na página é essa.
- Textos da interface e comentários em português do Brasil.
- Commits na `main`, com a linha `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` no fim.

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `app.py` | lógica pura (`load`, `view`, `check`), acesso aos aparelhos, threads, HTTP e `--selftest` |
| `index.html` | página inteira (CSS e JS inline) |
| `manifest.json` | PWA: nome e `display: standalone` |
| `tuya-local.service` | unit do systemd versionada; o João copia com sudo |

---

### Task 1: Lógica pura + selftest

**Files:**
- Create: `app.py`

**Interfaces:**
- Produces:
  - `CATEGORIES: dict[str, tuple[str, str]]`: categoria → (grupo, DP de liga/desliga), na ordem da página.
  - `load(devices: list[dict]) -> list[dict]`: filtra pelo `CATEGORIES` e ordena por (ordem da categoria, nome).
  - `view(d: dict, entry: dict) -> dict`: `entry = {"online": bool, "dps": dict}` → `{id, name, group, dp, on, online, watts, dps}`. `watts` é `None` se o aparelho não tem `cur_power` ou não mandou o DP.
  - `check(d: dict, dp: str, value) -> str | None`: mensagem de erro, ou `None` se o DP existe no `mapping` e o tipo do valor bate.

- [ ] **Step 1: Escrever o selftest (vai falhar)**

Criar `app.py` com:

```python
#!/usr/bin/env python3
"""Página de liga/desliga dos aparelhos Tuya pela LAN (spec em docs/superpowers/specs)."""
import sys


def selftest():
    pc = {"id": "a", "name": "PC", "category": "cz", "mapping": {
        "1": {"code": "switch_1", "type": "Boolean", "values": {}},
        "19": {"code": "cur_power", "type": "Integer", "values": {"unit": "W", "scale": 1}}}}
    ir = {"id": "b", "name": "Controle Remoto", "category": "wnykq", "mapping": {}}
    abajur = {"id": "c", "name": "Abajur", "category": "dj", "mapping": {
        "20": {"code": "switch_led", "type": "Boolean", "values": {}}}}

    # IR sai; lâmpadas (dj) vêm antes de tomadas (cz)
    assert load([ir, pc, abajur]) == [abajur, pc]

    v = view(pc, {"online": True, "dps": {"1": True, "19": 823}})
    assert (v["group"], v["dp"], v["on"], v["watts"]) == ("Tomadas", "1", True, 82.3)
    v = view(abajur, {"online": True, "dps": {"20": False}})
    assert (v["group"], v["dp"], v["on"], v["watts"]) == ("Lâmpadas", "20", False, None)
    assert view(pc, {"online": False, "dps": {}})["watts"] is None

    assert check(pc, "1", True) is None
    assert check(pc, "19", 500) is None
    assert check(pc, "99", True)   # DP fora do mapping
    assert check(pc, "1", 1)       # int não passa como Boolean
    assert check(pc, "19", True)   # bool não passa como Integer
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python app.py --selftest`
Expected: `NameError: name 'load' is not defined`

- [ ] **Step 3: Implementar a lógica**

Inserir logo depois do `import sys`:

```python
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIR = Path(__file__).resolve().parent
HOST, PORT = "192.168.0.2", 8090
POLL_EVERY = 10

# categoria do wizard → (grupo na página, DP de liga/desliga); a ordem aqui é a ordem na página
CATEGORIES = {"tdq": ("Interruptores", "1"), "dj": ("Lâmpadas", "20"),
              "dd": ("Fitas LED", "20"), "cz": ("Tomadas", "1")}
# tipo do mapping → tipo Python aceito na escrita (checado com `type() is`, então bool não passa por int)
TYPES = {"Boolean": bool, "Integer": int, "Enum": str, "String": str, "Json": str}


def load(devices):
    order = list(CATEGORIES)
    keep = [d for d in devices if d.get("category") in CATEGORIES]
    return sorted(keep, key=lambda d: (order.index(d["category"]), d["name"]))


def view(d, entry):
    group, dp = CATEGORIES[d["category"]]
    dps = entry["dps"]
    watts = None
    for k, m in d["mapping"].items():
        if m["code"] == "cur_power" and k in dps:
            watts = dps[k] / 10 ** m["values"].get("scale", 0)
    return {"id": d["id"], "name": d["name"], "group": group, "dp": dp, "on": dps.get(dp),
            "online": entry["online"], "watts": watts, "dps": dps}


def check(d, dp, value):
    m = d["mapping"].get(dp)
    if not m:
        return f"DP {dp} não existe em {d['name']}"
    t = TYPES.get(m["type"])
    if t and type(value) is not t:
        return f"DP {dp} de {d['name']} espera {m['type']}"
    return None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python app.py --selftest`
Expected: `selftest ok`

- [ ] **Step 5: Conferir com o devices.json real (só leitura)**

Run:
```bash
.venv/bin/python -c "
import json, app
ds = app.load(json.load(open('devices.json')))
print(len(ds)); print([d['name'] for d in ds])"
```
Expected: `17` e a lista começando por `Banheiro, Corredor, Cozinha, Lavanderia, Mesa, Sala` e terminando em `PC, Repelente`. Sem `Controle Remoto`, `CRCLMTZDOR`, `Bateria #1/#2` nem as entradas de IR.

- [ ] **Step 6: Commit**

```bash
git status --short   # só app.py
git add app.py
git commit -m "app.py: lógica de categorias, view e validação com selftest

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Aparelhos, threads e HTTP

**Files:**
- Modify: `app.py` (depois de `check`, e o bloco `__main__`)

**Interfaces:**
- Consumes: `load`, `view`, `check`, `CATEGORIES`, `DIR`, `HOST`, `PORT`, `POLL_EVERY` do Task 1.
- Produces:
  - `GET /api/state` → `200` com a lista de `view(...)` na ordem de `load`.
  - `POST /api/set/<id>` com `{"dp": str|int, "value": ...}` → `200` com o `view` atualizado; `400` para corpo inválido ou falha no `check`; `404` para id desconhecido; `502` para erro do aparelho ou aparelho ainda sem IP. Corpo de erro: `{"error": str}`.
  - `GET /` e `GET /manifest.json` servem os arquivos do Task 3 (até lá dão 500 por arquivo ausente, o que é esperado).

- [ ] **Step 1: Estado e acesso aos aparelhos**

Inserir depois de `check`:

```python
DEVS = {}   # id → aparelho do devices.json, na ordem de exibição
CONN = {}   # id → tinytuya.Device; aparelho sem IP fica de fora até a descoberta achar
CACHE = {}  # id → {"online": bool, "dps": dict}
# ponytail: lock global, uma chamada por vez em toda a casa; trocar por lock por aparelho se o toque ficar lento
LOCK = threading.Lock()


def connect(d, ip, version):
    import tinytuya
    dev = tinytuya.Device(d["id"], ip, d["key"], version=float(version))
    dev.set_socketTimeout(3)
    dev.set_socketRetryLimit(1)
    return dev


def refresh(id, fn):
    """Roda fn(device) sob o lock e junta os DPs da resposta ao cache. Devolve a mensagem de erro ou None."""
    with LOCK:
        r = fn(CONN[id])
    entry = CACHE[id]
    if isinstance(r, dict) and "dps" in r:
        entry["dps"] = {**entry["dps"], **r["dps"]}  # troca o dict inteiro: quem está serializando não vê ele mudar
        entry["online"] = True
        return None
    entry["online"] = False
    return r.get("Error", "erro desconhecido") if isinstance(r, dict) else "sem resposta"


def poll():
    while True:
        for id in list(CONN):
            refresh(id, lambda dev: dev.status())
        time.sleep(POLL_EVERY)


def discover():
    """Aparelho sem IP no devices.json (ex.: offline no wizard): procura pelo broadcast até achar."""
    import tinytuya
    while missing := [i for i in DEVS if i not in CONN]:
        for i in missing:
            b = tinytuya.find_device(i)  # ~18 s escutando broadcast, sem lock: não abre conexão
            if b["ip"]:
                CONN[i] = connect(DEVS[i], b["ip"], b["version"])
                print(f"{DEVS[i]['name']} achado em {b['ip']}", flush=True)
        time.sleep(60)
```

- [ ] **Step 2: Handler HTTP e main**

Inserir depois de `discover`:

```python
class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/manifest.json": ("manifest.json", "application/manifest+json")}
        path = self.path.split("?")[0]
        if path in files:
            name, ctype = files[path]
            return self.reply(200, (DIR / name).read_bytes(), ctype)
        if path == "/api/state":
            return self.reply(200, [view(DEVS[i], CACHE[i]) for i in DEVS])
        self.reply(404, {"error": "não encontrado"})

    def do_POST(self):
        id = self.path.removeprefix("/api/set/")
        if id == self.path or id not in DEVS:
            return self.reply(404, {"error": "aparelho não encontrado"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            dp, value = str(body["dp"]), body["value"]
        except (ValueError, KeyError, TypeError):
            return self.reply(400, {"error": 'corpo esperado: {"dp": ..., "value": ...}'})
        if err := check(DEVS[id], dp, value):
            return self.reply(400, {"error": err})
        if id not in CONN:
            return self.reply(502, {"error": "aparelho ainda não achado na rede"})
        if err := refresh(id, lambda dev: dev.set_value(dp, value)):
            return self.reply(502, {"error": err})
        self.reply(200, view(DEVS[id], CACHE[id]))

    def log_message(self, *args):
        pass  # a página consulta a cada 5 s; logar isso só enche o journal


def main():
    for d in load(json.load(open(DIR / "devices.json"))):
        DEVS[d["id"]] = d
        CACHE[d["id"]] = {"online": False, "dps": {}}
        if d.get("ip"):
            CONN[d["id"]] = connect(d, d["ip"], d["version"])
    threading.Thread(target=poll, daemon=True).start()
    threading.Thread(target=discover, daemon=True).start()
    print(f"ouvindo em http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
```

Trocar o bloco final por:

```python
if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
```

- [ ] **Step 3: Selftest continua passando**

Run: `.venv/bin/python app.py --selftest`
Expected: `selftest ok`

- [ ] **Step 4: Subir e ler o estado real**

Run:
```bash
.venv/bin/python app.py > /home/joao/.claude/jobs/ffe2097c/tmp/app.log 2>&1 &
sleep 15
curl -s http://192.168.0.2:8090/api/state | .venv/bin/python -c "
import json,sys
for x in json.load(sys.stdin): print(f\"{x['group']:13} {x['name']:22} online={x['online']} on={x['on']} watts={x['watts']}\")"
```
Expected: 17 linhas; 16 com `online=True` e `on` True/False; Lavanderia `online=False`; PC e Repelente com `watts` numérico.

- [ ] **Step 5: Caminhos de erro, sem escrever em aparelho**

Run (o servidor do Step 4 ainda no ar):
```bash
PC=$(curl -s http://192.168.0.2:8090/api/state | .venv/bin/python -c "import json,sys;print(next(x['id'] for x in json.load(sys.stdin) if x['name']=='PC'))")
curl -s -w ' %{http_code}\n' -X POST http://192.168.0.2:8090/api/set/naoexiste -d '{"dp":"1","value":true}'
curl -s -w ' %{http_code}\n' -X POST http://192.168.0.2:8090/api/set/$PC -d 'lixo'
curl -s -w ' %{http_code}\n' -X POST http://192.168.0.2:8090/api/set/$PC -d '{"dp":"99","value":true}'
curl -s -w ' %{http_code}\n' -X POST http://192.168.0.2:8090/api/set/$PC -d '{"dp":"1","value":1}'
curl -s -w ' %{http_code}\n' http://192.168.0.2:8090/nada
kill %1
```
Expected, na ordem: `404`, `400` (corpo esperado), `400` (DP 99 não existe), `400` (espera Boolean), `404`.

- [ ] **Step 6: Commit**

```bash
git status --short   # só app.py
git add app.py
git commit -m "app.py: poll, descoberta de IP e API HTTP

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Página e manifest

**Files:**
- Create: `index.html`, `manifest.json`

**Interfaces:**
- Consumes: `GET /api/state` e `POST /api/set/<id>` do Task 2 (campos `id, name, group, dp, on, online, watts`).

- [ ] **Step 1: `manifest.json`**

```json
{
  "name": "Casa",
  "short_name": "Casa",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#111111",
  "theme_color": "#111111"
}
```

- [ ] **Step 2: `index.html`**

```html
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Casa">
<meta name="theme-color" content="#111111">
<link rel="manifest" href="/manifest.json">
<title>Casa</title>
<style>
  :root { color-scheme: dark; --bg: #111; --off: #242424; --on: #f5c542; --txt: #eee; }
  body { margin: 0; padding: 16px; padding-top: max(16px, env(safe-area-inset-top));
         background: var(--bg); color: var(--txt); font: 16px -apple-system, system-ui, sans-serif; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; opacity: .6; margin: 20px 0 8px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
  button { all: unset; box-sizing: border-box; min-height: 84px; padding: 12px; border-radius: 14px;
           background: var(--off); display: flex; flex-direction: column; justify-content: space-between;
           cursor: pointer; -webkit-tap-highlight-color: transparent; }
  button:focus-visible { outline: 2px solid var(--on); }
  button.on { background: var(--on); color: #111; }
  button.offline { opacity: .35; pointer-events: none; }
  button.busy { opacity: .6; }
  small { opacity: .75; font-size: 13px; }
  #erro { color: #f77; min-height: 1.2em; }
</style>
</head>
<body>
<div id="erro" role="status"></div>
<main id="app"></main>
<script>
const app = document.getElementById('app'), erro = document.getElementById('erro');
const busy = new Set();
let last = [];

function render() {
  app.replaceChildren();
  let grid;
  for (const it of last) {
    if (!grid || grid.dataset.group !== it.group) {
      const h = document.createElement('h2');
      h.textContent = it.group;
      grid = document.createElement('div');
      grid.className = 'grid';
      grid.dataset.group = it.group;
      app.append(h, grid);
    }
    const b = document.createElement('button');
    b.className = [it.on && 'on', !it.online && 'offline', busy.has(it.id) && 'busy'].filter(Boolean).join(' ');
    b.setAttribute('aria-pressed', String(!!it.on));
    const name = document.createElement('span');
    name.textContent = it.name;
    const status = document.createElement('small');
    status.textContent = !it.online ? 'offline'
      : it.watts != null ? `${it.watts.toFixed(1)} W`
      : it.on ? 'ligado' : 'desligado';
    b.append(name, status);
    b.onclick = () => toggle(it);
    grid.append(b);
  }
}

async function load() {
  try {
    const r = await fetch('/api/state');
    last = await r.json();
    erro.textContent = '';
    render();
  } catch (e) {
    erro.textContent = 'Sem conexão com o servidor';
  }
}

async function toggle(it) {
  if (busy.has(it.id)) return;
  busy.add(it.id);
  render();
  try {
    const r = await fetch('/api/set/' + it.id, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dp: it.dp, value: !it.on}),
    });
    const body = await r.json();
    if (!r.ok) throw new Error(body.error);
    last = last.map(x => x.id === body.id ? body : x);
    erro.textContent = '';
  } catch (e) {
    erro.textContent = `${it.name}: ${e.message}`;
  }
  busy.delete(it.id);
  render();
}

let timer;
function start() {
  clearInterval(timer);
  if (!document.hidden) { load(); timer = setInterval(load, 5000); }
}
document.addEventListener('visibilitychange', start);
start();
</script>
</body>
</html>
```

- [ ] **Step 3: Conferir que o servidor entrega os dois**

Run:
```bash
.venv/bin/python app.py > /home/joao/.claude/jobs/ffe2097c/tmp/app.log 2>&1 &
sleep 3
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://192.168.0.2:8090/
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://192.168.0.2:8090/manifest.json
kill %1
```
Expected: `200 text/html; charset=utf-8` e `200 application/manifest+json`.

- [ ] **Step 4: Commit**

```bash
git status --short   # index.html e manifest.json
git add index.html manifest.json
git commit -m "Página e manifest da PWA

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Unit do systemd e firewall

**Files:**
- Create: `tuya-local.service`

- [ ] **Step 1: Unit**

```ini
[Unit]
Description=Tuya local: página de liga/desliga pela LAN
After=network-online.target
Wants=network-online.target

[Service]
User=joao
WorkingDirectory=/home/joao/homelab/tuya-local
ExecStart=/home/joao/homelab/tuya-local/.venv/bin/python app.py
# no boot o 192.168.0.2 pode ainda não existir; o bind falha e o systemd tenta de novo
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Conferir a unit**

Run: `systemd-analyze verify ./tuya-local.service`
Expected: nenhuma linha de erro sobre este arquivo.

- [ ] **Step 3: Commit**

```bash
git add tuya-local.service
git commit -m "Unit do systemd

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Instalação (o João roda, precisa de sudo)**

Passar ao João exatamente:
```bash
sudo cp ~/homelab/tuya-local/tuya-local.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tuya-local
sudo ufw allow from 192.168.0.0/24 to any port 8090 proto tcp
```
Depois conferir: `systemctl is-active tuya-local` → `active` e
`curl -s http://192.168.0.2:8090/api/state | head -c 200` retorna JSON.

---

### Task 5: Aceitação com o João em casa (manual)

- [ ] Abrir `http://192.168.0.2:8090` no iPhone (Wi-Fi de casa) e conferir os 4 grupos com os nomes certos.
- [ ] Primeira escrita real: tocar num aparelho de cada grupo (um interruptor, uma lâmpada, uma fita, o Repelente) e conferir que ele liga e desliga de verdade e que o bloco acompanha.
- [ ] Mudar um aparelho pelo app da Tuya e ver a página atualizar em até ~15 s.
- [ ] Compartilhar → "Adicionar à Tela de Início" e abrir sem a barra do Safari.
- [ ] Reiniciar a Lavanderia e acompanhar `journalctl -u tuya-local -f` até aparecer `Lavanderia achado em ...`, depois tocar nela.
- [ ] Se algum aparelho responder mal à escrita (ex.: v3.5 devolvendo `sem resposta` mesmo mudando de estado), anotar e voltar para o Task 2.
