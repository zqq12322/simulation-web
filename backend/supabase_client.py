import os
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv

# Load backend/.env independently of the current working directory
load_dotenv(Path(__file__).with_name(".env"))

url: str = os.getenv("SUPABASE_URL", "YOUR_SUPABASE_URL_HERE")
key: str = os.getenv("SUPABASE_KEY", "YOUR_SUPABASE_ANON_KEY_HERE")

# Initialize only if keys are somewhat valid to avoid crashes if user hasn't set them yet
supabase: Client | None = None
if url != "YOUR_SUPABASE_URL_HERE" and key != "YOUR_SUPABASE_ANON_KEY_HERE":
    try:
        supabase = create_client(url, key)
    except Exception as e:
        print(f"Failed to initialize Supabase client: {e}")
