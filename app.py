#!/usr/bin/env python3
"""Página de liga/desliga dos aparelhos Tuya pela LAN (spec em docs/superpowers/specs)."""
import sys
import json
import sqlite3
import threading
from contextlib import closing
from urllib.parse import parse_qs, urlsplit
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


def refresh(id, fn, sent={}):
    """Roda fn(device) sob o lock e junta ao cache o que foi enviado (`sent`) e, por cima, o que o aparelho
    respondeu. As lâmpadas v3.4/v3.5 às vezes só confirmam parte dos DPs de um comando aceito; sem o `sent`
    o cache ficava com o valor antigo e o comando seguinte saía com dado velho. Devolve o erro ou None."""
    with LOCK:
        r = fn(CONN[id])
    entry = CACHE[id]
    if isinstance(r, dict) and "dps" in r:
        entry["dps"] = {**entry["dps"], **sent, **r["dps"]}  # troca o dict inteiro: quem serializa não vê ele mudar
        entry["online"] = True
        return None
    entry["online"] = False
    return r.get("Error", "erro desconhecido") if isinstance(r, dict) else "sem resposta"


def poll():
    while True:
        for id in list(CONN):
            refresh(id, lambda dev: dev.status())
            v = view(DEVS[id], CACHE[id])
            if v["online"] and v["watts"] is not None:
                try:
                    record(id, v["watts"], time.time())
                except sqlite3.Error as e:  # disco cheio/travado não pode parar o poll dos aparelhos
                    print(f"histórico: {e}", flush=True)
        time.sleep(POLL_EVERY)


# histórico de consumo: média por minuto por tomada, no mesmo padrão do /opt/scripts/telemetria.db
HISTORY_DB = DIR / "history.db"  # dado, fora do git
RANGES = {"24h": (86400, 300), "7d": (7 * 86400, 3600), "30d": (30 * 86400, None)}  # janela, balde (None = por dia)
_minute = {}  # id → [início do minuto, soma dos watts, amostras]


def db():
    c = sqlite3.connect(HISTORY_DB)
    c.execute("CREATE TABLE IF NOT EXISTS power (device TEXT, ts INTEGER, watts REAL, PRIMARY KEY (device, ts)) WITHOUT ROWID")
    return c


def record(id, watts, now):
    """Acumula a amostra no minuto corrente; quando o minuto vira, grava a média do anterior."""
    m = int(now) - int(now) % 60
    cur = _minute.get(id)
    if cur and cur[0] != m:
        with closing(db()) as c, c:
            c.execute("INSERT OR REPLACE INTO power VALUES (?, ?, ?)", (id, cur[0], cur[1] / cur[2]))
        cur = None
    if not cur:
        cur = _minute[id] = [m, 0.0, 0]
    cur[1] += watts
    cur[2] += 1


def history(id, rng, now):
    """24h e 7d: média de watts por balde; 30d: kWh por dia (horário local). kWh = soma das médias por minuto / 60."""
    span, bucket = RANGES[rng]
    since = int(now) - span
    with closing(db()) as c:
        kwh, avg = c.execute("SELECT COALESCE(SUM(watts), 0) / 60.0 / 1000, AVG(watts) FROM power WHERE device = ? AND ts >= ?",
                             (id, since)).fetchone()
        if bucket:
            pts = c.execute("SELECT ts - ts % ?, AVG(watts) FROM power WHERE device = ? AND ts >= ? GROUP BY 1 ORDER BY 1",
                            (bucket, id, since)).fetchall()
        else:
            pts = c.execute("SELECT date(ts, 'unixepoch', 'localtime'), SUM(watts) / 60.0 / 1000 FROM power "
                            "WHERE device = ? AND ts >= ? GROUP BY 1 ORDER BY 1", (id, since)).fetchall()
    return {"range": rng, "unit": "W" if bucket else "kWh", "points": [list(p) for p in pts],
            "kwh": kwh, "avg_w": None if avg is None else round(avg, 1)}


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


SCENES_FILE = DIR / "scenes.json"  # dado do João, fora do git
SCENES_LOCK = threading.Lock()


def load_scenes():
    try:
        return json.loads(SCENES_FILE.read_text())
    except FileNotFoundError:
        return []


def save_scenes(scenes):
    with SCENES_LOCK:  # grava num temporário e troca: um PUT no meio não deixa o arquivo pela metade
        tmp = SCENES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(scenes, ensure_ascii=False, indent=2))
        tmp.replace(SCENES_FILE)


def validate_scenes(scenes):
    """Lista de {id, name, devices: {id_do_aparelho: {dp: valor}}}; devolve o erro ou None."""
    if not isinstance(scenes, list):
        return "esperado uma lista de cenas"
    ids = set()
    for sc in scenes:
        if not isinstance(sc, dict) or not isinstance(sc.get("id"), str) or not isinstance(sc.get("devices"), dict):
            return "cena esperada: {id, name, devices}"
        if not isinstance(sc.get("name"), str) or not sc["name"].strip():
            return "cena sem nome"
        if sc["id"] in ids:
            return f"id de cena repetido: {sc['id']}"
        ids.add(sc["id"])
        for dev, dps in sc["devices"].items():
            if dev not in DEVS:
                return f"aparelho desconhecido na cena {sc['name']}"
            if err := check_dps(DEVS[dev], dps):
                return f"{sc['name']}: {err}"
    return None


def run_scene(scene):
    """Aplica a cena aparelho por aparelho (um comando cada); devolve os nomes dos que falharam."""
    failed = []
    for dev, dps in scene["devices"].items():
        if dev not in CONN or refresh(dev, lambda d: d.set_multiple_values(dps), sent=dps):
            failed.append(DEVS[dev]["name"])
    return failed


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

    def body(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except ValueError:
            return None

    def do_GET(self):
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/manifest.json": ("manifest.json", "application/manifest+json"),
                 "/codec.js": ("codec.js", "text/javascript; charset=utf-8"),
                 "/icon-180.png": ("icon-180.png", "image/png"),
                 "/icon-512.png": ("icon-512.png", "image/png")}
        path = self.path.split("?")[0]
        if path in files:
            name, ctype = files[path]
            return self.reply(200, (DIR / name).read_bytes(), ctype)
        if path == "/api/state":
            return self.reply(200, [view(DEVS[i], CACHE[i]) for i in DEVS])
        if path == "/api/scenes":
            return self.reply(200, load_scenes())
        if path.startswith("/api/history/"):
            id = path.removeprefix("/api/history/")
            rng = parse_qs(urlsplit(self.path).query).get("range", ["24h"])[0]
            if id not in DEVS:
                return self.reply(404, {"error": "aparelho não encontrado"})
            if rng not in RANGES:
                return self.reply(400, {"error": f"range: {', '.join(RANGES)}"})
            return self.reply(200, history(id, rng, time.time()))
        self.reply(404, {"error": "não encontrado"})

    def do_PUT(self):
        if self.path != "/api/scenes":
            return self.reply(404, {"error": "não encontrado"})
        scenes = self.body()
        if err := validate_scenes(scenes):
            return self.reply(400, {"error": err})
        save_scenes(scenes)
        self.reply(200, scenes)

    def do_POST(self):
        if self.path.startswith("/api/scenes/") and self.path.endswith("/run"):
            sid = self.path.removeprefix("/api/scenes/").removesuffix("/run")
            scene = next((sc for sc in load_scenes() if sc["id"] == sid), None)
            if not scene:
                return self.reply(404, {"error": "cena não encontrada"})
            return self.reply(200, {"failed": run_scene(scene)})
        id = self.path.removeprefix("/api/set/")
        if id == self.path or id not in DEVS:
            return self.reply(404, {"error": "aparelho não encontrado"})
        try:
            dps = self.body()["dps"]
        except (KeyError, TypeError):
            dps = None
        if err := check_dps(DEVS[id], dps):
            return self.reply(400, {"error": err})
        dps = {str(k): v for k, v in dps.items()}
        if id not in CONN:
            return self.reply(502, {"error": "aparelho ainda não achado na rede"})
        # um comando só: trocar modo + cor em duas chamadas faria a lâmpada piscar no modo errado
        if err := refresh(id, lambda dev: dev.set_multiple_values(dps), sent=dps):
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
    # resposta parcial do aparelho: o que foi enviado entra no cache; o que o aparelho respondeu vale por cima
    class Fake:
        def set_multiple_values(self, dps):
            return {"dps": {"20": True}}  # v3.4/v3.5 às vezes só confirma parte do que recebeu
    DEVS["c"], CONN["c"], CACHE["c"] = abajur, Fake(), {"online": True, "dps": {"20": False, "24": "velho"}}
    assert refresh("c", lambda dev: dev.set_multiple_values({"20": True, "24": "novo"}), sent={"20": True, "24": "novo"}) is None
    assert CACHE["c"]["dps"] == {"20": True, "24": "novo"}

    # cenas: validação, gravação e execução (o aparelho "c" já está em DEVS/CONN acima)
    ok = [{"id": "s1", "name": "Cinema", "devices": {"c": {"20": True, "21": "colour", "24": "00d7038400c8"}}}]
    assert validate_scenes(ok) is None
    assert validate_scenes({"id": "s1"})                                          # não é lista
    assert validate_scenes([{"id": "s1", "name": " ", "devices": {}}])            # nome vazio
    assert validate_scenes(ok + ok)                                               # id repetido
    assert validate_scenes([{"id": "s2", "name": "X", "devices": {"zz": {"20": True}}}])  # aparelho que não existe
    assert validate_scenes([{"id": "s2", "name": "X", "devices": {"c": {"21": "disco"}}}])  # DP inválido
    assert validate_scenes([{"id": "s2", "name": "X", "devices": {"c": {}}}])    # aparelho sem estado
    import tempfile
    global SCENES_FILE
    SCENES_FILE = Path(tempfile.mkdtemp()) / "scenes.json"
    assert load_scenes() == []
    save_scenes(ok)
    assert load_scenes() == ok
    assert run_scene(ok[0]) == []
    DEVS["d"] = {**abajur, "id": "d", "name": "Sem IP"}  # aparelho que a descoberta ainda não achou
    assert run_scene({"id": "s3", "name": "Y", "devices": {"d": {"20": True}, "c": {"20": False}}}) == ["Sem IP"]

    # histórico: média por minuto, gravada quando o minuto vira; kWh integrando a potência
    global HISTORY_DB
    HISTORY_DB = Path(tempfile.mkdtemp()) / "history.db"
    t0 = 1_789_700_000 - 1_789_700_000 % 86400 + 3 * 3600  # 00:00 em -03 (servidor em America/Sao_Paulo)
    for t in range(t0, t0 + 60, 10):
        record("pc", 60.0, t)                    # minuto inteiro a 60 W
    record("pc", 120.0, t0 + 60)                 # virou o minuto: grava o anterior (60 W) e começa outro
    record("pc", 180.0, t0 + 70)
    record("pc", 0.0, t0 + 120)                  # grava o segundo minuto: média 150 W
    h = history("pc", "24h", now=t0 + 130)
    assert h["unit"] == "W" and h["points"] == [[t0, 105.0]]  # os dois minutos caem no mesmo balde de 5 min
    assert abs(h["kwh"] - (60 + 150) / 60 / 1000) < 1e-9 and h["avg_w"] == 105.0
    d = history("pc", "30d", now=t0 + 130)
    assert d["unit"] == "kWh" and len(d["points"]) == 1 and abs(d["points"][0][1] - h["kwh"]) < 1e-9
    assert history("pc", "24h", now=t0 + 3 * 86400)["points"] == []  # fora da janela
    assert history("repelente", "7d", now=t0)["kwh"] == 0
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
