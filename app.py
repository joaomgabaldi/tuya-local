#!/usr/bin/env python3
"""Página de liga/desliga dos aparelhos Tuya pela LAN (spec em docs/superpowers/specs)."""
import sys
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
    timer_dp = next((k for k, m in d["mapping"].items() if m["code"] == "countdown_1"), None)
    codes = {m["code"]: k for k, m in d["mapping"].items()}  # a página acha brilho/cor/cena pelo código
    return {"id": d["id"], "name": d["name"], "group": group, "dp": dp, "on": dps.get(dp),
            "online": entry["online"], "watts": watts, "timer_dp": timer_dp, "codes": codes, "dps": dps}


def check(d, dp, value):
    m = d["mapping"].get(dp)
    if not m:
        return f"DP {dp} não existe em {d['name']}"
    t = TYPES.get(m["type"])
    if t and type(value) is not t:
        return f"DP {dp} de {d['name']} espera {m['type']}"
    lim = m["values"]
    if t is int and isinstance(lim, dict) and not lim.get("min", value) <= value <= lim.get("max", value):
        return f"DP {dp} de {d['name']} aceita de {lim.get('min')} a {lim.get('max')}"
    if m["type"] == "Enum" and isinstance(lim, dict) and value not in lim.get("range", [value]):
        return f"DP {dp} de {d['name']} aceita {', '.join(lim['range'])}"
    return None


def check_dps(d, dps):
    """Valida um comando com vários DPs; um valor ruim recusa o comando inteiro."""
    if not isinstance(dps, dict) or not dps:
        return 'corpo esperado: {"dps": {"<dp>": <valor>, ...}}'
    for dp, value in dps.items():
        if err := check(d, str(dp), value):
            return err
    return None


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
                 "/manifest.json": ("manifest.json", "application/manifest+json"),
                 "/codec.js": ("codec.js", "text/javascript; charset=utf-8")}
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
            dps = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))["dps"]
        except (ValueError, KeyError, TypeError):
            dps = None
        if err := check_dps(DEVS[id], dps):
            return self.reply(400, {"error": err})
        dps = {str(k): v for k, v in dps.items()}
        if id not in CONN:
            return self.reply(502, {"error": "aparelho ainda não achado na rede"})
        # um comando só: trocar modo + cor em duas chamadas faria a lâmpada piscar no modo errado
        if err := refresh(id, lambda dev: dev.set_multiple_values(dps)):
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


def selftest():
    pc = {"id": "a", "name": "PC", "category": "cz", "mapping": {
        "1": {"code": "switch_1", "type": "Boolean", "values": {}},
        "9": {"code": "countdown_1", "type": "Integer", "values": {"unit": "s", "min": 0, "max": 86400}},
        "19": {"code": "cur_power", "type": "Integer", "values": {"unit": "W", "scale": 1}}}}
    ir = {"id": "b", "name": "Controle Remoto", "category": "wnykq", "mapping": {}}
    abajur = {"id": "c", "name": "Abajur", "category": "dj", "mapping": {
        "20": {"code": "switch_led", "type": "Boolean", "values": {}},
        "21": {"code": "work_mode", "type": "Enum", "values": {"range": ["white", "colour", "scene", "music"]}},
        "24": {"code": "colour_data_v2", "type": "Json", "values": {}}}}

    # IR sai; lâmpadas (dj) vêm antes de tomadas (cz)
    assert load([ir, pc, abajur]) == [abajur, pc]

    v = view(pc, {"online": True, "dps": {"1": True, "19": 823}})
    assert (v["group"], v["dp"], v["on"], v["watts"]) == ("Tomadas", "1", True, 82.3)
    v = view(abajur, {"online": True, "dps": {"20": False}})
    assert (v["group"], v["dp"], v["on"], v["watts"]) == ("Lâmpadas", "20", False, None)
    assert view(pc, {"online": False, "dps": {}})["watts"] is None
    assert view(pc, {"online": True, "dps": {}})["timer_dp"] == "9"
    assert view(abajur, {"online": True, "dps": {}})["timer_dp"] is None  # sem countdown_1 (ex.: fitas)

    assert check(pc, "1", True) is None
    assert check(pc, "19", 500) is None
    assert check(pc, "99", True)   # DP fora do mapping
    assert check(pc, "1", 1)       # int não passa como Boolean
    assert check(pc, "19", True)   # bool não passa como Integer
    assert check(pc, "9", 1800) is None
    assert check(pc, "9", -1)      # abaixo do min do mapping
    assert check(pc, "9", 86401)   # acima do max do mapping
    assert check(abajur, "21", "colour") is None
    assert check(abajur, "21", "disco")  # fora do range do Enum

    assert view(abajur, {"online": True, "dps": {}})["codes"]["colour_data_v2"] == "24"
    assert check_dps(abajur, {"20": True, "21": "colour", "24": "0115035c03e8"}) is None
    assert check_dps(abajur, {"20": True, "21": "disco"})  # um valor ruim recusa o comando inteiro
    assert check_dps(abajur, {})
    assert check_dps(abajur, ["20", True])
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
