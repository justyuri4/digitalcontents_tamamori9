"""ローカルWhisperXによる音声文字起こしとSQLite保存。"""

import argparse
import queue
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlretrieve

import numpy as np
import sounddevice as sd
import sherpa_onnx
import whisperx


DATABASE_PATH = Path(__file__).with_name("conversation.db")
MODEL_DIRECTORY = Path(__file__).with_name("models") / "sherpa-onnx"
SAMPLE_RATE = 16_000
CHUNK_SECONDS = 10
LIVE_DIARIZATION_INTERVAL_SECONDS = 10
SILENCE_THRESHOLD = 0.01
MAX_QUEUED_CHUNKS = 20
SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)
SEGMENTATION_MODEL = MODEL_DIRECTORY / "sherpa-onnx-pyannote-segmentation-3-0" / "model.int8.onnx"
EMBEDDING_MODEL = MODEL_DIRECTORY / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
_MODEL_CACHE = {}
_MODEL_LOCK = threading.Lock()
_TRANSCRIPTION_LOCK = threading.Lock()
_DIARIZATION_LOCK = threading.Lock()
_DIARIZATION_MODELS = {}
_ALIGNMENT_MODEL = None
_ALIGNMENT_MODEL_LOCK = threading.Lock()
_ALIGNMENT_LOAD_FAILED = False


def load_whisper_model(model_name: str):
    """WhisperXの日本語モデルをMacのCPU上で一度だけ読み込みます。"""
    with _MODEL_LOCK:
        if model_name not in _MODEL_CACHE:
            _MODEL_CACHE[model_name] = whisperx.load_model(
                model_name,
                device="cpu",
                compute_type="int8",
                language="ja",
            )
        return _MODEL_CACHE[model_name]


def _download_diarization_models() -> None:
    """公式配布先からSherpa-ONNXのモデルを初回だけ取得します。"""
    MODEL_DIRECTORY.mkdir(parents=True, exist_ok=True)
    if not SEGMENTATION_MODEL.exists():
        archive_path = MODEL_DIRECTORY / "segmentation-model.tar.bz2"
        try:
            urlretrieve(SEGMENTATION_URL, archive_path)
            with tarfile.open(archive_path, "r:bz2") as archive:
                archive.extractall(MODEL_DIRECTORY, filter="data")
        finally:
            archive_path.unlink(missing_ok=True)

    if not EMBEDDING_MODEL.exists():
        temporary_model = EMBEDDING_MODEL.with_suffix(".onnx.download")
        try:
            urlretrieve(EMBEDDING_URL, temporary_model)
            temporary_model.replace(EMBEDDING_MODEL)
        finally:
            temporary_model.unlink(missing_ok=True)


def load_diarization_model(num_speakers: int | None = None):
    """Sherpa-ONNXの話者分離モデルを一度だけ読み込みます。"""
    with _DIARIZATION_LOCK:
        if num_speakers not in _DIARIZATION_MODELS:
            _download_diarization_models()
            config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                        model=str(SEGMENTATION_MODEL),
                        window_shift_ratio=0.1,
                    )
                ),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(EMBEDDING_MODEL)
                ),
                clustering=sherpa_onnx.FastClusteringConfig(
                    num_clusters=num_speakers if num_speakers is not None else -1,
                    threshold=0.5,
                ),
                min_duration_on=0.3,
                min_duration_off=0.5,
            )
            if not config.validate():
                raise RuntimeError("Sherpa-ONNXの話者分離モデル設定を確認してください。")
            _DIARIZATION_MODELS[num_speakers] = sherpa_onnx.OfflineSpeakerDiarization(config)
        return _DIARIZATION_MODELS[num_speakers]


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


def save_transcript(text: str, database_path: Path = DATABASE_PATH) -> int | None:
    """空でない文字起こし結果をSQLiteへ追加します。"""
    text = text.strip()
    if not text:
        return None

    with sqlite3.connect(database_path) as connection:
        cursor = connection.execute("INSERT INTO transcripts (text) VALUES (?)", (text,))
        return int(cursor.lastrowid)


def update_transcript(transcript_id: int, text: str, database_path: Path = DATABASE_PATH) -> None:
    """話者分離後のテキストで既存行を更新します（テーブル構成は変えません）。"""
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE transcripts SET text = ? WHERE id = ?",
            (text.strip(), transcript_id),
        )


def transcribe_audio_segments(model, audio: np.ndarray) -> list[dict]:
    """WhisperXで日本語文字起こしと区間の時刻を取得します。"""
    with _TRANSCRIPTION_LOCK:
        result = model.transcribe(audio, batch_size=4, language="ja")
    return [
        {
            "start": float(segment.get("start", 0.0)),
            "end": float(segment.get("end", 0.0)),
            "text": segment.get("text", "").strip(),
        }
        for segment in result.get("segments", [])
        if segment.get("text", "").strip()
    ]


def diarize_audio(audio: np.ndarray, num_speakers: int | None = None) -> list[dict]:
    """音声全体を一度に話者分離し、時間区間と話者番号を返します。"""
    diarizer = load_diarization_model(num_speakers)
    diarization_audio = audio.astype(np.float32, copy=False)
    minimum_length = SAMPLE_RATE * CHUNK_SECONDS
    if len(diarization_audio) < minimum_length:
        diarization_audio = np.pad(diarization_audio, (0, minimum_length - len(diarization_audio)))
    with _DIARIZATION_LOCK:
        diarization_result = diarizer.process(diarization_audio).sort_by_start_time()

    return [
        {
            "start": float(segment.start),
            "end": float(segment.end),
            "speaker_id": int(segment.speaker),
        }
        for segment in diarization_result
    ]


def segment_has_multiple_speakers(
    transcript_segments: list[dict],
    speaker_regions: list[dict],
    time_offset: float = 0.0,
) -> bool:
    """Whisperの一区間に複数の話者区間が重なっているか調べます。"""
    for transcript_segment in transcript_segments:
        if transcript_segment.get("chars"):
            continue
        start = time_offset + float(transcript_segment["start"])
        end = time_offset + float(transcript_segment["end"])
        overlapping_speakers = {
            region["speaker_id"]
            for region in speaker_regions
            if min(end, region["end"]) > max(start, region["start"])
        }
        if len(overlapping_speakers) > 1:
            return True
    return False


def align_transcription_segments(
    transcript_segments: list[dict], audio: np.ndarray
) -> list[dict]:
    """日本語の文字ごとの時刻を取得し、話者交代位置を細かく分けられるようにします。"""
    global _ALIGNMENT_MODEL, _ALIGNMENT_LOAD_FAILED
    if not transcript_segments or _ALIGNMENT_LOAD_FAILED:
        return transcript_segments

    try:
        with _ALIGNMENT_MODEL_LOCK:
            if _ALIGNMENT_MODEL is None:
                # 日本語アラインメントモデルは初回だけ取得し、以降はメモリ上で再利用します。
                _ALIGNMENT_MODEL = whisperx.load_align_model(
                    language_code="ja", device="cpu"
                )
            align_model, metadata = _ALIGNMENT_MODEL

        result = whisperx.align(
            transcript_segments,
            align_model,
            metadata,
            audio,
            "cpu",
            return_char_alignments=True,
        )
        return result.get("segments", transcript_segments)
    except Exception as error:
        _ALIGNMENT_LOAD_FAILED = True
        print(
            f"日本語の文字時刻合わせを使えません。話者ラベルは従来の区間単位で付けます: {error}",
            file=sys.stderr,
            flush=True,
        )
        return transcript_segments


def add_speakers_to_segments(
    transcript_segments: list[dict],
    speaker_regions: list[dict],
    time_offset: float = 0.0,
    speaker_names: dict[int, str] | None = None,
) -> list[dict]:
    """文字起こし区間に、最も重なる話者区間のラベルを付けます。"""
    if speaker_names is None:
        speaker_names = {}

    labeled_segments = []

    def speaker_id_for_interval(start: float, end: float):
        best_id = None
        best_overlap = 0.0
        for region in speaker_regions:
            overlap = max(
                0.0,
                min(time_offset + end, region["end"])
                - max(time_offset + start, region["start"]),
            )
            if overlap > best_overlap:
                best_id = region["speaker_id"]
                best_overlap = overlap

        # 文字の時刻が話者境界に重なる場合は、その文字の中央時刻で判定します。
        if best_id is None:
            midpoint = time_offset + (start + end) / 2
            for region in speaker_regions:
                if region["start"] <= midpoint <= region["end"]:
                    return region["speaker_id"]
            if speaker_regions:
                nearest = min(
                    speaker_regions,
                    key=lambda region: min(
                        abs(midpoint - region["start"]),
                        abs(midpoint - region["end"]),
                    ),
                )
                # WhisperとSherpa-ONNXの区間境界にはずれが出るため、
                # 文字起こし済みの発言には最も近い検出話者を割り当てます。
                return nearest["speaker_id"]
        return best_id

    def speaker_label(speaker_id):
        if speaker_id is None:
            return "話者不明"
        return speaker_names.setdefault(
            speaker_id, f"話者{len(speaker_names) + 1}"
        )

    for transcript_segment in transcript_segments:
        start = float(transcript_segment["start"])
        end = float(transcript_segment["end"])
        aligned_chars = transcript_segment.get("chars") or []
        if aligned_chars:
            # ひとつの文字起こし区間に話者交代があっても、文字の時刻で分けます。
            groups = []
            text_length = max(1, len(aligned_chars))
            for index, char_info in enumerate(aligned_chars):
                char = char_info.get("char", "")
                if not char:
                    continue
                if char.isspace():
                    if groups:
                        groups[-1]["text"] += char
                    continue
                char_start = char_info.get("start")
                char_end = char_info.get("end")
                if char_start is None or char_end is None:
                    char_start = start + (end - start) * index / text_length
                    char_end = start + (end - start) * (index + 1) / text_length
                speaker_id = speaker_id_for_interval(
                    float(char_start), float(char_end)
                )
                label = speaker_label(speaker_id)
                if groups and groups[-1]["speaker"] == label:
                    groups[-1]["text"] += char
                    groups[-1]["end"] = float(char_end)
                else:
                    groups.append(
                        {
                            "start": float(char_start),
                            "end": float(char_end),
                            "text": char,
                            "speaker": label,
                        }
                    )
            if groups:
                labeled_segments.extend(groups)
                continue

        speaker_id = speaker_id_for_interval(start, end)
        labeled_segments.append(
            {
                "start": start,
                "end": end,
                "text": transcript_segment["text"],
                "speaker": speaker_label(speaker_id),
            }
        )

    # 文字単位の時刻ずれで生じた短いラベルの揺れを、前後の発言へ統合します。
    def visible_character_count(text: str) -> int:
        punctuation = "。、．，！？!?「」『』（）()…・"
        return sum(
            not char.isspace() and char not in punctuation for char in text
        )

    for index, segment in enumerate(labeled_segments):
        if not segment["text"].strip():
            continue
        duration = max(0.0, segment["end"] - segment["start"])
        character_count = visible_character_count(segment["text"])
        is_short_fragment = duration < 0.35 and character_count <= 2
        is_tiny_fragment = duration < 0.2 and character_count <= 1
        if segment["speaker"] != "話者不明" and not is_short_fragment:
            continue

        previous = labeled_segments[index - 1] if index > 0 else None
        following = (
            labeled_segments[index + 1]
            if index + 1 < len(labeled_segments)
            else None
        )
        neighbors = [
            item
            for item in (previous, following)
            if item is not None and item["speaker"] != "話者不明"
        ]
        if segment["speaker"] != "話者不明":
            # 短い相づちは、同じ話者の発言に挟まれた場合だけ揺れとみなします。
            if not (
                is_short_fragment
                and len(neighbors) == 2
                and neighbors[0]["speaker"] == neighbors[1]["speaker"]
            ) and not (is_tiny_fragment and len(neighbors) == 1):
                continue
        if not neighbors:
            continue
        if len(neighbors) == 2 and neighbors[0]["speaker"] == neighbors[1]["speaker"]:
            segment["speaker"] = neighbors[0]["speaker"]
        elif len(neighbors) == 1:
            segment["speaker"] = neighbors[0]["speaker"]
        else:
            segment["speaker"] = max(
                neighbors, key=lambda item: item["end"] - item["start"]
            )["speaker"]

    merged_segments = []
    for segment in labeled_segments:
        if not segment["text"].strip():
            continue
        if (
            merged_segments
            and merged_segments[-1]["speaker"] == segment["speaker"]
        ):
            merged_segments[-1]["text"] += segment["text"]
            merged_segments[-1]["end"] = segment["end"]
        else:
            merged_segments.append(segment.copy())
    return merged_segments


def transcribe_audio_file(
    model, audio_path: Path, num_speakers: int | None = None
) -> str:
    """音声ファイルをWhisperXで文字起こしし、Sherpa-ONNXで話者を付けます。"""
    audio = whisperx.load_audio(str(audio_path))
    transcript_segments = transcribe_audio_segments(model, audio)
    speaker_regions = diarize_audio(audio, num_speakers)
    print(f"音声ファイルの話者区間を{len(speaker_regions)}件検出しました。", flush=True)
    if segment_has_multiple_speakers(transcript_segments, speaker_regions):
        transcript_segments = align_transcription_segments(transcript_segments, audio)
    labeled_segments = add_speakers_to_segments(transcript_segments, speaker_regions)
    return "\n".join(
        f"{segment['speaker']}：{segment['text']}" for segment in labeled_segments
    ).strip()


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
        self._speaker_count = 2
        self._speaker_revision = 0

    def start(self, speaker_count: int = 2) -> str:
        """録音スレッドを開始します。すでに動作中なら現在の状態を返します。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._status
            self._stop_event.clear()
            self._error = None
            self._dropped_chunks = 0
            self._speaker_count = speaker_count
            self._speaker_revision = 0
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
                "speaker_revision": self._speaker_revision,
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
        session_audio_dir = Path(tempfile.mkdtemp(prefix="tamamori-audio-"))
        audio_files: list[Path] = []
        transcript_records: list[dict] = []
        audio_offset_samples = 0
        last_live_diarization_samples = 0

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
            """区間音声を一時保存し、文字起こしをSQLiteへ仮保存します。"""
            nonlocal audio_offset_samples
            audio = resample_audio(audio, input_sample_rate)
            audio_path = session_audio_dir / f"chunk-{len(audio_files):06d}.npy"
            np.save(audio_path, audio)
            audio_files.append(audio_path)
            chunk_offset = audio_offset_samples / SAMPLE_RATE
            audio_offset_samples += len(audio)

            if float(np.sqrt(np.mean(audio * audio))) < SILENCE_THRESHOLD:
                return

            transcript_segments = transcribe_audio_segments(model, audio)
            transcript_text = "\n".join(
                segment["text"] for segment in transcript_segments
            ).strip()
            if transcript_text:
                row_id = save_transcript(transcript_text, self.database_path)
                if row_id is not None:
                    transcript_records.append(
                        {
                            "id": row_id,
                            "offset": chunk_offset,
                            "segments": transcript_segments,
                            "audio_path": audio_path,
                        }
                    )
                print(
                    f"[{datetime.now().astimezone().strftime('%H:%M:%S')}] {transcript_text}",
                    flush=True,
                )
            else:
                print("（文字起こし結果なし。保存しません）", flush=True)

        def update_speaker_labels(force: bool = False) -> None:
            """録音済み音声全体を再解析し、SQLiteの話者ラベルを更新します。"""
            nonlocal last_live_diarization_samples
            if not transcript_records or not audio_files:
                return
            interval_samples = SAMPLE_RATE * LIVE_DIARIZATION_INTERVAL_SECONDS
            if (
                not force
                and audio_offset_samples - last_live_diarization_samples < interval_samples
            ):
                return

            complete_audio = np.concatenate(
                [np.load(path).astype(np.float32, copy=False) for path in audio_files]
            )
            speaker_regions = diarize_audio(complete_audio, self._speaker_count)
            print(f"話者区間を{len(speaker_regions)}件検出しました。", flush=True)
            speaker_names: dict[int, str] = {}
            for record in transcript_records:
                transcript_segments = record["segments"]
                # 録音中は追加モデルによる重い文字時刻合わせを避け、停止後だけ実行します。
                if force and segment_has_multiple_speakers(
                    transcript_segments,
                    speaker_regions,
                    time_offset=record["offset"],
                ):
                    chunk_audio = np.load(record["audio_path"]).astype(
                        np.float32, copy=False
                    )
                    transcript_segments = align_transcription_segments(
                        transcript_segments, chunk_audio
                    )
                    if any(segment.get("chars") for segment in transcript_segments):
                        record["segments"] = transcript_segments
                labeled_segments = add_speakers_to_segments(
                    transcript_segments,
                    speaker_regions,
                    time_offset=record["offset"],
                    speaker_names=speaker_names,
                )
                labeled_text = "\n".join(
                    f"{segment['speaker']}：{segment['text']}"
                    for segment in labeled_segments
                )
                update_transcript(record["id"], labeled_text, self.database_path)

            last_live_diarization_samples = audio_offset_samples
            with self._lock:
                self._speaker_revision += 1
            print("話者ラベルを更新しました。", flush=True)

        try:
            print(f"WhisperXモデル '{self.model_name}' を読み込みます…", flush=True)
            model = load_whisper_model(self.model_name)
            if self._stop_event.is_set():
                self._set_status("stopped")
                return

            print("Sherpa-ONNXの話者分離モデルを読み込みます…", flush=True)
            load_diarization_model(self._speaker_count)
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
                        update_speaker_labels()
                    except Exception as error:
                        print(f"音声区間の処理に失敗しました: {error}", file=sys.stderr, flush=True)

            # キュー内の古い区間を先に処理し、最後に未完了の短い区間を処理します。
            while True:
                try:
                    audio = audio_chunks.get_nowait()
                except queue.Empty:
                    break
                try:
                    process_audio(audio, model)
                    update_speaker_labels()
                except Exception as error:
                    print(f"終了前の音声区間の処理に失敗しました: {error}", file=sys.stderr, flush=True)

            if pending_count:
                final_audio = np.concatenate(pending_frames).astype(np.float32, copy=False)
                try:
                    process_audio(final_audio, model)
                except Exception as error:
                    print(f"最後の音声区間の処理に失敗しました: {error}", file=sys.stderr, flush=True)

            if transcript_records and audio_files:
                print("録音の最終話者分離を行い、SQLiteを更新します…", flush=True)
                update_speaker_labels(force=True)

            self._set_status("stopped")
        except Exception as error:
            message = str(error)
            self._set_status("error", message)
            print(f"録音または音声処理に失敗しました: {message}", file=sys.stderr, flush=True)
        finally:
            shutil.rmtree(session_audio_dir, ignore_errors=True)


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
