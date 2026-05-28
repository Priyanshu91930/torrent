import asyncio
from pyrogram import Client
from dotenv import load_dotenv
import os

load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

if not API_ID or not API_HASH:
    print("Error: API_ID and API_HASH must be set in your .env file!")
    exit(1)

async def main():
    print("Generating Pyrogram String Session...")
    print("Please follow the prompts to log into your account.")
    
    async with Client("my_account", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
        session_string = await app.export_session_string()
        print("\n\n" + "="*50)
        print("✅ SUCCESS! Here is your String Session:\n")
        print(session_string)
        print("\n" + "="*50)
        print("\nCopy the text above and paste it into your .env file as:")
        print("STRING_SESSION=your_session_string_here")

if __name__ == "__main__":
    asyncio.run(main())
