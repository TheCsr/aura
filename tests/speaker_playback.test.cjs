const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function browser({ voices = [{ name: 'Swedish', lang: 'sv-SE' }], playError = null } = {}) {
  const elements = Object.fromEntries(['enable', 'replay', 'stop', 'fallback', 'status', 'voice'].map(name => [name, { dataset: {}, disabled: false }]));
  const listeners = {};
  const messages = [];
  const speeches = [];
  const audios = [];
  const timers = new Map();
  const storage = new Map();
  const parent = { postMessage: message => messages.push(message) };
  const synthesis = {
    getVoices: () => voices,
    cancel: () => {},
    speak: speech => speeches.push(speech),
    addEventListener: () => {},
  };
  class Audio {
    constructor(source) { this.source = source; audios.push(this); }
    play() {
      if (playError) return Promise.reject(playError);
      this.onplaying();
      return Promise.resolve();
    }
    pause() { this.paused = true; }
  }
  const context = {
    document: { getElementById: name => elements[name], body: { scrollHeight: 160 } },
    window: { parent, speechSynthesis: synthesis, addEventListener: (name, listener) => { listeners[name] = listener; } },
    sessionStorage: { setItem: (key, value) => storage.set(key, value), getItem: key => storage.get(key) ?? null },
    SpeechSynthesisUtterance: function SpeechSynthesisUtterance(text) { this.text = text; },
    Audio,
    setTimeout: callback => { const token = Symbol(); timers.set(token, callback); return token; },
    clearTimeout: token => timers.delete(token),
  };
  vm.runInNewContext(readFileSync(path.join(__dirname, '../aura/voice/speaker_frontend/speaker.js'), 'utf8'), context);
  return {
    elements, speeches, audios, timers,
    values: () => messages.filter(message => message.type === 'streamlit:setComponentValue').map(message => message.value),
    render: (prompt, args = {}) => listeners.message({ source: parent, data: { type: 'streamlit:render', args: { prompt, ...args } } }),
  };
}

const prompt = { event_id: 'event-1', subject_id: 'resident-1', conversation_id: 'conversation-1', text: 'Hej, hur mar du?' };

test('enabled speaker plays each new prompt once, not on polling updates', () => {
  const page = browser();
  page.elements.enable.onclick();
  page.render(prompt);
  const speech = page.speeches[0];
  assert.equal(speech.text, prompt.text);
  assert.equal(speech.lang, 'sv-SE');
  speech.onstart();
  for (let index = 0; index < 10; index += 1) page.render(prompt);
  assert.equal(page.speeches.length, 1);
  assert.equal(page.values().filter(value => value.status === 'completed').length, 0);
  speech.onend();
  page.render(prompt);
  assert.equal(page.speeches.length, 1);
  assert.equal(page.values().filter(value => value.status === 'completed').length, 1);
  page.elements.replay.onclick();
  assert.equal(page.speeches.length, 2);
});

test('Stop cancels speech and stale completion cannot confirm playback', () => {
  const page = browser();
  page.render(prompt);
  page.elements.replay.onclick();
  const lateEnd = page.speeches[0].onend;
  page.speeches[0].onstart();
  page.elements.stop.onclick();
  lateEnd();
  page.render(prompt);
  assert.equal(page.speeches.length, 1);
  assert.equal(page.values().at(-1).status, 'stopped');
  assert.equal(page.values().some(value => value.status === 'completed'), false);
});

test('a new conversation cancels the old one and ignores its callbacks', () => {
  const page = browser();
  page.elements.enable.onclick();
  page.render(prompt);
  const lateEnd = page.speeches[0].onend;
  page.render({ ...prompt, event_id: 'event-2', conversation_id: 'conversation-2' });
  lateEnd();
  assert.equal(page.speeches.length, 2);
  assert.equal(page.values().some(value => value.status === 'completed'), false);
  page.render(null);
  assert.equal(page.elements.replay.disabled, true);
});

test('missing Swedish voice is visible and does not fabricate completion', () => {
  const page = browser({ voices: [{ name: 'English', lang: 'en-GB' }] });
  page.elements.enable.onclick();
  page.render(prompt);
  page.render(prompt);
  assert.equal(page.speeches.length, 0);
  assert.equal(page.elements.status.dataset.error, 'true');
  assert.equal(page.values().at(-1).status, 'blocked');
});

test('end before start is blocked and a late start cannot revive the attempt', () => {
  const page = browser();
  page.render(prompt);
  page.elements.replay.onclick();
  const speech = page.speeches[0];
  speech.onend();
  speech.onstart();
  assert.equal(page.values().at(-1).status, 'blocked');
  assert.equal(page.elements.stop.disabled, true);
});

test('autoplay blocked or speech startup timeout is not completion', () => {
  const page = browser();
  page.render(prompt);
  page.elements.replay.onclick();
  page.speeches[0].onerror({ error: 'not-allowed' });
  assert.equal(page.values().at(-1).status, 'blocked');
  page.elements.replay.onclick();
  for (const callback of page.timers.values()) callback();
  assert.equal(page.values().at(-1).status, 'blocked');
  assert.equal(page.values().some(value => value.status === 'completed'), false);
});

test('gTTS fallback requests audio explicitly and reports only ended playback', async () => {
  const page = browser({ voices: [] });
  page.render(prompt);
  assert.equal(page.audios.length, 0);
  page.elements.fallback.onclick();
  assert.equal(page.values().at(-1).action, 'fallback');
  page.render(prompt, { audio_base64: 'dGVzdA==' });
  await Promise.resolve();
  assert.equal(page.audios.length, 1);
  assert.equal(page.values().at(-1).status, 'started');
  page.render(prompt, { audio_base64: 'dGVzdA==' });
  assert.equal(page.audios.length, 1);
  page.audios[0].onended();
  assert.equal(page.values().at(-1).provider, 'gtts');
  assert.equal(page.values().at(-1).status, 'completed');
});

test('blocked MP3 playback remains undelivered and offers manual replay', async () => {
  const page = browser({ playError: new Error('NotAllowedError') });
  page.render(prompt);
  page.elements.fallback.onclick();
  page.render(prompt, { audio_base64: 'dGVzdA==' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.values().at(-1).status, 'blocked');
  assert.equal(page.values().some(value => value.status === 'completed'), false);
});