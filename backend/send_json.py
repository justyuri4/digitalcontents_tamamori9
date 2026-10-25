# analyzer_service.py
from ai_treat import (
    LiveTopicAnalyzer,
    load_messages,
    get_sample_prompt_log_path,
    get_sample_output_log_path,
    get_chatgpt_response,
    ANALYSIS_PROMPT_TEMPLATE,
)
from topic_detecter import TopicShiftDetector

def run_analysis_to_json():
    """
    ai_treat の解析を実行し、結果をまとめた辞書（JSON用データ）を返します。
    """
    try:
        messages = load_messages(None)
        if not messages:
            return {"status": "error", "message": "会話テキストが空です"}

        results = []
        # 話題が完了した際のコールバックで結果をリストに蓄積
        analyzer = LiveTopicAnalyzer(
            get_chatgpt_response=get_chatgpt_response,
            prompt_template=ANALYSIS_PROMPT_TEMPLATE,
            save_analysis=lambda topic, analysis: None, # 標準出力への保存はスキップ
            prompt_log_path=get_sample_prompt_log_path(None),
            analysis_log_path=get_sample_output_log_path(None),
        )

        detector = TopicShiftDetector(
            on_topic_completed=lambda topic: results.append(analyzer.analyze_topic(topic))
        )

        for message in messages:
            detector.add_message(message)
        detector.flush_topic()

        return {"status": "success", "results": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}