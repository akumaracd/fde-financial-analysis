import os, json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key  = os.environ["GROQ_API_KEY"],
    base_url = "https://api.groq.com/openai/v1",
)

SYSTEM_PROMPT = """
You are a senior financial analyst.
When asked for risks, return a JSON array of risk objects.
Each object must have: name, level (Critical/High/Medium/Low), mitigation.
Return JSON ONLY — no prose, no markdown fences.
"""

response = client.chat.completions.create(
    model = "openai/gpt-oss-120b",
    temperature = 0.3,
    max_tokens  = 800,
    messages    = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": "List 3 risks of AI agents in banking."}
    ]
)

raw   = response.choices[0].message.content
risks = json.loads(raw)

for r in risks:
    print(f"[{r['level']}] {r['name']}")
    print(f"  → {r['mitigation']}")
    print()
