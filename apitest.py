import google.generativeai as genai

API_KEY = "AIzaSyC_ruQSZKoTgJYI7aaR-fGDWz3H1g6S_f8"  # <--- Paste your key here
genai.configure(api_key=API_KEY)

print("Listing available models...")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)