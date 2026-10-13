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