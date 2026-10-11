import os
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
  raise ValueError("OPENAI_API_KEYが設定されていません。")