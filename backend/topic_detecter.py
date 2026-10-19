import argparse
import re
from pathlib import Path

from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

WORD_SIMILARITY_WEIGHT = 0.18
DEFAULT_THRESHOLD = 0.745  # 委託先が持つべき正しいデフォルトのコンフィグ
DEMONSTRATIVE_REFERENCE_PATTERN = re.compile(
    r"(?:あそこ|こちら|そちら|あちら|これ|それ|あれ|ここ|そこ|この|その|あの|"
    r"こんな|そんな|あんな|こういう|そういう|ああいう)(?!ぞれ|ろ)"
)

# 応答・相槌・否定の言葉を文中どこでも（または単体で）検出するパターン
RESPONSE_PATTERN = re.compile(
    r"(?:うん|はい|ええ|そう|そうそう|その通り|たしかに|確かに|なるほど|そうですね|そうですねえ|そうだね|"
    r"違う|いや|ううん|そうじゃない|ちがう|違う違う|いやそうじゃなくて|おー|へえ|なるほどね)"
)


class TopicShiftDetector:

  def __init__(
      self,
      model_name="intfloat/multilingual-e5-small",
      threshold=DEFAULT_THRESHOLD,
      on_topic_completed=None,
  ):
    print(f"モデルをロード中: {model_name}...")
    self.model = SentenceTransformer(model_name)
    self.threshold = threshold  # コンフィグは委託先で保持
    self.current_topic = []
    # 話題が確定した瞬間に呼び出すイベントコールバック
    self.on_topic_completed = on_topic_completed
    self.topic_count = 0

  def _complete_current_topic(self):
    """現在の話題を確定し、コンソール表示後ただちにコールバックへ通知する。"""
    if not self.current_topic:
      return None

    topic = "\\n".join(self.current_topic)
    self.current_topic = []
    self.topic_count += 1

    print(f"\\n=== 話題 {self.topic_count} が終了しました ===")
    print("【話題の内容】")
    print(topic)

    if self.on_topic_completed is not None:
      self.on_topic_completed(topic)

    return topic

  @staticmethod
  def _weighted_similarity(similarities):
    total_weight = sum(window_size for window_size, _ in similarities)
    return (
        sum(
            similarity * window_size for window_size, similarity in similarities
        )
        / total_weight
    )

  def _tokenize_content(self, text):
    return [
        token
        for token in self.model.tokenizer.tokenize(text)
        if token.strip("▁").isalnum()
    ]

  @staticmethod
  def has_demonstrative_reference(text):
    return DEMONSTRATIVE_REFERENCE_PATTERN.search(text) is not None

  @staticmethod
  def has_response_word(text):
    return RESPONSE_PATTERN.search(text) is not None

  def _fit_word_vectorizer(self, messages):
    vectorizer = TfidfVectorizer(
        tokenizer=self._tokenize_content,
        token_pattern=None,
        lowercase=False,
    )
    try:
      vectorizer.fit(messages)
    except ValueError as error:
      if "empty vocabulary" not in str(error):
        raise
      return None
    return vectorizer

  @staticmethod
  def _word_similarity(vectorizer, left, right):
    if vectorizer is None:
      return None
    vectors = vectorizer.transform([left, right])
    return float(cosine_similarity(vectors[0], vectors[1])[0][0])

  @staticmethod
  def _combine_similarities(semantic_similarity, word_similarity):
    if word_similarity is None:
      return semantic_similarity
    return (
        semantic_similarity * (1 - WORD_SIMILARITY_WEIGHT)
        + word_similarity * WORD_SIMILARITY_WEIGHT
    )

  def compare_context(self, context_messages, next_message):
    all_context_messages = [
        message.strip() for message in context_messages if message.strip()
    ]
    recent_messages = all_context_messages[-4:]
    next_message = next_message.strip()
    if not recent_messages or not next_message:
      raise ValueError("比較には文脈と次の発言が必要です")

    windows = [
        (window_size, "\n".join(recent_messages[-window_size:]))
        for window_size in range(1, len(recent_messages) + 1)
    ]
    vectorizer = self._fit_word_vectorizer(
        [*all_context_messages, next_message]
    )
    embeddings = self.model.encode(
        [text for _, text in windows] + [next_message],
        show_progress_bar=False,
    )
    similarities = []
    for (window_size, context), embedding in zip(windows, embeddings[:-1]):
      semantic_similarity = float(
          cosine_similarity([embedding], [embeddings[-1]])[0][0]
      )
      word_similarity = self._word_similarity(
          vectorizer, context, next_message
      )
      similarities.append(
          (
              window_size,
              self._combine_similarities(semantic_similarity, word_similarity),
          )
      )
    return self._weighted_similarity(similarities)

  def add_message(self, message):
    message = message.strip()
    if not message:
      return None

    completed_topic = None
    if self.current_topic:
      similarity = self.compare_context(self.current_topic, message)
      topic_changed = (
          similarity < self.threshold
          and not self.has_demonstrative_reference(message)
          and not self.has_response_word(message)
      )
      if topic_changed:
        completed_topic = "\n".join(self.current_topic)
        self.current_topic = []

    self.current_topic.append(message)
    return completed_topic

  def flush_topic(self):
    if not self.current_topic:
      return None

    topic = "\n".join(self.current_topic)
    self.current_topic = []
    return topic

  def detect_shifts(self, messages):
    if len(messages) < 2:
      return []

    shift_indices = []
    topic_start = 0
    print("\n--- 1〜4行の文脈と次の発言の類似度チェック ---")
    for index in range(1, len(messages)):
      next_message = messages[index]
      context_messages = messages[max(topic_start, index - 4) : index]
      windows = [
          (window_size, "\n".join(context_messages[-window_size:]))
          for window_size in range(1, len(context_messages) + 1)
      ]
      vectorizer = self._fit_word_vectorizer(
          [*messages[topic_start:index], next_message]
      )
      embeddings = self.model.encode(
          [text for _, text in windows] + [next_message],
          show_progress_bar=False,
      )
      next_embedding = embeddings[-1]
      window_scores = []
      for (window_size, context), window_embedding in zip(
          windows, embeddings[:-1]
      ):
        semantic_similarity = float(
            cosine_similarity([window_embedding], [next_embedding])[0][0]
        )
        word_similarity = self._word_similarity(
            vectorizer, context, next_message
        )
        combined_window_similarity = self._combine_similarities(
            semantic_similarity, word_similarity
        )
        window_scores.append(
            (
                window_size,
                semantic_similarity,
                word_similarity,
                combined_window_similarity,
            )
        )
      combined_similarity = self._weighted_similarity(
          [
              (window_size, combined_window_similarity)
              for window_size, _, _, combined_window_similarity in window_scores
          ]
      )
      context = windows[-1][1]
      print(f"[文脈: {context}] と [次の発言: {next_message}]")
      for (
          window_size,
          semantic_similarity,
          word_similarity,
          combined_window_similarity,
      ) in window_scores:
        weight = window_size
        word_score = (
            "未算出" if word_similarity is None else f"{word_similarity:.3f}"
        )
        print(
            f"  -> {window_size}行: 意味 {semantic_similarity:.3f},"
            f" 単語 {word_score}, 合成 {combined_window_similarity:.3f}"
            f" (重み {weight})"
        )
      print(f"  -> 加重総合類似度: {combined_similarity:.3f}")

      has_reference = self.has_demonstrative_reference(next_message)
      has_response = self.has_response_word(next_message)

      if has_reference or has_response:
        print("  -> 指示語、または応答・否定の言葉があるため話題継続として判定")

      if (
          combined_similarity < self.threshold
          and not has_reference
          and not has_response
      ):
        shift_indices.append(index)
        print(
            "  -> 話題が切り替わりました（しきい値"
            f" {self.threshold} 未満）"
        )
        topic_start = index

    return shift_indices


SAMPLE_FILE = Path(__file__).with_name("sample_topic_conversation.txt")


def main():
  parser = argparse.ArgumentParser(
      description="会議サンプルで話題の切り替わりを検出します。"
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
  shift_indices = detector.detect_shifts(messages)

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
