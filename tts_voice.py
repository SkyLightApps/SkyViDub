import os

os.environ.setdefault("TORCHDYNAMO_VERBOSE", "0")
os.environ.setdefault("TORCH_LOGS", "-all")
os.environ.setdefault("TORCHDYNAMO_REPRO_LEVEL", "0")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault(
    "TORCHINDUCTOR_CACHE_DIR",
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SkyViDub", "inductor"),
)

import warnings
warnings.filterwarnings("ignore")
import logging
for _n in ("torch._dynamo", "torch._inductor", "torch._functorch"):
    logging.getLogger(_n).setLevel(logging.ERROR)

import gc
import re
import subprocess
import sys
import tempfile
import threading

_QWEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Qwen3-TTS-streaming")
if os.path.isdir(_QWEN_DIR) and _QWEN_DIR not in sys.path:
    sys.path.insert(0, _QWEN_DIR)
import numpy as np
import soundfile as sf
import torch
import config
import settings

torch.set_float32_matmul_precision("high")
try:
    import torch._dynamo
    torch._dynamo.config.suppress_errors = True
    torch._dynamo.config.verbose = False
    try:
        from torch._logging import set_logs
        set_logs(dynamo=logging.ERROR, inductor=logging.ERROR, aot=logging.ERROR)
    except Exception:
        pass
except Exception:
    pass

_model = None
_clone_prompts = {}
_lock = threading.RLock()

_RE_STAR = re.compile(r"\*[^*]+\*")
_RE_SQUARE = re.compile(r"\[[^\]]+\]")
_RE_PAREN = re.compile(r"\([^)]+\)")
_RE_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]"
)
_RE_WS = re.compile(r"\s+")
_RE_HYPHEN = re.compile(r"(?<=\w)[-‐‑](?=\w)")


def clean_for_tts(text: str) -> str:
    t = _RE_STAR.sub(" ", text)
    t = _RE_SQUARE.sub(" ", t)
    t = _RE_PAREN.sub(lambda m: " " if len(m.group(0)) < 40 else m.group(0), t)
    t = _RE_EMOJI.sub("", t)
    t = t.replace("*", "").replace("#", "")
    t = _RE_HYPHEN.sub(" ", t)
    t = _RE_WS.sub(" ", t).strip(" ,.-")
    return t or "..."


def unload_model():
    global _model, _clone_prompts
    with _lock:
        _clone_prompts = {}
        if _model is not None:
            try:
                del _model
            except Exception:
                pass
            _model = None
        gc.collect()
        try:
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        except Exception:
            pass
        print("[tts] Модель вивантажено з VRAM")


def get_model():
    global _model
    with _lock:
        if _model is None:
            from qwen_tts import Qwen3TTSModel
            print(f"[tts] Завантаження моделі {config.QWEN_MODEL_ID}...")
            try:
                _model = Qwen3TTSModel.from_pretrained(
                    config.QWEN_MODEL_ID,
                    device_map="cuda:0",
                    dtype=torch.bfloat16,
                    attn_implementation="flash_attention_2",
                )
            except Exception:
                _model = Qwen3TTSModel.from_pretrained(
                    config.QWEN_MODEL_ID,
                    device_map="cuda:0",
                    dtype=torch.bfloat16,
                    attn_implementation="sdpa",
                )
            try:
                _model.enable_streaming_optimizations(
                    decode_window_frames=80,
                    use_compile=True,
                    use_cuda_graphs=False,
                    compile_mode="reduce-overhead",
                    use_fast_codebook=True,
                    compile_codebook_predictor=True,
                    compile_talker=True,
                )
                print("[tts] Оптимізацію torch.compile (reduce-overhead) активовано")
            except Exception as e2:
                print(f"[tts] Оптимізацію пропущено: {e2}")
    return _model


def get_clone_prompt(name: str):
    if name not in _clone_prompts:
        row = settings.get_base_voice_row(name) or settings.get_base_voice_row("academic_en")
        ref_audio = row[0]
        if not os.path.isabs(ref_audio):
            ref_audio = os.path.join(os.path.dirname(os.path.abspath(__file__)), ref_audio)
        m = get_model()
        _clone_prompts[name] = m.create_voice_clone_prompt(
            ref_audio=ref_audio,
            x_vector_only_mode=True,
        )
    return _clone_prompts[name]


def current_status() -> str:
    return f"Клон (Base x-vector), активний пресет: {settings.get_base_voice()}"


def speak_to_ogg(text: str, out_ogg: str):
    clean = clean_for_tts(text)
    if clean != text:
        print(f"[tts] Нормалізація: {text[:80]!r} -> {clean[:80]!r}")
    m = get_model()
    name = settings.get_base_voice()
    prompt = get_clone_prompt(name)
    wavs, sr = m.generate_voice_clone(
        text=clean,
        language=config.TTS_LANG,
        voice_clone_prompt=prompt,
    )
    audio = wavs[0]

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp_wav = tf.name
    try:
        sf.write(tmp_wav, audio, sr)
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_wav, "-c:a", "libopus", "-b:a", "64k", out_ogg],
            check=True, capture_output=True,
        )
    finally:
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)
    return out_ogg
