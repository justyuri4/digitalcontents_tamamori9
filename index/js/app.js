const sample = `田中：新しい企画を来月から始めたいです。\n佐藤：準備の時間が足りるか心配です。\n井上：最初から全員に向けて始めなくてもよいと思います。\n田中：まずは小さく始めて、様子を見るのはどうでしょう？\n佐藤：それならできそうです。ただ、うまくいったかどうかを何で判断するか決めたいです。\n井上：参加した人の感想や、最後まで使ってくれた人数を見るのはどうでしょう。\n田中：では、今週中に対象と確認方法を決めましょう。`;
const transcript = document.querySelector('#transcript');
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
  document.querySelector('#gpt-response').value = '';
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

function makeSuggestions(text) {
  const items = [];
  if (/時間|足り|人手|負担|リソース|準備/.test(text)) {
    items.push({
      title: '小さく始めて、段階的に広げる',
      body: 'まず対象を少人数に絞って試します。準備にかかる時間や運用上の問題を確認してから、対象を広げると負担を抑えられます。',
      next: '次の会議で、試す対象と期間を決める'
    });
  }
  if (/効果|判断|指標|測|確認|感想|決めきれ|決まら|未決/.test(text)) {
    items.push({
      title: '成功したかを確認する方法を決める',
      body: '利用した人の感想と、最後まで利用した人数など、確認しやすい項目を決めます。試す前に基準を共有しておくと、次の判断につなげやすくなります。',
      next: '確認する項目と担当者を決める'
    });
  }
  if (/いつ|期限|来週|来月|次回|保留|判断/.test(text)) {
    items.push({
      title: '次に話し合う日を決めておく',
      body: 'まだ決められない項目は、追加で必要な情報と担当者を明確にし、確認する日を設定します。',
      next: '未決の項目ごとに担当者と期限を記録する'
    });
  }
  if (!items.length) {
    items.push({
      title: '未決の点と、決めるために必要な情報を整理する',
      body: '会話の中で結論が出なかった点を一つずつ書き出し、選択肢や判断に必要な情報を確認します。',
      next: '項目ごとに担当者と確認期限を決める'
    });
  }
  return items.slice(0, 3);
}

document.querySelector('#generate').addEventListener('click', () => {
  const text = transcript.value.trim();
  if (!text) {
    transcript.focus();
    transcript.setCustomValidity('会議の内容を入力してください。');
    transcript.reportValidity();
    transcript.setCustomValidity('');
    return;
  }

  const suggestions = makeSuggestions(text);
  document.querySelector('#result-summary').textContent = `${suggestions.length}件の解決案を作成しました。`;
  document.querySelector('#suggestion-list').innerHTML = suggestions.map(item => `
    <article class="suggestion">
      <h3>${item.title}</h3>
      <p>${item.body}</p>
      <p class="next-step"><strong>次にすること：</strong>${item.next}</p>
    </article>
  `).join('');
  results.classList.remove('hidden');
  results.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

function handleTranscriptInput() {
  const currentText = transcript.value;
  if (currentText.startsWith(lastTopicTranscript)) {
    const addition = currentText.slice(lastTopicTranscript.length);
    lastTopicTranscript = currentText;
    queueTopicAppend(addition);
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
}

resetTopicDetector(transcript.value);