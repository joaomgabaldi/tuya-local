// Tradução entre os DPs crus das lâmpadas/fitas Tuya e o que a página mostra e manda.
// Formatos lidos dos aparelhos em 2026-09-18:
//   cor   = "hhhhssssvvvv" em hex (h 0–360, s e v 0–1000); nas fitas o brilho é o v
//   cena  = 2 dígitos com o número + blocos de 26 dígitos por passo (ver unit())
// Os códigos (work_mode, colour_data_v2...) vêm do mapping do wizard; o DP de cada um chega em it.codes.

const hex = (n, w) => Math.round(n).toString(16).padStart(w, '0');
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

export const hsvHex = (h, s, v) => hex(h, 4) + hex(s, 4) + hex(v, 4);
export function parseHsv(str) {
  if (!str || str.length < 12) return {h: 0, s: 0, v: 1000};
  const [h, s, v] = [0, 4, 8].map(i => parseInt(str.slice(i, i + 4), 16));
  return {h, s, v};
}

const dpsOf = it => {
  const c = it.codes;
  return {sw: it.dp, mode: c.work_mode, bright: c.bright_value_v2, temp: c.temp_value_v2,
          colour: c.colour_data_v2 ?? c.colour_data, scene: c.scene_data_v2 ?? c.scene_data};
};

// estado legível de uma lâmpada/fita: brilho e temperatura em %, h 0–360, s 0–1000, scene = índice do efeito
export function readLight(it) {
  const d = dpsOf(it), v = it.dps;
  const mode = v[d.mode] ?? 'colour';
  const {h, s, v: val} = parseHsv(v[d.colour]);
  const bright = Math.round((mode === 'white' && d.bright ? v[d.bright] ?? 1000 : val) / 10);
  return {on: !!it.on, mode, bright, temp: Math.round((v[d.temp] ?? 0) / 10), h, s,
          scene: parseInt(String(v[d.scene] ?? '00').slice(0, 2), 16)};
}

// comandos: devolvem o objeto {dp: valor} para o /api/set
export function brightCmd(it, pct) {
  const d = dpsOf(it), cur = readLight(it);
  if (pct <= 0) return {[d.sw]: false};  // arrastar até o fundo desliga; qualquer resto vira o mínimo (1%)
  const val = clamp(Math.round(pct * 10), 10, 1000);
  if (cur.mode === 'white' && d.bright) return {[d.sw]: true, [d.bright]: val};
  return {[d.sw]: true, [d.colour]: hsvHex(cur.h, cur.s, val)};
}
export function colourCmd(it, h, s) {
  const d = dpsOf(it);
  return {[d.sw]: true, [d.mode]: 'colour', [d.colour]: hsvHex(h, s, Math.max(10, readLight(it).bright * 10))};
}
export function whiteCmd(it, tempPct) {
  const d = dpsOf(it);
  return {[d.sw]: true, [d.mode]: 'white', [d.temp]: Math.round(tempPct * 10),
          [d.bright]: Math.max(10, readLight(it).bright * 10)};
}
export function effectCmd(it, i) {
  const d = dpsOf(it);
  return {[d.sw]: true, [d.mode]: 'scene', [d.scene]: EFFECTS[i].data};
}

// o que uma cena guarda de um aparelho: só o estado. O DP de cena entra só em modo cena, porque a fita
// entra em modo cena com qualquer escrita nele, mesmo junto de work_mode=colour.
export function stateDps(it) {
  const d = dpsOf(it), v = it.dps, on = !!v[d.sw];
  if (!on || !d.mode) return {[d.sw]: on};
  const mode = v[d.mode] ?? 'colour';
  const keep = {white: [d.bright, d.temp], colour: [d.colour], scene: [d.scene]}[mode] ?? [];
  return Object.fromEntries([[d.sw, true], [d.mode, mode], ...keep.filter(k => k && k in v).map(k => [k, v[k]])]);
}

// um passo de cena: velocidade de troca, velocidade do gradiente, modo (0 estático, 1 salto, 2 gradiente),
// h, s, v (cor) e brilho/temperatura (branco), cada número em hex de 4 dígitos
const unit = (mode, {h = 0, s = 0, v = 0, bright = 0, temp = 0}, speed = [0x46, 0x46]) =>
  hex(speed[0], 2) + hex(speed[1], 2) + hex(mode, 2) + hsvHex(h, s, v) + hex(bright, 4) + hex(temp, 4);
const scene = (n, ...units) => hex(n, 2) + units.join('');
const colours = (mode, hues, speed) => hues.map(h => unit(mode, {h, s: 1000, v: 1000}, speed));

// ponytail: efeitos montados por nós no formato do aparelho (o app da Tuya guarda os de fábrica, o aparelho não);
// "Boa noite" confere byte a byte com o valor lido do Abajur. Os 4 primeiros são de branco: só lâmpadas.
export const EFFECTS = [
  {name: 'Boa noite', white: true, data: scene(0, unit(0, {bright: 200, temp: 0}, [0x0e, 0x0d]))},
  {name: 'Leitura', white: true, data: scene(1, unit(0, {bright: 1000, temp: 500}, [0x0e, 0x0d]))},
  {name: 'Trabalho', white: true, data: scene(2, unit(0, {bright: 1000, temp: 1000}, [0x0e, 0x0d]))},
  {name: 'Lazer', white: true, data: scene(3, unit(0, {bright: 500, temp: 500}, [0x0e, 0x0d]))},
  {name: 'Suave', data: scene(4, unit(2, {h: 120, s: 1000, v: 1000}), unit(2, {h: 120, s: 1000, v: 10}))},
  {name: 'Colorido', data: scene(5, ...colours(2, [0, 120, 240]))},
  {name: 'Vibrante', data: scene(6, ...colours(1, [0, 120, 240], [0x28, 0x28]))},
  {name: 'Arco-íris', data: scene(7, ...colours(2, [0, 60, 120, 180, 240, 300]))},
];
export const effectsFor = it => [...EFFECTS.entries()].filter(([, e]) => !e.white || it.codes.bright_value_v2);
