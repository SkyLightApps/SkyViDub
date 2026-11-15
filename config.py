import os
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GEMMA_MODEL = os.getenv("GEMMA_MODEL", "gemma-4-26b-a4b-it")
QWEN_MODEL_ID = os.getenv("QWEN_MODEL_ID", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
REF_AUDIO = os.getenv("REF_AUDIO", "voices/academic_en.wav")
TTS_LANG = os.getenv("TTS_LANG", "English")

REF_TEXT = (
    "Artificial intelligence opens new frontiers for global education and science, "
    "making knowledge accessible across any language barrier."
)
