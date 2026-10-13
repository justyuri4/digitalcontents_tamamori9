# リアルタイム音声文字起こし（macOS）

マイクの音声を5秒ごとに区切り、Mac上で動作するWhisperで日本語に文字起こしして、`conversation.db` に保存します。FastAPIのサーバーはMac内（`127.0.0.1`）だけで動作し、音声を外部サーバーへ送りません。録音データはファイルとして保存せず、文字起こし結果だけSQLiteに保存します。

## 動作環境

- Apple Silicon搭載Mac
- Python 3.11
- Homebrew

## インストール

プロジェクトのフォルダーでターミナルを開き、次を実行します。

```bash
brew install ffmpeg python@3.11
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

PythonはWhisperが互換性を明記している3.11を使います。`requirements.txt`ではWhisperを`20250625`、FastAPIを`0.143.0`、Uvicornを`0.54.0`、アップロード処理用の`python-multipart`を`0.0.32`に固定し、sounddeviceは0.5系、NumPyは1.xに制限しています。FastAPIとUvicornでHTMLの配信とJSON APIを実行します。音声を扱うためにFFmpegが必要です。PyTorchなどの依存関係もpipでインストールされます。初回のライブラリ導入とWhisperモデル取得にはインターネット接続が必要ですが、録音と文字起こしの実行時に音声を外部APIへ送りません。

## VS Codeと仮想環境

ターミナルのプロンプト先頭に`(.venv)`と表示されていれば、そのターミナルでは仮想環境が有効です。表示がない場合は、プロジェクトのルートで次を実行します。

```bash
source .venv/bin/activate
```

このプロジェクトには現在`.vscode/settings.json`がないため、VS Codeで開くだけでは`.venv`を使う設定になっていません。自動で使う場合は、Python拡張機能でプロジェクト内の`.venv`をインタープリターとして選択し、ワークスペース設定でターミナルの環境自動有効化を有効にします。`.venv`をまだ作成していない場合は、先に上のインストール手順を実行してください。

## マイクの権限

初回起動時にmacOSからマイクへのアクセス許可を求められたら許可してください。許可ダイアログが出ない、または拒否した場合は、**システム設定 → プライバシーとセキュリティ → マイク**を開き、実行に使うアプリ（ターミナル、iTerm、またはIDE）を有効にします。設定後、ターミナルやIDEを再起動してください。

## 起動（Web画面）

仮想環境を有効にした状態で、ローカルサーバーを起動します。

```bash
python app.py
```

ブラウザーで <http://127.0.0.1:8000> を開き、「録音を開始」を押します。初回はWhisperの`base`モデル（約150 MB）が自動でダウンロードされます。以降はMac上に保存されたモデルを使用します。録音を止めるには画面のボタンを押すか、サーバーを実行しているターミナルで **Ctrl+C** を押します。

音声認識が遅い場合は、Webサーバーを停止し、軽い`tiny`モデルで起動できます。

```bash
WHISPER_MODEL=tiny python app.py
```

音声区間の長さは既定で5秒です。Web画面のサーバーで長さを変える場合は、`app.py`の`TranscriptionService()`に`chunk_seconds`を指定します。単独のターミナル版も引き続き利用でき、モデルや区切り時間を引数で指定できます。

```bash
python realtime_transcriber.py --model tiny --chunk-seconds 8
```

利用できるモデルは`tiny`、`base`、`small`、`medium`、`large`です。大きいモデルほどメモリと処理時間が増えますが、認識精度が上がる場合があります。停止時は録音を閉じた後、すでに待機中の音声区間も処理します。

## 保存先とテーブル

`conversation.db` はこのPythonファイルと同じフォルダーに作られます。テーブルは初回起動時に自動作成します。

```sql
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    text TEXT NOT NULL,
    processed INTEGER DEFAULT 0
);
```

確認例（別のターミナルで実行）：

```bash
sqlite3 conversation.db 'SELECT id, timestamp, text, processed FROM transcripts ORDER BY id;'
```

無音と空の文字起こし結果は保存しません。`save_transcript()`がDB保存を、`transcribe_audio()`がWhisper処理を担当します。将来GPT処理を加える際は、SQLiteから`processed = 0`を取得して処理できます。

## Web連携とJSON API

HTMLとPythonの音声処理は、Mac上で動作するFastAPIを通して接続しています。HTMLからJSONで録音開始・停止を送り、Pythonから状態や文字起こし結果をJSONで受け取ります。音声ファイル本体はアップロード用の`multipart/form-data`で送ります。録音、音声認識、SQLite保存はPython側で行います。

マイク入力は、**HTML（JavaScript）→ FastAPIへJSONリクエスト → Pythonで録音・Whisper文字起こし・SQLite保存 → FastAPIからJSONレスポンス → HTMLに表示**です。音声ファイルは、**HTML → FastAPIへファイル送信 → ローカルWhisper → SQLite保存 → 結果をJSONで返す**流れです。画面も同じFastAPIアプリから配信するため、ブラウザーからローカルAPIへ直接アクセスできます。アップロードした音声は一時ファイルで処理した後に削除し、文字起こし結果だけを保存します。

現在のAPIは次のとおりです。

| 用途 | HTTP API | リクエスト / 応答 |
| --- | --- | --- |
| 録音開始 | `POST /api/recording/start` | リクエスト `{}`、応答 `{"status":"loading","after_id":12}` |
| 録音停止 | `POST /api/recording/stop` | リクエスト `{}`、応答 `{"status":"stopped"}` |
| 録音状態 | `GET /api/recording/status` | 応答 `{"status":"recording","error":null,"dropped_chunks":0,"latest_id":13}` |
| 新しい文字起こし取得 | `GET /api/transcripts?after_id=12` | 応答 `{"items":[{"id":13,"timestamp":"...","text":"...","processed":0}]}` |
| 音声ファイル文字起こし | `POST /api/transcripts/upload` | `multipart/form-data`で`file`を送信。応答例 `{"status":"saved","text":"...","saved":true}` |

録音開始ボタンでマイク入力を開始し、画面は約1.2秒ごとに状態と新しい文字起こしを取得します。音声ファイル入力はWAV、MP3、M4A、MP4、AAC、FLAC、OGG、OPUS、WebMに対応し、サイズは25MBまでです。文字起こし結果はマイク入力と同じ`transcripts`テーブルへ保存します。

## トラブルシューティング

- **マイクが開けない**: macOSのマイク権限と、システム設定の「サウンド → 入力」で選ばれている機器を確認します。
- **Whisperが読み込めない**: 仮想環境が有効か、インストールが完了しているかを確認します。`python -m pip install -r requirements.txt`を再実行してください。
- **文字起こしが間に合わない**: `--model tiny`を使うか、より短い会話を試してください。録音は認識中も続きますが、Macの処理速度によっては処理待ちの区間が増えることがあります。
