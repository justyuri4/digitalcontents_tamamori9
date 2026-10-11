import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity


class TopicShiftDetector:

  def __init__(
      self, model_name="intfloat/multilingual-e5-small"
  ):  # 軽量で日本語の文脈理解に優れたモデル
    print(f"モデルをロード中: {model_name}...")
    self.model = SentenceTransformer(model_name)

  def compare(self, first_message, second_message):
    embeddings = self.model.encode(
        [first_message, second_message], show_progress_bar=False
    )
    return float(cosine_similarity([embeddings[0]], [embeddings[1]])[0][0])

  def compare_context(self, context_messages, next_message):
    context = " ".join(message.strip() for message in context_messages[-3:] if message.strip())
    if not context or not next_message.strip():
      raise ValueError("比較には文脈と次の発言が必要です")
    return self.compare(context, next_message.strip())

  def detect_shifts(self, messages, threshold=0.88):
    """会話のメッセージリストを受け取り、話題が変わったインデックスを検知する

    :param messages: 会話の文字列のリスト
    :param threshold:
    類似度の閾値（0〜1）。これより下がると話題が変わったと判定
    """
    if len(messages) < 2:
      return []

    comparison_pairs = [
        (" ".join(messages[max(0, index - 3):index]), messages[index])
        for index in range(1, len(messages))
    ]
    comparison_texts = [
        text for context, next_message in comparison_pairs
        for text in (context, next_message)
    ]
    embeddings = self.model.encode(comparison_texts, show_progress_bar=False)

    shift_indices = []
    print("\n--- 直近3文と次の発言の類似度チェック ---")

    for pair_index, (context, next_message) in enumerate(comparison_pairs):
      embedding_index = pair_index * 2
      sim = cosine_similarity(
          [embeddings[embedding_index]], [embeddings[embedding_index + 1]]
      )[0][0]

      print(f"[文脈: {context}] と [次の発言: {next_message}]")
      print(f"  -> 類似度: {sim:.3f}")

      # 類似度が閾値を下回ったら「話題の変わり目」とみなす
      if sim < threshold:
        shift_indices.append(pair_index + 1)
        print(
            "  -> ⚠️ 【話題が切り替わりました】（閾値"
            f" {threshold} を下回りました）"
        )

    return shift_indices


# ==========================================
# 実行テスト用コード
# ==========================================
if __name__ == "__main__":
  # サンプルの会話ログ
  # （前半：プログラミング、中盤：夕飯、後半：天気）
  chat_log = [
      "Pythonでベクトル検索のスクリプトを書いています。",
      "ライブラリは何を使うのがおすすめですか？",
      "sentence-transformersが一番手軽でローカルでも軽快に動きます。",
      "なるほど、ありがとう！ところで、今夜の夕飯は何にしようかな。",
      "カレーライスか、それともラーメンがいいですかね。",
      "ラーメンもいいですね。あ、そういえば明日の天気予報って雨でしたっけ？",
      "明日の名古屋は晴れの予報ですよ。",
  ]

  # 検出器の初期化
  detector = TopicShiftDetector()

  # 話題の変わり目を検知（閾値はデータの性質に合わせて 0.6 〜 0.7 辺りで微調整してください）
  shifts = detector.detect_shifts(chat_log, threshold=0.65)

  print("\n=== 完結したトピックごとの分割結果 ===")
  start_idx = 0
  for shift_idx in shifts:
    topic = chat_log[start_idx:shift_idx]
    print(f"\n[まとまったトピック]: {topic}")
    # ★ここに「ChatGPT APIへこのトピックを投げて要約させる処理」を組み込めます
    start_idx = shift_idx

  # 最後のトピック
  print(f"\n[まとまったトピック]: {chat_log[start_idx:]}")