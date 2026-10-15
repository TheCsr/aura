(() => {
  const enableButton = document.getElementById('enable');
  const replayButton = document.getElementById('replay');
  const stopButton = document.getElementById('stop');
  const fallbackButton = document.getElementById('fallback');
  const statusLabel = document.getElementById('status');
  const voiceLabel = document.getElementById('voice');
  const synthesis = window.speechSynthesis;
  let prompt = null;
  let promptKey = '';
  let enabled = false;
  let mode = 'browser_speech';
  let audioData = null;
  let audioPlayer = null;
  let utterance = null;
  let generation = 0;
  let startupTimer = null;
  let fallbackPending = false;
  let sequence = 0;
  const attempted = new Set();

  function send(type, payload) {
    window.parent.postMessage({ isStreamlitMessage: true, type, ...payload }, '*');
  }

  function resize() {
    send('streamlit:setFrameHeight', { height: document.body.scrollHeight + 24 });
  }

  function showStatus(text, error = false) {
    statusLabel.textContent = text;
    statusLabel.dataset.error = String(error);
    resize();
  }

  function notify(action, provider, status) {
    if (!prompt) return;
    send('streamlit:setComponentValue', {
      dataType: 'json',
      value: {
        action, provider, status,
        event_id: prompt.event_id,
        subject_id: prompt.subject_id,
        conversation_id: prompt.conversation_id,
        notification_id: `${Date.now()}-${++sequence}`,
      },
    });
  }

  function rememberAttempt() {
    attempted.add(promptKey);
    try { sessionStorage.setItem(`aura-speaker:${promptKey}`, 'attempted'); } catch (_) {}
  }

  function alreadyAttempted() {
    try { return attempted.has(promptKey) || sessionStorage.getItem(`aura-speaker:${promptKey}`) !== null; }
    catch (_) { return attempted.has(promptKey); }
  }

  function swedishVoice() {
    const voices = synthesis ? synthesis.getVoices() : [];
    const voice = voices.find(item => /^sv([-_]|$)/i.test(item.lang));
    voiceLabel.textContent = voice ? `${voice.name} (${voice.lang})` : 'No Swedish browser voice is available.';
    return voice;
  }

  function halt(report = true) {
    const wasPlaying = utterance !== null || audioPlayer !== null;
    generation += 1;
    clearTimeout(startupTimer);
    if (utterance) {
      utterance.onstart = null;
      utterance.onend = null;
      utterance.onerror = null;
      utterance = null;
    }
    if (synthesis) synthesis.cancel();
    if (audioPlayer) {
      audioPlayer.onended = null;
      audioPlayer.onerror = null;
      audioPlayer.pause();
      audioPlayer = null;
    }
    stopButton.disabled = true;
    fallbackPending = false;
    if (report && prompt && wasPlaying) {
      rememberAttempt();
      showStatus('Playback stopped.');
      notify('playback', mode, 'stopped');
    }
  }

  function speak() {
    if (!prompt) return;
    halt(false);
    mode = 'browser_speech';
    const voice = swedishVoice();
    if (!synthesis || !voice) {
      rememberAttempt();
      showStatus('No Swedish voice is available. Choose Google Translate (demo) or install a Swedish voice.', true);
      notify('playback', mode, 'blocked');
      return;
    }
    rememberAttempt();
    const token = generation;
    let started = false;
    utterance = new SpeechSynthesisUtterance(prompt.text);
    utterance.lang = 'sv-SE';
    utterance.voice = voice;
    utterance.rate = 0.9;
    utterance.onstart = () => {
      if (token !== generation) return;
      started = true;
      clearTimeout(startupTimer);
      showStatus('Playing message...');
      notify('playback', mode, 'started');
    };
    utterance.onend = () => {
      if (token !== generation) return;
      generation += 1;
      clearTimeout(startupTimer);
      utterance = null;
      stopButton.disabled = true;
      showStatus(started ? 'Message playback finished.' : 'Select Replay to start audio.', !started);
      notify('playback', mode, started ? 'completed' : 'blocked');
    };
    utterance.onerror = event => {
      if (token !== generation) return;
      halt(false);
      showStatus('Audio could not be played. Select Replay or choose Google Translate (demo).', true);
      notify('playback', mode, event.error === 'not-allowed' ? 'blocked' : 'failed');
    };
    showStatus('Starting audio...');
    stopButton.disabled = false;
    startupTimer = setTimeout(() => {
      if (token !== generation || started) return;
      halt(false);
      showStatus('The browser blocked audio. Select Replay to try again.', true);
      notify('playback', mode, 'blocked');
    }, 5000);
    try { synthesis.speak(utterance); }
    catch (_) {
      halt(false);
      showStatus('Audio could not start. Select Replay to try again.', true);
      notify('playback', mode, 'failed');
    }
  }

  async function playFallback() {
    if (!prompt || !audioData) return;
    halt(false);
    mode = 'gtts';
    rememberAttempt();
    const token = generation;
    let started = false;
    audioPlayer = new Audio(`data:audio/mpeg;base64,${audioData}`);
    audioPlayer.onplaying = () => {
      if (token !== generation) return;
      started = true;
      showStatus('Playing message with Google Translate (demo)...');
      notify('playback', mode, 'started');
    };
    audioPlayer.onended = () => {
      if (token !== generation) return;
      generation += 1;
      audioPlayer = null;
      stopButton.disabled = true;
      showStatus(started ? 'Message playback finished.' : 'Audio could not be played.', !started);
      notify('playback', mode, started ? 'completed' : 'failed');
    };
    audioPlayer.onerror = () => {
      if (token !== generation) return;
      halt(false);
      showStatus('The audio file could not be played. Try again.', true);
      notify('playback', mode, 'failed');
    };
    stopButton.disabled = false;
    try { await audioPlayer.play(); }
    catch (_) {
      if (token !== generation) return;
      halt(false);
      showStatus('Select Replay to allow audio playback.', true);
      notify('playback', mode, 'blocked');
    }
  }

  enableButton.onclick = () => {
    enabled = !enabled;
    enableButton.textContent = enabled ? 'Disable audio' : 'Enable audio';
    if (!enabled) {
      halt();
      showStatus('Audio is off.');
    } else if (prompt) speak();
    else showStatus('Audio is enabled. Waiting for a message.');
  };
  replayButton.onclick = () => { if (mode === 'gtts' && audioData) playFallback(); else speak(); };
  stopButton.onclick = () => { halt(); rememberAttempt(); };
  fallbackButton.onclick = () => {
    halt(false);
    mode = 'gtts';
    if (audioData) playFallback();
    else {
      fallbackPending = true;
      showStatus('Creating demo audio...');
      notify('fallback', mode, 'requested');
    }
  };

  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.data?.type !== 'streamlit:render') return;
    const args = event.data.args || {};
    const next = args.prompt || null;
    const nextKey = next ? JSON.stringify([next.subject_id, next.event_id, next.conversation_id]) : '';
    if (nextKey !== promptKey) {
      halt(false);
      prompt = next;
      promptKey = nextKey;
      audioData = null;
      mode = 'browser_speech';
      replayButton.disabled = !prompt;
      fallbackButton.disabled = !prompt;
      if (prompt && prompt.playback?.delivered) rememberAttempt();
      showStatus(prompt ? 'Message is ready.' : 'No message is waiting.');
      if (prompt && enabled && !alreadyAttempted()) speak();
    }
    if (args.audio_base64 && args.audio_base64 !== audioData) {
      audioData = args.audio_base64;
      if (fallbackPending) playFallback();
    }
    if (args.fallback_error && fallbackPending) {
      fallbackPending = false;
      showStatus('Demo voice is unavailable. Try again.', true);
      notify('playback', 'gtts', 'failed');
    }
    resize();
  });
  if (synthesis) synthesis.addEventListener('voiceschanged', () => { swedishVoice(); resize(); });
  window.addEventListener('pagehide', () => halt(false));
  swedishVoice();
  send('streamlit:componentReady', { apiVersion: 1 });
  resize();
})();