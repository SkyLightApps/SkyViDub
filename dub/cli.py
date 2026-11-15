import argparse
import faulthandler
import os
import subprocess
import sys
import tempfile

faulthandler.enable()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SR = 24000


def slog(msg: str):
    try:
        print(str(msg), flush=True)
    except UnicodeEncodeError:
        print(str(msg).encode("utf-8", "replace").decode("utf-8"), flush=True)


def run(cmd):
    try:
        subprocess.run(
            cmd, check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace"
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Помилка FFmpeg [{e.returncode}]: {(e.stderr or '')[-500:]}")


def extract_audio(video: str, out_wav: str):
    run(["ffmpeg", "-y", "-i", video, "-ar", str(SR), "-ac", "1", out_wav])


def transcribe(wav: str, src: str):
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(wav, language=None if src == "auto" else src, vad_filter=True)
    out = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments if s.text.strip()]
    print(f"[dub] Розпізнано мовних сегментів (VAD): {len(out)}")
    return out


LANG_NAMES = {
    "uk": "Ukrainian",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}

LANG_KAPPA = {
    "en": 1.00,
    "uk": 0.95,
    "es": 0.95,
    "it": 0.95,
    "pt": 0.94,
    "fr": 0.92,
    "de": 0.90,
    "zh": 0.65,
    "ja": 0.70,
    "ko": 0.75,
}

NUM_EXAMPLES = {
    "uk": "4.7 -> чотири крапка сім",
    "en": "4.7 -> four point seven",
    "de": "4.7 -> vier Komma sieben",
    "fr": "4.7 -> quatre virgule sept",
    "es": "4.7 -> cuatro punto siete",
    "it": "4.7 -> quattro punto sette",
    "pt": "4.7 -> quatro ponto sete",
    "zh": "4.7 -> 四点七",
    "ja": "4.7 -> よんてんなな",
    "ko": "4.7 -> 사점칠",
}


def script_rule(dst: str) -> str:
    return (
        f"Write EVERYTHING in the {dst} script: transliterate any foreign words, names and brands "
        f"into {dst} letters (no mixed scripts) - TTS misreads foreign spellings. "
    )


def translate_batch(texts, src: str, dst: str, budgets=None, tries: int = 8):
    from google import genai
    from google.genai import types
    import config
    import time

    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    dst_name = LANG_NAMES.get(dst, dst)
    num_ex = NUM_EXAMPLES.get(dst, NUM_EXAMPLES["en"])
    num_rule = (
        f"Write all numbers, versions and dotted abbreviations as full WORDS in {dst_name} "
        f"({num_ex}) - TTS mangles digits and dots inside words. "
        f"Keep normal sentence punctuation as is. "
        f"{script_rule(dst_name)}"
    )
    if budgets:
        limits = (
            "\nHard length limits (MAX characters per line, cut adjectives/filler but keep meaning): "
            + ", ".join(f"{i + 1}:{b}" for i, b in enumerate(budgets)) + "\n"
        )
        prompt = (
            f"Translate from {src} to {dst_name}. Each line MUST fit its character limit - "
            f"this is voiceover timing, shorter is fine, longer is broken. "
            f"{num_rule}"
            f"No explanations. Return ONLY the numbered lines in the same format 'N. text'.\n"
            f"{limits}{numbered}"
        )
    else:
        prompt = (
            f"Translate from {src} to {dst_name}. Keep each line short, similar length to source. "
            f"{num_rule}"
            f"No explanations. Return ONLY the numbered lines in the same format 'N. text'.\n{numbered}"
        )
    last = None
    for attempt in range(1, tries + 1):
        try:
            resp = client.models.generate_content(
                model=config.GEMMA_MODEL,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=2000,
                    thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
                contents=prompt,
            )
            break
        except Exception as e:
            last = e
            time.sleep(min(3 * attempt, 20))
    else:
        raise last
    lines = (resp.text or "").strip().split("\n")
    out = []
    for i in range(len(texts)):
        if i < len(lines) and lines[i].lstrip()[: len(str(i + 1)) + 1].rstrip(". ") == str(i + 1):
            out.append(lines[i].split(".", 1)[1].strip() if "." in lines[i] else lines[i].strip())
        else:
            out.append(lines[i].strip() if i < len(lines) else texts[i])
    return out


CPS = 14
MAX_TEMPO = 1.4
RESYNTH_RATIO = 1.6


def slot_of(segments, i: int) -> float:
    end = segments[i + 1]["start"] if i + 1 < len(segments) else segments[i]["end"]
    return max(0.5, end - segments[i]["start"])


def budget_of(slot: float, dst: str) -> int:
    kappa = LANG_KAPPA.get(dst, 1.0)
    return max(8, int(slot * CPS * kappa))


def translate_all(segments, src: str, dst: str, log=print):
    res = []
    for i in range(0, len(segments), 8):
        chunk = segments[i:i + 8]
        budgets = [budget_of(slot_of(segments, j), dst) for j in range(i, i + len(chunk))]
        log(f"[dub] Пакетний переклад сегментів {i + 1}–{i + len(chunk)} із {len(segments)} (Gemma API)...")
        res.extend(translate_batch([s["text"] for s in chunk], src, dst, budgets=budgets))
    return res


def shorten_text(text: str, budget: int, dst: str) -> str:
    from google import genai
    from google.genai import types
    import config
    import time

    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    dst_name = LANG_NAMES.get(dst, dst)
    last = text
    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(
                model=config.GEMMA_MODEL,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=300,
                    thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
                contents=(
                    f"Shorten this {dst_name} voiceover line to at most {budget} characters, "
                    f"keep the core meaning. Output ONLY the shortened line, nothing else.\n{last}"
                ),
            )
            cand = (resp.text or "").strip()
            if cand:
                return cand
        except Exception:
            time.sleep(min(3 * attempt, 10))
    return last


def separate_music(wav: str, work: str) -> str | None:
    out_dir = os.path.join(work, "demucs_out")
    cmd = [sys.executable, "-m", "demucs", "--two-stems", "vocals", "-o", out_dir, wav]
    try:
        subprocess.run(cmd + ["-d", "cuda"], check=True, capture_output=True)
    except Exception:
        subprocess.run(cmd + ["-d", "cpu"], check=True, capture_output=True)
    cands = []
    for root, _, files in os.walk(out_dir):
        if "no_vocals.wav" in files:
            cands.append(os.path.join(root, "no_vocals.wav"))
    if not cands:
        return None
    bed = os.path.join(work, "music_bed.wav")
    run(["ffmpeg", "-y", "-i", cands[0], "-ar", str(SR), "-ac", "1", bed])
    return bed


def merge_semantic(segments, max_gap: float = 1.2, max_dur: float = 12.0):
    import re
    END = re.compile(r"[.!?…]+$")
    chunks, cur = [], None
    for s in segments:
        gap = s["start"] - (cur["end"] if cur else s["start"])
        if cur is None or (gap > max_gap and END.search(cur["text"].strip())):
            if cur:
                chunks.append(cur)
            cur = dict(s)
        else:
            cur["end"] = s["end"]
            cur["text"] = (cur["text"] + " " + s["text"]).strip()
        if cur["end"] - cur["start"] >= max_dur and END.search(cur["text"].strip()):
            chunks.append(cur)
            cur = None
    if cur:
        chunks.append(cur)
    if len(chunks) >= 2 and chunks[-1]["end"] - chunks[-1]["start"] < 1.0:
        prev = chunks.pop()
        chunks[-1]["end"] = prev["end"]
        chunks[-1]["text"] = (chunks[-1]["text"] + " " + prev["text"]).strip()
    return chunks


def fit_filter(tts_dur: float, seg_dur: float) -> tuple:
    if tts_dur > seg_dur * 1.05:
        ratio = min(tts_dur / seg_dur, MAX_TEMPO)
        squeezed = tts_dur / seg_dur > MAX_TEMPO
        return f"atempo={ratio:.3f},atrim=0:{seg_dur:.3f},asetpts=PTS-STARTPTS", squeezed
    if tts_dur < seg_dur * 0.9:
        return f"apad=whole_dur={seg_dur:.3f},asetpts=PTS-STARTPTS", False
    return "anull,asetpts=PTS-STARTPTS", False


def build_dub_track(chunks, total_dur: float, out_wav: str, bg_wav=None, bg_vol: float = 0.0, log=print):
    import soundfile as sf
    inputs, filters, mix = [], [], []
    squeezed_n = 0
    for i, (path, start) in enumerate(chunks):
        inputs += ["-i", path]
        info = sf.info(path)
        seg_end = chunks[i + 1][1] if i + 1 < len(chunks) else total_dur
        seg_dur = max(0.3, seg_end - start)
        dur = info.frames / info.samplerate
        filt, squeezed = fit_filter(dur, seg_dur)
        if squeezed:
            squeezed_n += 1
            log(f"[dub] Сегмент {i + 1}: застосовано обмеження прискорення {MAX_TEMPO}x + atrim")
        filters.append(f"[{i}:a]{filt},adelay={int(start * 1000)}|{int(start * 1000)}[a{i}]")
        mix.append(f"[a{i}]")
    if squeezed_n:
        log(f"[dub] Прискорено сегментів понад ліміт: {squeezed_n}/{len(chunks)}")
    else:
        log(f"[dub] 100% сегментів укладено в часові слоти (atempo <= {MAX_TEMPO}x)")
    n = len(chunks)
    if bg_wav and bg_vol > 0:
        inputs += ["-i", bg_wav]
        filters.append(f"[{n}:a]volume={bg_vol}[bg]")
        mix.append("[bg]")
        n += 1
    filters.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=longest[out]")
    run([
        "ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
        "-map", "[out]", "-t", f"{total_dur:.3f}", "-ar", str(SR), "-ac", "1", out_wav
    ])


def mux(video: str, dub_wav: str, out_mp4: str):
    run([
        "ffmpeg", "-y", "-i", video, "-i", dub_wav,
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
        "-shortest", out_mp4
    ])


def run_job(job: dict, log=print, progress_cb=None):
    import soundfile as sf
    import config
    import settings
    import tts_voice

    def set_stage(stage_idx: int, pct: float, status_text: str):
        if progress_cb:
            try:
                progress_cb(stage_idx, pct, status_text)
            except Exception:
                pass

    out_mp4 = job.get("out") or os.path.splitext(job["input"])[0] + "_dubbed.mp4"
    dst, src = job.get("dst", "en"), job.get("src", "auto")
    synth_lang = LANG_NAMES.get((job.get("lang") or dst).lower(), dst)
    work = tempfile.mkdtemp(prefix="skyvidub_")
    orig_wav = os.path.join(work, "orig.wav")

    set_stage(1, 10, "Крок 1/5: Витягнення аудіо з відео...")
    log("[dub] 1/5 Витягую аудіо з відео...")
    extract_audio(job["input"], orig_wav)
    total_dur = sf.info(orig_wav).frames / SR
    log(f"[dub] Тривалість аудіо: {total_dur:.1f} с")

    set_stage(2, 25, "Крок 2/5: Розпізнавання мови (faster-whisper)...")
    log("[dub] 2/5 Розпізнавання мови (faster-whisper)...")
    segments = transcribe(orig_wav, src)
    if not segments:
        log("[dub] Мовлення у відео не знайдено.")
        return None
    if job.get("semantic", False):
        before = len(segments)
        segments = merge_semantic(segments)
        log(f"[dub] Об'єднання фраз за змістом: {before} -> {len(segments)} речень")
    bed = None
    if job.get("music", False):
        set_stage(2, 35, "Крок 2.5/5: Відокремлення фонової музики (demucs)...")
        log("[dub] 2.5/5 Відокремлюю фонову музику (demucs)...")
        tts_voice.unload_model()
        bed = separate_music(orig_wav, work)
        log("[dub] Фонова музика: " + ("готово" if bed else "не вдалося, без фону"))

    set_stage(3, 45, "Крок 3/5: Переклад тексту (Gemma)...")
    log(f"[dub] 3/5 Переклад тексту (Gemma, пакетами по 8 рядків, мова: {dst})...")
    translated = translate_all(segments, src, dst, log=log)
    for idx, (seg, tr) in enumerate(zip(segments, translated), 1):
        slot = slot_of(segments, idx - 1)
        b_lim = budget_of(slot, dst)
        log(f"  [{idx:02d}] (слот {slot:.1f}с | ліміт {b_lim} симв.): {tr}")

    set_stage(4, 65, "Крок 4/5: Озвучення клонованим голосом (Qwen3-TTS)...")
    log("[dub] 4/5 Озвучення клонованим голосом (Qwen3-TTS)...")
    model = tts_voice.get_model()
    voice = job.get("voice", "orig")
    if voice == "orig":
        ref_end = min(20.0, segments[min(2, len(segments) - 1)]["end"])
        ref_wav = os.path.join(work, "ref.wav")
        run(["ffmpeg", "-y", "-i", orig_wav, "-ss", "0", "-t", str(ref_end), ref_wav])
        log(f"[dub] Беру зразок голосу з відео (перші {ref_end:.1f} с)...")
        prompt = model.create_voice_clone_prompt(ref_audio=ref_wav, x_vector_only_mode=True)
        gen = lambda t: model.generate_voice_clone(
            text=tts_voice.clean_for_tts(t), language=synth_lang, voice_clone_prompt=prompt
        )
    else:
        if settings.get_base_voice_row(voice) is None:
            log(f"[dub] Немає такого голосу в базі: {voice}")
            return None
        log(f"[dub] Використовую голос із пресетів: {voice}")
        prompt = tts_voice.get_clone_prompt(voice)
        gen = lambda t: model.generate_voice_clone(
            text=tts_voice.clean_for_tts(t), language=synth_lang, voice_clone_prompt=prompt
        )

    chunks = []
    resynth = bool(job.get("resynth", False))
    for i, (seg, text) in enumerate(zip(segments, translated)):
        slot = slot_of(segments, i)
        wavs, sr = gen(text)
        dur = len(wavs[0]) / sr
        if resynth and dur > slot * RESYNTH_RATIO:
            budget = budget_of(slot, dst)
            log(f"[dub] Фраза {i + 1} вийшла довшою за слот ({dur:.1f}с > {slot:.1f}с) — прошу Gemma коротше і перегенеровую...")
            text2 = shorten_text(text, budget, dst)
            wavs, sr = gen(text2)
            log(f"[dub] Фраза {i + 1}: перегенеровано ({len(wavs[0]) / sr:.1f}с)")
        path = os.path.join(work, f"ch{i:03d}.wav")
        sf.write(path, wavs[0], sr)
        chunks.append((path, seg["start"]))
        pct = 65 + int(22 * (i + 1) / max(1, len(segments)))
        set_stage(4, pct, f"Крок 4/5: Озвучено фразу {i + 1} із {len(segments)}...")
        log(f"[dub] Озвучено фразу {i + 1}/{len(segments)} ({dur:.1f} с у слот {slot:.1f} с)")

    set_stage(5, 90, "Крок 5/5: Підгонка по часу та склейка відео...")
    log("[dub] 5/5 Підгонка по часу (ffmpeg) та склейка з відео...")
    dub_wav = os.path.join(work, "dub.wav")
    bg = float(job.get("bg", 0.0))
    if bed:
        build_dub_track(chunks, total_dur, dub_wav, bg_wav=bed, bg_vol=1.0, log=log)
    else:
        build_dub_track(
            chunks, total_dur, dub_wav,
            bg_wav=orig_wav if bg > 0 else None, bg_vol=bg, log=log
        )
    mux(job["input"], dub_wav, out_mp4)
    set_stage(5, 100, f"Готово! Збережено: {os.path.basename(out_mp4)}")
    log(f"[dub] ГОТОВО: {out_mp4}")
    return out_mp4


def main():
    ap = argparse.ArgumentParser(description="SkyViDub: автоматичний дубляж відео")
    ap.add_argument("input", help="вхідний відеофайл")
    ap.add_argument("--dst", default="en", help="цільова мова перекладу (en/de/fr/es/it/pt/zh/ja/ko)")
    ap.add_argument("--src", default="auto", help="мова оригіналу (auto/uk/en)")
    ap.add_argument("--voice", default="orig", help="orig або ім'я голосу з бази пресетів")
    ap.add_argument("--out", default=None, help="вихідний відеофайл (за замовч. input_dubbed.mp4)")
    ap.add_argument("--bg", type=float, default=0.0, help="гучність оригінального фону 0..0.5")
    ap.add_argument("--lang", default=None, help="мова синтезу (за замовч. = dst)")
    ap.add_argument(
        "--resynth", action="store_true",
        help="скорочення фраз, що перевищили слот (>1.6x), через Gemma та повторне озвучення"
    )
    ap.add_argument(
        "--music", action="store_true",
        help="відокремлення музики та фону без вокалу через Demucs"
    )
    ap.add_argument(
        "--semantic", action="store_true",
        help="об'єднання дрібних фраз у цілі речення за пунктуацією"
    )
    args = ap.parse_args()
    run_job(vars(args), log=lambda m: slog(m))


if __name__ == "__main__":
    main()
