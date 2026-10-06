const sample = `田中：新しい企画を来月から始めたいです。\n佐藤：準備の時間が足りるか心配です。\n井上：最初から全員に向けて始めなくてもよいと思います。\n田中：まずは小さく始めて、様子を見るのはどうでしょう？\n佐藤：それならできそうです。ただ、うまくいったかどうかを何で判断するか決めたいです。\n井上：参加した人の感想や、最後まで使ってくれた人数を見るのはどうでしょう。\n田中：では、今週中に対象と確認方法を決めましょう。`;
const transcript = document.querySelector('#transcript');
const results = document.querySelector('#results');

document.querySelector('#load-sample').addEventListener('click', () => {
  transcript.value = sample;
  transcript.focus();
});
document.querySelector('#clear-text').addEventListener('click', () => {
  transcript.value = '';
  results.classList.add('hidden');
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
