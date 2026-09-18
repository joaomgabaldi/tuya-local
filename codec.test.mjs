// node codec.test.mjs — testa a tradução entre os DPs crus e o que a página mostra/manda
import assert from 'node:assert/strict';
import {hsvHex, parseHsv, readLight, brightCmd, colourCmd, whiteCmd, effectCmd, effectsFor, EFFECTS} from './codec.js';

const lamp = {dp: '20', codes: {switch_led: '20', work_mode: '21', bright_value_v2: '22', temp_value_v2: '23',
  colour_data_v2: '24', scene_data_v2: '25'}};
const strip = {dp: '20', codes: {switch_led: '20', work_mode: '21', colour_data: '24', scene_data: '25'}};

// hex hhhhssssvvvv
assert.equal(hsvHex(277, 860, 1000), '0115035c03e8');
assert.deepEqual(parseHsv('002703E801F4'), {h: 39, s: 1000, v: 500});
assert.deepEqual(parseHsv(undefined), {h: 0, s: 0, v: 1000});

// leitura: branco usa o DP de brilho; cor usa o V; cena devolve o índice
let st = readLight({...lamp, on: true, dps: {'21': 'white', '22': 700, '23': 300, '24': '0115035c03e8'}});
assert.deepEqual([st.mode, st.bright, st.temp], ['white', 70, 30]);
st = readLight({...lamp, on: true, dps: {'21': 'colour', '22': 700, '24': '0115035c01f4'}});
assert.deepEqual([st.mode, st.bright, st.h, st.s], ['colour', 50, 277, 860]);
st = readLight({...strip, on: false, dps: {'21': 'scene', '25': '06464601000003e803e800000000'}});
assert.deepEqual([st.mode, st.scene], ['scene', 6]);

// brilho: branco → DP 22; cor → V do DP 24 mantendo h/s; mínimo 1% = 10; 0% desliga
const white = {...lamp, on: true, dps: {'21': 'white', '22': 700, '23': 300}};
assert.deepEqual(brightCmd(white, 40), {'20': true, '22': 400});
assert.deepEqual(brightCmd(white, 0.4), {'20': true, '22': 10});
assert.deepEqual(brightCmd(white, 0), {'20': false});
const col = {...strip, on: true, dps: {'21': 'colour', '24': '002703e801f4'}};
assert.deepEqual(brightCmd(col, 80), {'20': true, '24': '002703e80320'});

// cor: liga, entra no modo cor e mantém o brilho atual
assert.deepEqual(colourCmd(white, 120, 700), {'20': true, '21': 'colour', '24': hsvHex(120, 700, 700)});
// branco (só lâmpada): liga, modo branco, temperatura e o brilho atual
assert.deepEqual(whiteCmd({...lamp, on: true, dps: {'21': 'colour', '24': '0000000003e8'}}, 60),
                 {'20': true, '21': 'white', '23': 600, '22': 1000});

// efeitos: "Boa noite" é idêntico ao valor lido do Abajur em 2026-09-18
assert.equal(EFFECTS[0].data, '000e0d0000000000000000c80000');
for (const [i, e] of EFFECTS.entries()) {
  assert.equal(parseInt(e.data.slice(0, 2), 16), i, `número da cena ${i}`);
  assert.equal((e.data.length - 2) % 26, 0, `blocos de 26 dígitos em ${e.name}`);
}
assert.deepEqual(effectCmd(strip, 5), {'20': true, '21': 'scene', '25': EFFECTS[5].data});
// fita não tem branco regulável: só os efeitos coloridos
assert.deepEqual(effectsFor(strip).map(([i]) => i), [4, 5, 6, 7]);
assert.deepEqual(effectsFor(lamp).map(([i]) => i), [0, 1, 2, 3, 4, 5, 6, 7]);

console.log('codec ok');
