import argparse
from pathlib import Path

from topic_ditecter import TopicShiftDetector


SAMPLE_FILE = Path(__file__).with_name("sample_topic_conversation.txt")


def main():
  parser = argparse.ArgumentParser(
      description="会議サンプルで話題の切り替わりを検出します。"
  )
  parser.add_argument(
      "--threshold",
      type=float,
      default=0.88,
      help="話題切り替わりと判定する類似度のしきい値（既定値: 0.88）",
  )
  args = parser.parse_args()

  messages = [
      line.strip()
      for line in SAMPLE_FILE.read_text(encoding="utf-8").splitlines()
      if line.strip()
  ]
  if not messages:
    parser.error(f"テスト用会話が空です: {SAMPLE_FILE}")

  detector = TopicShiftDetector()
  shift_indices = detector.detect_shifts(
      messages, threshold=args.threshold
  )

  print("\n=== 検出された話題 ===")
  boundaries = [0, *shift_indices, len(messages)]
  for topic_number, (start, end) in enumerate(
      zip(boundaries, boundaries[1:]), start=1
  ):
    print(f"\n[話題 {topic_number}]")
    for message in messages[start:end]:
      print(f"- {message}")

  print(f"\n切り替わり位置（次の発言の番号）: {shift_indices}")


if __name__ == "__main__":
  main()
