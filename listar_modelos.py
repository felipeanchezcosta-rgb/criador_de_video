import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
print("Modelos disponíveis na sua conta do Groq:\n")
for model in sorted(client.models.list().data, key=lambda m: m.id):
    print(" ", model.id)