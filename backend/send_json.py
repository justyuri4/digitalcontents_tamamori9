import json
import re
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.append(str(backend_dir))

root_dir = backend_dir.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))

from ai_send import load_messages, LiveTopicAnalyzer
from ai_service import ANALYSIS_PROMPT_TEMPLATE, get_chatgpt_response

HTML_FILE_PATH = root_dir / "index" / "index.html"

def update_html_transcript(results_data):
    """HTMLのテキストエリアを最新のJSONデータで更新する関数"""
    if not HTML_FILE_PATH.exists():
        return
    
    json_str = json.dumps(results_data, ensure_ascii=False, indent=2)
    try:
        html_content = HTML_FILE_PATH.read_text(encoding="utf-8")
        updated_html = re.sub(
            r'(<textarea\s+[^>]*id=["\']transcript["\'][^>]*>)(.*?)(</textarea>)',
            rf'\g<1>\n{json_str}\n\g<3>',
            html_content,
            flags=re.DOTALL | re.IGNORECASE
        )
        HTML_FILE_PATH.write_text(updated_html, encoding="utf-8")
    except Exception as e:
        print(f"HTML更新エラー: {e}")


def stream_analysis_updates():
    """
    話題が確定してChatGPTから出力が返るたびに、
    リアルタイムでresultsを更新しながらyieldで返していくジェネレータ
    """
    messages = load_messages(None)
    if not messages:
        yield {"status": "error", "message": "会話テキストが空です"}
        return

    results = []

    def handle_save_analysis(topic, analysis):
        # 個別の保存用（必要に応じて）
        pass

    analyzer = LiveTopicAnalyzer(
        get_chatgpt_response=get_chatgpt_response,
        prompt_template=ANALYSIS_PROMPT_TEMPLATE,
        save_analysis=handle_save_analysis,
    )

    from topic_detecter import TopicShiftDetector

    def handle_completed_topic(topic):
        # ChatGPTへ送信し、結果を取得（ここでAIの返答を待つ）
        new_item = analyzer.analyze_topic(topic)
        if new_item:
            # analyzer.analyze_topic 内ですでにresults相当の統合・追加制御が行われている場合はそちらを利用
            # ここでは analyzer.results をそのまま最新の状態として扱う
            pass

    # ※ analyzer.results をリアルタイムで監視・取得できるように構築
    # analyzer.analyze_topic が呼ばれるたびに results が更新される仕組みを利用します
    
    # 実際の発話ループ
    detector = TopicShiftDetector(on_topic_completed=None) # 後からカスタム処理を差し込む

    # トピックが完了するたびにリアルタイムで yield するためのカスタム処理
    completed_topics = []

    def custom_topic_completed(topic):
        # 1. 話題をChatGPTに投げて結果を得る
        res = analyzer.analyze_topic(topic)
        if res:
            # 最新の全結果リスト（analyzer.results）を yield 用に保持
            current_results = list(analyzer.results)
            # HTML側のテキストエリアも即時ファイル書き込みで更新
            update_html_transcript(current_results)
            completed_topics.append(current_results)

    detector.on_topic_completed = custom_topic_completed

    for message in messages:
        detector.add_message(message)
        # もしこのメッセージ追加で話題が完了していれば、上のコールバックが走り
        # completed_topics にデータが追加されるので随時 yield できるようにチェック
        while completed_topics:
            yield {"status": "updated", "results": completed_topics.pop(0)}

    # 最後のフラッシュ
    final_topic = detector.flush_topic()
    if final_topic:
        custom_topic_completed(final_topic)
        while completed_topics:
            yield {"status": "completed", "results": completed_topics.pop(0)}