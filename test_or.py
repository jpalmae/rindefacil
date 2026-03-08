import requests
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get("OPENROUTER_API_KEY")

response = requests.post(
  url="https://openrouter.ai/api/v1/chat/completions",
  headers={
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
  },
  json={
    "model": "openai/gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "What is 2+2?"
      }
    ]
  }
)

print(response.status_code)
print(response.text)
