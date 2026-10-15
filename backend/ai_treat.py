import argparse
import json
from pathlib import Path

from ai_service import ANALYSIS_PROMPT_TEMPLATE, get_chatgpt_response
from topic_detecter import TopicShiftDetector

SAMPLE_FILE = Path(__file__).with_name("sample_topic_conversation.txt")
JSON_FILE = Path(__file__).with_name("conversation.json")


class LiveTopicAnalyzer:
  """確定済みの話題をChatGPTへ送り、出力を保存します。"""

  def __init__(self, get_chatgpt_response, prompt_template, save_analysis):
    self.get_chatgpt_response = get_chatgpt_response
    self.prompt_template = prompt_template
    self.save_analysis = save_analysis

  def analyze_topic(self, topic):
    topic = topic.strip()
    if not topic:
      return None

    prompt = self.prompt_template.format(topic=topic)
    analysis = self.get_chatgpt_response(prompt) or ""
    self.save_analysis(topic, analysis)
    return {"topic": topic, "analysis": analysis}


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
  print(f"\n--- [イベント発火] 話題が確定しました ---")
  print(topic)
  print(f"--- ChatGPT分析結果 ---\n{analysis}")


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

  print("\n=== リアルタイム（発話ごと）の話題検出とイベント通知のシミュレーション ===")
  for message in messages:
    print(f"\n[受信メッセージ]: {message}")
    # メッセージを順番に追加（話題が変わるとdetectorから自動でイベントが発火する）
    detector.add_message(message)

  # 最後に残っている最後の話題をフラッシュして確定・送信
  detector.flush_topic()


if __name__ == "__main__":
  main()