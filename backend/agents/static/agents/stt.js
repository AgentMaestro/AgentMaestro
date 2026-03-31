(function () {
  // Config
  const FRAME_MS = 30; // frame length for VAD
  const SAMPLE_RATE_TARGET = 16000; // server expects 16 kHz PCM16
  const START_ENERGY = 0.01; // RMS threshold to consider speech started (tune)
  const END_SILENCE_MS = 400; // ms of silence to end utterance
  const MAX_UTTERANCE_MS = 15000; // safety cap
  const STT_ENDPOINT = (agentSlug) => `/agents/${agentSlug}/stt/transcribe/`;

  // DOM hooks (attempt to insert checkbox if missing)
  function ensureCheckbox() {
    let checkbox = document.getElementById('enable-stt-checkbox');
    if (checkbox) return checkbox;
    // Try to find a sidebar to insert into
    const sidebar = document.querySelector('.agent-sidebar') || document.querySelector('#left-sidebar') || document.querySelector('#sidebar') || document.body;
    const container = document.createElement('div');
    container.style.margin = '8px 0';
    container.innerHTML = `
      <label style="font-size:13px;display:flex;align-items:center;gap:8px;">
        <input id="enable-stt-checkbox" type="checkbox"> <span>Enable Speech-to-Text</span>
        <span id="stt-status" style="margin-left:8px;font-size:12px;color:#666">Idle</span>
      </label>
    `;
    sidebar.prepend(container);
    return document.getElementById('enable-stt-checkbox');
  }

  const checkbox = ensureCheckbox();
  const statusEl = document.getElementById('stt-status');
  const promptInput = () => document.querySelector('#prompt-input') || document.querySelector('textarea[name="prompt"]') || document.querySelector('input[name="prompt"]');

  // Runtime state
  let audioContext = null;
  let sourceNode = null;
  let processor = null;
  let mediaStream = null;
  let recording = false;
  let buffer = []; // Float32 arrays
  let silenceMs = 0;
  let startedSpeech = false;
  let agentName = (document.querySelector('[data-agent-name]') || {}).dataset?.agentName || document.title || 'jeeves';
  agentName = agentName.toLowerCase();

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg;
    console.log('[stt] ' + msg);
  }

  function floatTo16BitPCM(float32Array) {
    const l = float32Array.length;
    const buf = new ArrayBuffer(l * 2);
    const view = new DataView(buf);
    let offset = 0;
    for (let i = 0; i < l; i++, offset += 2) {
      let s = Math.max(-1, Math.min(1, float32Array[i]));
      s = s < 0 ? s * 0x8000 : s * 0x7fff;
      view.setInt16(offset, s, true);
    }
    return new Uint8Array(buf);
  }

  function mergeBuffers(buffers) {
    let length = 0;
    for (let i = 0; i < buffers.length; i++) length += buffers[i].length;
    const result = new Float32Array(length);
    let offset = 0;
    for (let i = 0; i < buffers.length; i++) {
      result.set(buffers[i], offset);
      offset += buffers[i].length;
    }
    return result;
  }

  function downsampleBuffer(buffer, inputSampleRate, outputSampleRate) {
    const merged = mergeBuffers(buffer);
    if (outputSampleRate === inputSampleRate) return merged;
    const sampleRateRatio = inputSampleRate / outputSampleRate;
    const newLength = Math.round(merged.length / sampleRateRatio);
    const result = new Float32Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
      let accum = 0, count = 0;
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < merged.length; i++) {
        accum += merged[i];
        count++;
      }
      result[offsetResult] = count ? accum / count : 0;
      offsetResult++;
      offsetBuffer = nextOffsetBuffer;
    }
    return result;
  }

  function rms(buffer) {
    let sum = 0;
    for (let i = 0; i < buffer.length; i++) {
      sum += buffer[i] * buffer[i];
    }
    return Math.sqrt(sum / buffer.length);
  }

  function startCapture() {
    if (recording) return;
    buffer = [];
    silenceMs = 0;
    startedSpeech = false;
    setStatus('Requesting microphone...');
    navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
      mediaStream = stream;
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      sourceNode = audioContext.createMediaStreamSource(stream);
      const frameSize = Math.round(audioContext.sampleRate * FRAME_MS / 1000);
      processor = audioContext.createScriptProcessor(frameSize, 1, 1);
      processor.onaudioprocess = (evt) => {
        const input = evt.inputBuffer.getChannelData(0);
        const inputRms = rms(input);
        if (!startedSpeech) {
          if (inputRms > START_ENERGY) {
            startedSpeech = true;
            setStatus('Speaking...');
            buffer.push(new Float32Array(input));
            silenceMs = 0;
          }
        } else {
          buffer.push(new Float32Array(input));
          if (inputRms < START_ENERGY * 0.6) {
            silenceMs += FRAME_MS;
            if (silenceMs >= END_SILENCE_MS) {
              stopAndSendUtterance();
            }
          } else {
            silenceMs = 0;
          }
          const totalMs = buffer.length * FRAME_MS;
          if (totalMs > MAX_UTTERANCE_MS) {
            setStatus('Utterance too long, sending');
            stopAndSendUtterance();
          }
        }
      };
      sourceNode.connect(processor);
      processor.connect(audioContext.destination);
      recording = true;
      setStatus('Listening');
    }).catch((err) => {
      console.error('getUserMedia error', err);
      setStatus('Microphone access denied or error');
    });
  }

  function stopCapture() {
    if (!recording) return;
    if (processor) {
      processor.disconnect();
      processor.onaudioprocess = null;
      processor = null;
    }
    if (sourceNode) {
      try { sourceNode.disconnect(); } catch(_) {}
      sourceNode = null;
    }
    if (audioContext) {
      try { audioContext.close(); } catch(_) {}
      audioContext = null;
    }
    if (mediaStream) {
      mediaStream.getTracks().forEach(t => t.stop());
      mediaStream = null;
    }
    recording = false;
    setStatus('Stopped');
  }

  function stopAndSendUtterance() {
    if (!buffer.length) {
      startedSpeech = false;
      setStatus('Idle');
      return;
    }
    setStatus('Processing utterance...');
    const localBuffer = buffer.slice();
    buffer = [];
    startedSpeech = false;
    silenceMs = 0;

    const inputSampleRate = audioContext ? audioContext.sampleRate : 48000;
    const float32 = downsampleBuffer(localBuffer, inputSampleRate, SAMPLE_RATE_TARGET);
    const pcm16 = floatTo16BitPCM(float32);
    const agentSlug = (document.body.dataset.agentSlug || '').trim() || window.location.pathname.split('/').filter(Boolean).pop();
    const endpoint = STT_ENDPOINT(agentSlug);
    setStatus('Sending to STT...');
    fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        'X-Sample-Rate': String(SAMPLE_RATE_TARGET),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: pcm16.buffer
    }).then(resp => resp.json()).then(data => {
      setStatus('Transcribed');
      const text = (data && data.text) ? data.text.trim() : '';
      handleTranscription(text);
    }).catch(err => {
      console.error('STT request error', err);
      setStatus('STT error');
    }).finally(() => {
      setTimeout(() => setStatus(recording ? 'Listening' : 'Idle'), 250);
    });
  }

  function handleTranscription(text) {
    if (!text) {
      setStatus('No speech recognized');
      return;
    }
    const low = text.toLowerCase();
    const pattern = new RegExp(`^(?:hey\\s+)?${escapeRegExp(agentName)}\\b\\s*`, 'i');
    if (pattern.test(low)) {
      const cleaned = text.replace(pattern, '').trim();
      const input = promptInput();
      if (input) {
        input.value = (input.value ? input.value + ' ' : '') + cleaned;
        input.focus();
        setStatus('Prompt filled (wake word detected)');
      } else {
        console.warn('Prompt input not found to insert text');
      }
    } else {
      console.log('Wake word not found in transcription:', text);
      setStatus('Wake-word not detected');
    }
  }

  function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&');
  }

  if (checkbox) {
    checkbox.addEventListener('change', (e) => {
      if (e.target.checked) startCapture(); else stopCapture();
    });
  } else {
    console.warn('STT checkbox element not found (id=enable-stt-checkbox)');
  }

  window._stt = { start: startCapture, stop: stopCapture };
})();
