import argparse
import json
from pathlib import Path

# ai_serviceから必要なクラス・関数・テンプレートをすべてインポートする
from backend.ai_service import (
    ANALYSIS_PROMPT_TEMPLATE,
    LiveTopicAnalyzer,
    get_chatgpt_response,
)
from backend.topic_detecter import TopicShiftDetector

SAMPLE_FILE = Path(__file__).with_name("sample_topic_conversation.txt")
JSON_FILE = Path(__file__).with_name("conversation.json")
SENT_PROMPT_FILE = Path(__file__).with_name("sent_prompt.txt")
SAMPLE_OUTPUT_FILE = Path(__file__).with_name("sample_output.txt")


def get_sample_prompt_log_path(input_path=None):
  if input_path is None:
    input_path = JSON_FILE if JSON_FILE.is_file() else SAMPLE_FILE

  if input_path.resolve() == SAMPLE_FILE.resolve():
    return SENT_PROMPT_FILE
  return None


def get_sample_output_log_path(input_path=None):
  if input_path is None:
    input_path = JSON_FILE if JSON_FILE.is_file() else SAMPLE_FILE

  if input_path.resolve() == SAMPLE_FILE.resolve():
    return SAMPLE_OUTPUT_FILE
  return None


def load_messages(input_path=None):
  if input_path is None:
    input_path = JSON_FILE if JSON_FILE.is_file() else SAMPLE_FILE

  if not input_path.is_file():
    return []

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
  print(f"\n--- [イベント発火] 話題が確定しました ---")
  print(topic)
  print(f"--- ChatGPT分析結果 ---\n{analysis}")


def run_analysis_from_messages(messages, threshold=0.745, on_update=None):
  """
  メッセージを解析し、話題が完了して結果が蓄積されるたびに
  on_update(results) コールバックを呼び出してリアルタイムに更新・通知する関数
  """
  results = []
  
  # リアルタイム解析用のアナライザーを準備
  analyzer = LiveTopicAnalyzer(
      get_chatgpt_response=get_chatgpt_response,
      prompt_template=ANALYSIS_PROMPT_TEMPLATE,
      save_analysis=lambda topic, analysis: None
  )

  # 話題が完了するたびに結果を蓄積し、更新コールバックがあれば呼び出す
  def handle_topic_completed(topic):
    analysis_result = analyzer.analyze_topic(topic)
    results.append(analysis_result)
    # 蓄積されるたびに外部へ現在の全リストを通知する
    if on_update is not None:
      on_update(results)

  detector = TopicShiftDetector(
      threshold=threshold,
      on_topic_completed=handle_topic_completed
  )

  for message in messages:
    detector.add_message(message)

  # 最後に残っている話題をフラッシュして確定
  detector.flush_topic()

  return {
      "topics": results,
      "shift_indices": []
  }


def main():
  parser = argparse.ArgumentParser(
      description="話題検出のイベント通知をリアルタイム確認するデバッグスクリプトです。"
  )
  parser.add_argument(
      "--input",
      type=Path,
      default=None,
      help="会話テキストまたはJSONファイル（既定値: conversation.json、なければサンプル）",
  )
  args = parser.parse_args()

  messages = load_messages(args.input)
  if not messages:
    parser.error("会話テキストが空です")

  print("\n=== ChatGPTによる話題分析結果の逐次蓄積シミュレーション ===")
  
  analyzer = LiveTopicAnalyzer(
      get_chatgpt_response=get_chatgpt_response,
      prompt_template=ANALYSIS_PROMPT_TEMPLATE,
      save_analysis=save_analysis_result,
      prompt_log_path=get_sample_prompt_log_path(args.input),
        analysis_log_path=get_sample_output_log_path(args.input),
  )

  results = []
  def console_on_update(current_results):
    print(f"\n[リアルタイム更新] 現在蓄積された話題の数: {len(current_results)}")

  detector = TopicShiftDetector(
      on_topic_completed=lambda topic: [
          results.append(analyzer.analyze_topic(topic)),
          console_on_update(results)
      ]
  )

  for message in messages:
    detector.add_message(message)

  detector.flush_topic()


if __name__ == "__main__":
  main()
