import os
from openai import OpenAI
from dotenv import load_dotenv

# Load your .env file if you have one, or set the variable directly
load_dotenv()
api_key = os.getenv("MINIMAX_API_KEY", "your_api_key_here")

# Initialize the client pointing to the MiniMax endpoint
client = OpenAI(
    api_key=api_key,
    base_url="https://api.minimax.io/v1",
)

try:
    print("⏳ Sending test request to MiniMax...")
    
    response = client.chat.completions.create(
        model="MiniMax-M3",  # Ensure this matches your active model ID
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'MiniMax API connected successfully'."}
        ]
    )
    
    print("\n✅ API call succeeded!")
    print(f"Response: {response.choices[0].message.content}")

except Exception as e:
    print(f"\n❌ Connection failed: {e}")