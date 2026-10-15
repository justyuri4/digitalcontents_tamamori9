import argparse
import json
from pathlib import Path

from ai_service import ANALYSIS_PROMPT_TEMPLATE, get_chatgpt_response, check_topics_similarity
from topic_detecter import TopicShiftDetector

SAMPLE_FILE = Path(__file__).with_name("sample_topic_conversation.txt")
JSON_FILE = Path(__file__).with_name("conversation.json")


class LiveTopicAnalyzer:
  """確定済みの話題をChatGPTへ送り、出力を保存します。類似している場合は前の話題と統合します。"""

  def __init__(self, get_chatgpt_response, prompt_template, save_analysis):
    self.get_chatgpt_response = get_chatgpt_response
    self.prompt_template = prompt_template
    self.save_analysis = save_analysis
    self.results = []

  def analyze_topic(self, topic):
    topic = topic.strip()
    if not topic:
      return None

    prompt = self.prompt_template.format(topic=topic)
    analysis = self.get_chatgpt_response(prompt) or ""

    if self.results:
      last_result = self.results[-1]
      if check_topics_similarity(last_result.get("topic", ""), topic):
        last_result["topic"] += " / " + topic
        last_result["analysis"] += "\n" + analysis
        self.save_analysis(last_result["topic"], last_result["analysis"])
        return last_result

    new_item = {"topic": topic, "analysis": analysis}
    self.results.append(new_item)
    self.save_analysis(topic, analysis)
    return new_item


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


def run_ai_analysis(input_path=None):
  """
  AI分析を実行し、結果のリスト（results）を返します。
  send_json から呼び出されることを想定しています。
  """
  messages = load_messages(input_path)
  if not messages:
    raise ValueError("会話テキストが空です")

  analyzed_results = []

  analyzer = LiveTopicAnalyzer(
      get_chatgpt_response=get_chatgpt_response,
      prompt_template=ANALYSIS_PROMPT_TEMPLATE,
      save_analysis=lambda topic, analysis: None,
  )

  detector = TopicShiftDetector(
      on_topic_completed=lambda topic: analyzed_results.append(analyzer.analyze_topic(topic))
  )

  for message in messages:
    detector.add_message(message)
  
  final_topic = detector.flush_topic()
  if final_topic:
    res = analyzer.analyze_topic(final_topic)
    if res and res not in analyzed_results:
      analyzed_results.append(res)

  return analyzed_results


def main():
  parser = argparse.ArgumentParser(
      description="話題検出のイベント通知をリアルタイム確認するデバッグスクリプトです。"
  )
  parser.add_argument(
      "--input",
      type=Path,
      default=None,
      help="会話テキストまたはJSONファイル",
  )
  args = parser.parse_args()

  results = run_ai_analysis(args.input)
  print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
  main()