// Tradução entre os DPs crus das lâmpadas/fitas Tuya e o que a página mostra e manda.
// Formatos lidos dos aparelhos em 2026-09-18:
//   cor   = "hhhhssssvvvv" em hex (h 0–360, s e v 0–1000); nas fitas o brilho é o v
//   cena  = 2 dígitos com o número + blocos de 26 dígitos por passo; os efeitos vêm do app (effects.json)
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

// estado legível de uma lâmpada/fita: brilho e temperatura em %, h 0–360, s 0–1000,
// effect = posição do efeito ativo em it.effects (reconhecido pelo número), -1 se não for da lista
export function readLight(it) {
  const d = dpsOf(it), v = it.dps;
  const mode = v[d.mode] ?? 'colour';
  const {h, s, v: val} = parseHsv(v[d.colour]);
  const bright = Math.round((mode === 'white' && d.bright ? v[d.bright] ?? 1000 : val) / 10);
  // pelo número do efeito (2 primeiros dígitos): a fita reescreve o que recebe (velocidade, ou só um resumo
  // "NN466401...") mas preserva o número, que é único dentro da lista de cada modelo
  const num = s => parseInt(String(s ?? '').slice(0, 2), 16);
  const cur = num(v[d.scene]);
  return {on: !!it.on, mode, bright, temp: Math.round((v[d.temp] ?? 0) / 10), h, s,
          effect: Number.isNaN(cur) ? -1 : (it.effects ?? []).findIndex(e => num(e.data) === cur)};
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
  return {[d.sw]: true, [d.mode]: 'scene', [d.scene]: it.effects[i].data};  // exatamente o texto capturado do app
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
