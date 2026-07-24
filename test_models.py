import os
import json
from urllib.request import Request, urlopen
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("API key not found in .env")
    exit(1)

url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
req = Request(url)
try:
    with urlopen(req) as response:
        data = json.load(response)
        print("Available Models:")
        for m in data.get("models", []):
            name = m.get("name", "")
            # Only print gemini models that support generateContent
            if "gemini" in name and "generateContent" in m.get("supportedGenerationMethods", []):
                print(f"- {name.replace('models/', '')}")
except Exception as e:
    print(f"Error: {e}")
