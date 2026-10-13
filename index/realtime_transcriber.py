"""ローカルWhisperによる音声文字起こしとSQLite保存。"""

import argparse
import queue
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import whisper


DATABASE_PATH = Path(__file__).with_name("conversation.db")
SAMPLE_RATE = 16_000
CHUNK_SECONDS = 5
SILENCE_THRESHOLD = 0.01
MAX_QUEUED_CHUNKS = 20
_MODEL_CACHE = {}
_MODEL_LOCK = threading.Lock()
_TRANSCRIPTION_LOCK = threading.Lock()


def load_whisper_model(model_name: str):
    """同じモデルをマイク録音とファイル入力で共有します。"""
    with _MODEL_LOCK:
        if model_name not in _MODEL_CACHE:
            _MODEL_CACHE[model_name] = whisper.load_model(model_name)
        return _MODEL_CACHE[model_name]


def initialize_database(database_path: Path = DATABASE_PATH) -> None:
    """保存先と文字起こしテーブルを準備します。"""
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                text TEXT NOT NULL,
                processed INTEGER DEFAULT 0
            )"""
        )


def save_transcript(text: str, database_path: Path = DATABASE_PATH) -> None:
    """空でない文字起こし結果をSQLiteへ追加します。"""
    text = text.strip()
    if not text:
        return

    with sqlite3.connect(database_path) as connection:
        connection.execute("INSERT INTO transcripts (text) VALUES (?)", (text,))


def transcribe_audio(model, audio: np.ndarray) -> str:
    """音声配列を日本語指定でローカルWhisperに渡します。"""
    with _TRANSCRIPTION_LOCK:
        result = model.transcribe(
            audio,
            language="ja",
            task="transcribe",
            fp16=False,
            verbose=False,
            condition_on_previous_text=False,
        )
    return result["text"].strip()


def transcribe_audio_file(model, audio_path: Path) -> str:
    """音声ファイルをローカルWhisperで日本語文字起こしします。"""
    with _TRANSCRIPTION_LOCK:
        result = model.transcribe(
            str(audio_path),
            language="ja",
            task="transcribe",
            fp16=False,
            verbose=False,
            condition_on_previous_text=False,
        )
    return result["text"].strip()


def resample_audio(audio: np.ndarray, source_rate: int) -> np.ndarray:
    """Whisperの入力レート（16 kHz）へ音声を変換します。"""
    if source_rate == SAMPLE_RATE:
        return audio.astype(np.float32, copy=False)

    target_length = round(len(audio) * SAMPLE_RATE / source_rate)
    source_positions = np.arange(target_length) * source_rate / SAMPLE_RATE
    return np.interp(source_positions, np.arange(len(audio)), audio).astype(np.float32)


class TranscriptionService:
    """録音を続けながら音声区間を認識し、SQLiteへ保存します。"""

    def __init__(
        self,
        model_name: str = "base",
        chunk_seconds: int = CHUNK_SECONDS,
        database_path: Path = DATABASE_PATH,
    ) -> None:
        self.model_name = model_name
        self.chunk_seconds = chunk_seconds
        self.database_path = database_path
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status = "stopped"
        self._error: str | None = None
        self._dropped_chunks = 0

    def start(self) -> str:
        """録音スレッドを開始します。すでに動作中なら現在の状態を返します。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._status
            self._stop_event.clear()
            self._error = None
            self._dropped_chunks = 0
            self._status = "loading"
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return self._status

    def stop(self) -> str:
        """録音を止め、実行中の音声区間の処理が終わるのを待ちます。"""
        with self._lock:
            thread = self._thread
            if not thread or not thread.is_alive():
                return self._status
            self._status = "stopping"
            self._stop_event.set()

        # 実行中の認識が終わってから録音ストリームを閉じます。
        thread.join()
        with self._lock:
            return self._status

    def get_status(self) -> dict:
        with self._lock:
            return {
                "status": self._status,
                "error": self._error,
                "dropped_chunks": self._dropped_chunks,
            }

    def _set_status(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self._status = status
            if error is not None:
                self._error = error

    def _run(self) -> None:
        audio_chunks: queue.Queue[np.ndarray] = queue.Queue(maxsize=MAX_QUEUED_CHUNKS)
        pending_frames: list[np.ndarray] = []
        pending_count = 0
        input_sample_rate = SAMPLE_RATE

        def receive_audio(indata, frames, time_info, status):
            """録音コールバック。指定秒数分の音声をキューへ渡します。"""
            nonlocal pending_count, pending_frames
            mono_audio = indata[:, 0].copy()
            pending_frames.append(mono_audio)
            pending_count += len(mono_audio)

            while pending_count >= chunk_frames:
                combined = np.concatenate(pending_frames)
                chunk = combined[:chunk_frames].astype(np.float32, copy=False)
                remaining = combined[chunk_frames:]
                pending_frames = [remaining] if len(remaining) else []
                pending_count = len(remaining)
                try:
                    audio_chunks.put_nowait(chunk)
                except queue.Full:
                    # 音声入力を止めないよう、上限超過時は新しい区間を破棄します。
                    with self._lock:
                        self._dropped_chunks += 1

        def process_audio(audio: np.ndarray, model) -> None:
            """無音でない区間を認識し、結果があれば保存します。"""
            if float(np.sqrt(np.mean(audio * audio))) < SILENCE_THRESHOLD:
                return

            audio = resample_audio(audio, input_sample_rate)
            text = transcribe_audio(model, audio)
            if text:
                save_transcript(text, self.database_path)
                print(f"[{datetime.now().astimezone().strftime('%H:%M:%S')}] {text}", flush=True)
            else:
                print("（文字起こし結果なし。保存しません）", flush=True)

        try:
            print(f"Whisperモデル '{self.model_name}' を読み込みます…", flush=True)
            model = load_whisper_model(self.model_name)
            if self._stop_event.is_set():
                self._set_status("stopped")
                return

            input_device = sd.query_devices(kind="input")
            input_sample_rate = int(input_device["default_samplerate"])
            chunk_frames = input_sample_rate * self.chunk_seconds

            with sd.InputStream(
                samplerate=input_sample_rate,
                channels=1,
                dtype="float32",
                blocksize=0,
                callback=receive_audio,
            ):
                self._set_status("recording")
                print(
                    f"録音中です（入力 {input_sample_rate} Hz、{self.chunk_seconds}秒ごと）。停止はCtrl+Cです。",
                    flush=True,
                )
                reported_drops = 0
                while not self._stop_event.is_set():
                    with self._lock:
                        current_drops = self._dropped_chunks
                    if current_drops > reported_drops:
                        print(
                            f"処理待ちが上限に達したため、音声区間を{current_drops - reported_drops}件破棄しました。",
                            file=sys.stderr,
                            flush=True,
                        )
                        reported_drops = current_drops
                    try:
                        audio = audio_chunks.get(timeout=0.25)
                    except queue.Empty:
                        continue

                    try:
                        process_audio(audio, model)
                    except Exception as error:
                        print(f"音声区間の処理に失敗しました: {error}", file=sys.stderr, flush=True)

            # 録音を閉じてから、すでにキューに入っている区間を処理します。
            if pending_count:
                final_audio = np.concatenate(pending_frames).astype(np.float32, copy=False)
                try:
                    process_audio(final_audio, model)
                except Exception as error:
                    print(f"最後の音声区間の処理に失敗しました: {error}", file=sys.stderr, flush=True)

            while True:
                try:
                    audio = audio_chunks.get_nowait()
                except queue.Empty:
                    break
                try:
                    process_audio(audio, model)
                except Exception as error:
                    print(f"終了前の音声区間の処理に失敗しました: {error}", file=sys.stderr, flush=True)

            self._set_status("stopped")
        except Exception as error:
            message = str(error)
            self._set_status("error", message)
            print(f"録音またはWhisper処理に失敗しました: {message}", file=sys.stderr, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="マイク音声をローカルWhisperで文字起こしします")
    parser.add_argument("--model", default="base", choices=("tiny", "base", "small", "medium", "large"))
    parser.add_argument("--chunk-seconds", type=int, default=CHUNK_SECONDS)
    args = parser.parse_args()
    if args.chunk_seconds < 1:
        parser.error("--chunk-seconds は1以上を指定してください")

    initialize_database()
    service = TranscriptionService(args.model, args.chunk_seconds)
    service.start()
    print("終了するには Ctrl+C を押してください。", flush=True)
    try:
        while service.get_status()["status"] not in ("stopped", "error"):
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\n録音を停止しています…", flush=True)
        service.stop()

    if service.get_status()["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
