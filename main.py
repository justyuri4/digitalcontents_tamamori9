import argparse
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.ai_service import ANALYSIS_PROMPT_TEMPLATE, get_chatgpt_response
from backend.realtime_send import LiveTopicAnalyzer
from backend.topic_detecter import TopicShiftDetector

app = FastAPI()

# indexディレクトリのパスを設定（./index/index.html を参照）
INDEX_DIR = Path(__file__).resolve().parent / "index"
SAMPLE_FILE = Path(__file__).with_name("sample_topic_conversation.txt")
JSON_FILE = Path(__file__).with_name("conversation.json")

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# フロントエンドの静的ファイルを公開
if (INDEX_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=INDEX_DIR / "css"), name="css")
if (INDEX_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=INDEX_DIR / "js"), name="js")


@app.get("/", response_class=HTMLResponse)
def read_root():
    index_file = INDEX_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="index.htmlが見つかりません")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"), status_code=200)


def load_messages(input_path=None):
    if input_path is None:
        input_path = JSON_FILE if JSON_FILE.is_file() else SAMPLE_FILE

    content = input_path.read_text(encoding="utf-8")
    if input_path.suffix.lower() == ".json":
        data = json.loads(content)
        if isinstance(data, dict):
            data = data.get("transcript", data.get("messages"))
        if isinstance(data, str):
            content = data
        elif isinstance(data, list) and all(isinstance(item, str) for item in data):
            content = "\n".join(data)
        else:
            raise ValueError(
                "JSONはtranscript文字列、messages文字列配列、または文字列配列にしてください"
            )

    return [line.strip() for line in content.splitlines() if line.strip()]


def save_analysis_result(topic, analysis):
    """話題ごとの分析結果を保存・出力するコールバック関数"""
    pass


def run_simulation(input_path=None):
    messages = load_messages(input_path)
    if not messages:
        return

    # リアルタイム解析用のアナライザーを準備
    analyzer = LiveTopicAnalyzer(
        get_chatgpt_response=get_chatgpt_response,
        prompt_template=ANALYSIS_PROMPT_TEMPLATE,
        save_analysis=save_analysis_result,
    )

    # Detectorにイベント通知（コールバック）を設定して初期化
    detector = TopicShiftDetector(
        on_topic_completed=lambda topic: analyzer.analyze_topic(topic)
    )

    for message in messages:
        detector.add_message(message)

    detector.flush_topic()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="話題検出サーバーです。"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="会話テキストまたはJSONファイル（既定値: conversation.json、なければサンプル）",
    )
    parser.add_argument(
        "--run-script",
        action="store_true",
        help="サーバーを起動せず、スクリプト（シミュレーション）のみを実行します",
    )
    args = parser.parse_args()

    if args.run_script or args.input:
        run_simulation(args.input)
    else:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8080)