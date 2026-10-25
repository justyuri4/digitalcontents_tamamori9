const sample = `田中：新しい企画を来月から始めたいです。\n佐藤：準備の時間が足りるか心配です。\n井上：最初から全員に向けて始めなくてもよいと思います。\n田中：まずは小さく始めて、様子を見るのはどうでしょう？\n佐藤：それならできそうです。ただ、うまくいったかどうかを何で判断するか決めたいです。\n井上：参加した人の感想や、最後まで使ってくれた人数を見るのはどうでしょう。\n田中：では、今週中に対象と確認方法を決めましょう。`;
const transcript = document.querySelector('#transcript');
const responseArea = document.querySelector('#gpt-response');
const results = document.querySelector('#results');
const micButton = document.querySelector('#mic-button');
const recordingStatus = document.querySelector('#recording-status');
const audioFile = document.querySelector('#audio-file');
const fileStatus = document.querySelector('#file-status');
const speakerCount = document.querySelector('#speaker-count');
const speakerNameEditor = document.querySelector('#speaker-name-editor');
const speakerNameFields = document.querySelector('#speaker-name-fields');
const speakerNameStatus = document.querySelector('#speaker-name-status');
let currentStatus = 'stopped';
let lastTranscriptId = null;
let polling = false;
let activeRecording = false;
let finishingRecording = false;
let recordingStartAfterId = null;
let recordingBaseText = '';
let recordingAppendedText = '';
let recordingItems = [];
let lastTopicTranscript = '';
let pendingTopicAppend = '';
let topicSendTimer = null;
let topicRequestQueue = Promise.resolve();
let analysisTimer = null;
let analysisAbortController = null;
let renderedTopicCount = 0;

function renderGPTResponse() {
  const suggestionList = document.querySelector('#suggestion-list');
  const markerPattern = /(\[話題\d+\]|【(?:要約|決定事項|未決事項・課題|提案)】)/g;
  const parts = responseArea.value.trim().split(markerPattern);
  const topics = [];
  const unstructured = [];
  let topic = null;
  let section = null;

  for (const part of parts) {
    if (/^\[話題\d+\]$/.test(part)) {
      topic = { title: part.slice(1, -1), sections: [], text: '' };
      topics.push(topic);
      section = null;
    } else if (/^【(?:要約|決定事項|未決事項・課題|提案)】$/.test(part)) {
      section = { title: part.slice(1, -1), text: '' };
      if (topic) topic.sections.push(section);
      else unstructured.push(section);
    } else if (part) {
      if (section) section.text += part;
      else if (topic) topic.text += part;
      else unstructured.push(part);
    }
  }

  suggestionList.replaceChildren();
  if (!responseArea.value.trim()) {
    document.querySelector('#result-summary').textContent = '';
    results.classList.add('hidden');
    return;
  }

  document.querySelector('#result-summary').textContent = 'GPT APIからの出力';
  for (const item of topics) {
    const article = document.createElement('article');
    article.className = 'suggestion analysis-topic';
    const heading = document.createElement('h3');
    heading.className = 'analysis-topic-title';
    heading.textContent = item.title;
    article.append(heading);
    if (item.text.trim()) {
      const content = document.createElement('p');
      content.textContent = item.text.trim();
      article.append(content);
    }
    for (const itemSection of item.sections) {
      const sectionElement = document.createElement('section');
      sectionElement.className = 'analysis-section';
      const sectionHeading = document.createElement('h4');
      sectionHeading.textContent = itemSection.title;
      const content = document.createElement('p');
      content.textContent = itemSection.text.trim();
      sectionElement.append(sectionHeading, content);
      article.append(sectionElement);
    }
    suggestionList.append(article);
  }
  const fallbackText = unstructured.map(item => typeof item === 'string' ? item : `${item.title} ${item.text}`)
    .join('\n\n').trim();
  if (fallbackText) {
    const article = document.createElement('article');
    article.className = 'suggestion';
    const content = document.createElement('p');
    content.textContent = fallbackText;
    article.append(content);
    suggestionList.append(article);
  }
  const hasNewTopic = topics.length > renderedTopicCount;
  renderedTopicCount = topics.length;
  results.classList.remove('hidden');
  if (hasNewTopic) {
    requestAnimationFrame(() => {
      const topicHeadings = suggestionList.querySelectorAll('.analysis-topic-title');
      topicHeadings[topicHeadings.length - 1]
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }
}

responseArea.addEventListener('input', renderGPTResponse);

// すでに定義されている transcript にイベントを追加するだけにする
if (transcript) {
  transcript.addEventListener('input', handleTranscriptInput);
  transcript.addEventListener('blur', () => flushTopicAppend(true));
}

function appendTranscriptItem(item) {
  const addition = `${transcript.value && !transcript.value.endsWith('\n') ? '\n' : ''}${item.text}\n`;
  transcript.value += addition;
  lastTopicTranscript = transcript.value;
  queueTopicAppend(addition);
  scheduleTranscriptAnalysis();
  if (activeRecording) recordingAppendedText += addition;
}

async function applyFinalSpeakerLabels() {
  if (!activeRecording || finishingRecording || recordingStartAfterId === null) return;
  finishingRecording = true;
  try {
    const items = [];
    let fromId = recordingStartAfterId + 1;
    while (true) {
      const data = await requestJson(`/api/transcripts?from_id=${fromId}&limit=500`);
      items.push(...data.items);
      if (data.items.length < 500) break;
      fromId = data.items[data.items.length - 1].id + 1;
    }

    let finalAppendedText = '';
    let previousText = recordingBaseText;
    for (const item of items) {
      const addition = `${previousText && !previousText.endsWith('\n') ? '\n' : ''}${item.text}\n`;
      finalAppendedText += addition;
      previousText += addition;
    }
    recordingItems = items;
    if (
      transcript.value.startsWith(recordingBaseText) &&
      transcript.value.slice(recordingBaseText.length) === recordingAppendedText
    ) {
      transcript.value = recordingBaseText + finalAppendedText;
      resetTopicDetector(transcript.value);
    }
    if (items.length) lastTranscriptId = items[items.length - 1].id;
    showSpeakerNameEditor(items);
  } catch (error) {
    recordingStatus.textContent = `話者ラベルの再表示に失敗しました。SQLiteには反映済みです。(${error.message})`;
  } finally {
    activeRecording = false;
    finishingRecording = false;
    recordingStartAfterId = null;
  }
}

function showSpeakerNameEditor(items) {
  const speakerIds = [...new Set(items.flatMap(item =>
    [...item.text.matchAll(/話者(\d+)：/g)].map(match => Number(match[1]))
  ))].sort((a, b) => a - b);
  if (!speakerIds.length) return;
  speakerNameFields.replaceChildren();
  for (const id of speakerIds) {
    const label = document.createElement('label');
    label.className = 'speaker-name-field';
    label.textContent = `話者${id}`;
    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 40;
    input.dataset.speakerId = String(id);
    input.placeholder = id === 1
      ? '例：小坂さん'
      : id === 2
        ? '例：井手さん'
        : `例：話者${id}の名前`;
    label.append(input);
    speakerNameFields.append(label);
  }
  speakerNameStatus.textContent = '';
  speakerNameEditor.classList.remove('hidden');
}

document.querySelector('#save-speaker-names').addEventListener('click', async () => {
  const names = Object.fromEntries([...speakerNameFields.querySelectorAll('input')]
    .filter(input => input.value.trim())
    .map(input => [input.dataset.speakerId, input.value.trim()]));
  if (!Object.keys(names).length) {
    speakerNameStatus.textContent = '変更する人物名を入力してください。';
    return;
  }
  const button = document.querySelector('#save-speaker-names');
  button.disabled = true;
  try {
    await requestJson('/api/transcripts/rename-speakers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript_ids: recordingItems.map(item => item.id), names })
    });
    for (const [id, name] of Object.entries(names)) {
      transcript.value = transcript.value.replaceAll(`話者${id}：`, `${name}：`);
      for (const item of recordingItems) {
        item.text = item.text.replaceAll(`話者${id}：`, `${name}：`);
      }
    }
    resetTopicDetector(transcript.value);
    speakerNameStatus.textContent = '人物名を文字起こしを保存しました。';
  } catch (error) {
    speakerNameStatus.textContent = `保存できませんでした：${error.message}`;
  } finally {
    button.disabled = false;
  }
});

function setRecordingStatus(status, error, droppedChunks = 0) {
  currentStatus = status;
  const labels = {
    loading: ['WhisperX・Sherpa-ONNXモデルを読み込み中…', '読み込みを中止'],
    recording: ['録音中です。認識した文章を入力欄に追加しています。', '録音を停止'],
    stopping: ['録音と保存を終了しています…', '停止中'],
    stopped: ['録音していません。', '録音を開始'],
    error: [error ? `エラー：${error}` : '録音を開始できませんでした。', 'もう一度試す']
  };
  const [message, buttonLabel] = labels[status] || labels.stopped;
  const droppedMessage = droppedChunks > 0 ? ` 音声区間を${droppedChunks}件処理できませんでした。` : '';
  recordingStatus.textContent = message + droppedMessage;
  micButton.querySelector('strong').textContent = buttonLabel;
  micButton.disabled = status === 'stopping';
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const responseText = await response.text();
  let data = {};
  if (responseText.trim()) {
    try {
      data = JSON.parse(responseText);
    } catch {
      throw new Error(
        `サーバーがJSON以外の応答を返しました（HTTP ${response.status}）。${responseText.slice(0, 240)}`
      );
    }
  } else if (response.status !== 204) {
    throw new Error(`サーバーから空の応答が返りました（HTTP ${response.status} ${response.statusText}）。`);
  }
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

async function refreshRecording() {
  const state = await requestJson('/api/recording/status');
  if (lastTranscriptId === null) lastTranscriptId = state.latest_id;
  setRecordingStatus(state.status, state.error, state.dropped_chunks);

  const data = await requestJson(`/api/transcripts?after_id=${lastTranscriptId}`);
  for (const item of data.items) {
    appendTranscriptItem(item);
    lastTranscriptId = item.id;
  }
  if (activeRecording && state.status === 'stopped') {
    await applyFinalSpeakerLabels();
  } else if (activeRecording && state.status === 'error') {
    activeRecording = false;
    recordingStartAfterId = null;
  }
}

micButton.addEventListener('click', async () => {
  micButton.disabled = true;
  try {
    if (currentStatus === 'recording' || currentStatus === 'loading') {
      const data = await requestJson('/api/recording/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      setRecordingStatus(data.status);
      if (data.status === 'stopped') await applyFinalSpeakerLabels();
    } else {
      const data = await requestJson('/api/recording/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speaker_count: Number(speakerCount.value) || 2 })
      });
      lastTranscriptId = data.after_id;
      recordingStartAfterId = data.after_id;
      recordingBaseText = transcript.value;
      recordingAppendedText = '';
      recordingItems = [];
      speakerNameEditor.classList.add('hidden');
      activeRecording = true;
      setRecordingStatus(data.status);
    }
  } catch (error) {
    setRecordingStatus('error', error.message);
  } finally {
    micButton.disabled = false;
  }
});

audioFile.addEventListener('change', async () => {
  const file = audioFile.files[0];
  if (!file) return;
  if (file.size > 25 * 1024 * 1024) {
    fileStatus.textContent = '音声ファイルは25MB以下にしてください。';
    audioFile.value = '';
    return;
  }

  const formData = new FormData();
  formData.append('file', file);
  formData.append('speaker_count', String(Number(speakerCount.value) || 2));
  audioFile.disabled = true;
  fileStatus.textContent = `${file.name}を文字起こし中です…`;
  try {
    const data = await requestJson('/api/transcripts/upload', {
      method: 'POST',
      body: formData
    });
    fileStatus.textContent = data.saved
      ? `${file.name}の文字起こしを保存しました。`
      : '文字起こし結果が空だったため、保存しませんでした。';
    await pollApi();
    if (data.saved && data.transcript_id) {
      recordingItems = [{ id: data.transcript_id, text: data.text }];
      showSpeakerNameEditor(recordingItems);
    }
  } catch (error) {
    fileStatus.textContent = `音声ファイルを処理できませんでした：${error.message}`;
  } finally {
    audioFile.disabled = false;
    audioFile.value = '';
  }
});

async function pollApi() {
  if (polling) return;
  polling = true;
  try {
    await refreshRecording();
  } catch (error) {
    recordingStatus.textContent = `APIに接続できません。backend/main.py を起動してください。(${error.message})`;
  } finally {
    polling = false;
  }
}

pollApi();
window.setInterval(pollApi, 1200);

document.querySelector('#load-sample').addEventListener('click', () => {
  transcript.value = sample;
  transcript.dispatchEvent(new Event('input', { bubbles: true }));
  transcript.focus();
});
document.querySelector('#clear-text').addEventListener('click', () => {
  transcript.value = '';
  resetTopicDetector('');
  responseArea.value = '';
  renderGPTResponse();
  speakerCount.value = '2';
  speakerNameFields.replaceChildren();
  speakerNameEditor.classList.add('hidden');
  speakerNameStatus.textContent = '';
  fileStatus.textContent = '';
  audioFile.value = '';
  document.querySelector('#result-summary').textContent = '';
  document.querySelector('#suggestion-list').replaceChildren();
  results.classList.add('hidden');
  if (currentStatus === 'stopped') setRecordingStatus('stopped');
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

function handleTranscriptInput() {
  const currentText = transcript.value;
  if (currentText.startsWith(lastTopicTranscript)) {
    const addition = currentText.slice(lastTopicTranscript.length);
    lastTopicTranscript = currentText;
    queueTopicAppend(addition);
    scheduleTranscriptAnalysis();
    return;
  }

  resetTopicDetector(currentText);
}

function queueTopicAppend(text) {
  pendingTopicAppend += text;
  window.clearTimeout(topicSendTimer);
  topicSendTimer = window.setTimeout(flushTopicAppend, 350);
}

function flushTopicAppend(force = false) {
  const lastCompleteLine = pendingTopicAppend.lastIndexOf('\n');
  if (!force && lastCompleteLine < 0) return;

  const splitAt = force ? pendingTopicAppend.length : lastCompleteLine + 1;
  const addedText = pendingTopicAppend.slice(0, splitAt);
  pendingTopicAppend = pendingTopicAppend.slice(splitAt);
  if (!addedText.trim()) return;

  topicRequestQueue = topicRequestQueue.then(async () => {
    const data = await requestJson('/topic', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ added_text: addedText })
    });
    if (data.completed_topics.length) {
      console.log('検出した話題の区切り:', data.completed_topics);
    }
  }).catch((error) => {
    console.error('話題検出に失敗しました:', error);
  });
}

function resetTopicDetector(text) {
  window.clearTimeout(topicSendTimer);
  pendingTopicAppend = '';
  lastTopicTranscript = text;
  topicRequestQueue = topicRequestQueue.then(() => requestJson('/topic/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript: text })
  })).catch((error) => {
    console.error('話題検出の文脈同期に失敗しました:', error);
  });
  scheduleTranscriptAnalysis();
}

function scheduleTranscriptAnalysis() {
  window.clearTimeout(analysisTimer);
  analysisTimer = window.setTimeout(triggerTranscriptAnalysis, 700);
}

async function triggerTranscriptAnalysis() {
  const text = transcript.value.trim();
  if (!text) {
    if (analysisAbortController) analysisAbortController.abort();
    responseArea.value = '';
    renderGPTResponse();
    return;
  }

  if (analysisAbortController) analysisAbortController.abort();
  analysisAbortController = new AbortController();
  const signal = analysisAbortController.signal;
  responseArea.value = 'ChatGPTで解析しています…';
  renderGPTResponse();

  try {
    const response = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript: text }),
      signal
    });
    if (!response.ok) {
      const body = await response.text();
      let detail = body;
      try {
        detail = JSON.parse(body).detail || body;
      } catch {}
      throw new Error(`HTTP ${response.status}: ${detail}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const analyses = [];
    const handleEvent = (line) => {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.error) throw new Error(event.error);
      if (typeof event.analysis === 'string' && event.analysis.trim()) {
        if (event.integration_state === '統合した' && analyses.length) {
          analyses[analyses.length - 1] = event.analysis.trim();
        } else {
          analyses.push(event.analysis.trim());
        }
        responseArea.value = analyses.join('\n\n');
        renderGPTResponse();
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      lines.forEach(handleEvent);
      if (done) break;
    }
    handleEvent(buffer);
    if (!analyses.length) {
      responseArea.value = '解析結果がありませんでした。';
      renderGPTResponse();
    }
  } catch (error) {
    if (error.name === 'AbortError') return;
    console.error('/analyzeの呼び出しに失敗しました:', error);
    responseArea.value = `ChatGPT解析に失敗しました: ${error.message}`;
    renderGPTResponse();
  }
}

resetTopicDetector(transcript.value);