from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)


def get_chatgpt_response(prompt: str) -> str:
  response = client.chat.completions.create(
      model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}]
  )
  return response.choices[0].message.content