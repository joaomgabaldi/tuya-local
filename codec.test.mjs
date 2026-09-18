// node codec.test.mjs — testa a tradução entre os DPs crus e o que a página mostra/manda
import assert from 'node:assert/strict';
import {hsvHex, parseHsv, readLight, brightCmd, colourCmd, whiteCmd, effectCmd, stateDps} from './codec.js';

const lamp = {dp: '20', codes: {switch_led: '20', work_mode: '21', bright_value_v2: '22', temp_value_v2: '23',
  colour_data_v2: '24', scene_data_v2: '25'}};
const strip = {dp: '20', codes: {switch_led: '20', work_mode: '21', colour_data: '24', scene_data: '25'},
  // efeitos de fábrica capturados do app (effects.json): cada modelo tem a sua lista, com os nomes do app
  effects: [{name: 'Boa noite', data: '000D0d00002e03e802cc00000000'}, {name: 'Luz Noturna', data: '08aa'}, {name: 'Céu azul', data: '14bb'}]};

// hex hhhhssssvvvv
assert.equal(hsvHex(277, 860, 1000), '0115035c03e8');
assert.deepEqual(parseHsv('002703E801F4'), {h: 39, s: 1000, v: 500});
assert.deepEqual(parseHsv(undefined), {h: 0, s: 0, v: 1000});

// leitura: branco usa o DP de brilho; cor usa o V; cena devolve o índice
let st = readLight({...lamp, on: true, dps: {'21': 'white', '22': 700, '23': 300, '24': '0115035c03e8'}});
assert.deepEqual([st.mode, st.bright, st.temp], ['white', 70, 30]);
st = readLight({...lamp, on: true, dps: {'21': 'colour', '22': 700, '24': '0115035c01f4'}});
assert.deepEqual([st.mode, st.bright, st.h, st.s], ['colour', 50, 277, 860]);
// efeito ativo reconhecido pelo valor inteiro (a fita informa maiúsculas misturadas); fora da lista = -1
st = readLight({...strip, on: true, dps: {'21': 'scene', '25': '000d0D00002E03E802CC00000000'}});
assert.deepEqual([st.mode, st.effect], ['scene', 0]);
assert.equal(readLight({...strip, on: true, dps: {'21': 'scene', '25': '14bb'}}).effect, 2);  // número 20: a posição vale, não o número
assert.equal(readLight({...strip, on: true, dps: {'21': 'scene', '25': '99zz'}}).effect, -1);
// a fita reescreve o que recebe (troca a velocidade, ou devolve só um resumo "NN466401..."), mas preserva o número:
// o reconhecimento é pelo número do efeito (2 primeiros dígitos, únicos dentro da lista de cada modelo)
assert.equal(readLight({...strip, on: true, dps: {'21': 'scene', '25': '14466401000003e803e803e803e8'}}).effect, 2);

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

// efeito: manda exatamente o texto capturado do app
assert.deepEqual(effectCmd(strip, 2), {'20': true, '21': 'scene', '25': '14bb'});

// o que uma cena guarda: só o estado, e o DP de cena só em modo cena (a fita entra em modo cena com qualquer
// escrita nele, mesmo junto de work_mode=colour)
const full = {'20': true, '21': 'colour', '22': 700, '23': 300, '24': '00d7038401f4', '25': '14bb'};
assert.deepEqual(stateDps({...lamp, on: true, dps: full}), {'20': true, '21': 'colour', '24': '00d7038401f4'});
assert.deepEqual(stateDps({...lamp, on: true, dps: {...full, '21': 'white'}}), {'20': true, '21': 'white', '22': 700, '23': 300});
assert.deepEqual(stateDps({...strip, on: true, dps: {...full, '21': 'scene'}}), {'20': true, '21': 'scene', '25': '14bb'});
assert.deepEqual(stateDps({...lamp, on: false, dps: {...full, '20': false}}), {'20': false});
assert.deepEqual(stateDps({dp: '1', codes: {switch_1: '1'}, on: true, dps: {'1': true, '9': 300}}), {'1': true});

console.log('codec ok');
