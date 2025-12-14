#!/usr/bin/env python3
"""
Gemini TTS для Claude Code Stop hook
Читает transcript, извлекает последнее сообщение и озвучивает
"""

import json
import sys
import os
import subprocess
import base64
import wave
import hashlib
from pathlib import Path
import urllib.request

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
CACHE_DIR = Path.home() / ".claude" / "tts_cache"
VOICE = "Aoede"  # Puck, Kore, Charon, Aoede, Fenrir, Leda, Orus, Zephyr

ASMR_PROMPT = """You are a gentle ASMR narrator giving a brief status update. Summarize what was done in 1-2 sentences.
Be informative but soft. Speak in first person. Use calming, natural phrasing.
Focus on WHAT was accomplished. Skip file paths and line numbers.
Respond in the same language as the input.

Examples:
- "I updated the config file and fixed the bug in line 42 of auth.py" → "Fixed the auth bug for you... config is updated"
- "Добавил endpoint для экспорта и написал тесты" → "Добавила экспорт и тесты к нему..."
- "Refactored the database module, added connection pooling" → "Database refactored... now with connection pooling"
"""

CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_cache_path(text: str) -> Path:
    hash_key = hashlib.md5(f"{text}{VOICE}".encode()).hexdigest()
    return CACHE_DIR / f"{hash_key}.wav"


def summarize_asmr(text: str) -> str:
    """Использует Gemini Flash чтобы сделать ASMR-саммари"""
    if not GEMINI_API_KEY or not text:
        return text

    request_body = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": f"{ASMR_PROMPT}\n\nSummarize this:\n{text}"}]}
        ],
        "generationConfig": {
            "maxOutputTokens": 100,
            "temperature": 0.7
        }
    }).encode()

    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        data=request_body,
        headers={
            "x-goog-api-key": GEMINI_API_KEY,
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        sys.stderr.write(f"Summary error: {e}\n")
        return text[:200]  # fallback to truncated original


def synthesize(text: str, output_path: Path) -> bool:
    if not GEMINI_API_KEY:
        return False

    # Добавляем ASMR стиль в сам TTS запрос
    styled_text = f"Say this softly and slowly, like a gentle whisper: {text}"

    request_body = json.dumps({
        "contents": [{"parts": [{"text": styled_text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": VOICE}
                }
            }
        }
    }).encode()

    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent",
        data=request_body,
        headers={
            "x-goog-api-key": GEMINI_API_KEY,
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            audio_b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
            pcm_data = base64.b64decode(audio_b64)

            with wave.open(str(output_path), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(pcm_data)
            return True
    except Exception as e:
        sys.stderr.write(f"TTS error: {e}\n")
        return False


def play_async(audio_path: Path):
    if sys.platform == "darwin":
        subprocess.Popen(
            ["afplay", str(audio_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
    else:
        for player in [["paplay"], ["aplay", "-q"], ["mpv", "--really-quiet"]]:
            try:
                subprocess.Popen(
                    player + [str(audio_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                break
            except FileNotFoundError:
                continue


def speak(text: str):
    if not text or not GEMINI_API_KEY:
        return

    cache_path = get_cache_path(text)
    if not cache_path.exists():
        if not synthesize(text, cache_path):
            return
    play_async(cache_path)


def extract_last_assistant_message(transcript_path: str) -> str:
    """Читает JSONL транскрипт и возвращает последнее сообщение ассистента"""
    try:
        messages = []
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    messages.append(entry)
                except json.JSONDecodeError:
                    continue

        # Ищем последнее сообщение ассистента (в обратном порядке)
        for entry in reversed(messages):
            if entry.get("type") == "assistant":
                message = entry.get("message", {})
                content = message.get("content", [])

                # Собираем текст из всех text-блоков
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        texts.append(block)

                if texts:
                    return " ".join(texts)

        return ""
    except Exception as e:
        sys.stderr.write(f"Transcript error: {e}\n")
        return ""


def main():
    try:
        hook_data = json.load(sys.stdin)
    except Exception as e:
        sys.stderr.write(f"JSON error: {e}\n")
        sys.exit(0)

    # Получаем путь к транскрипту
    transcript_path = hook_data.get("transcript_path", "")

    if not transcript_path or not os.path.exists(transcript_path):
        speak("Задача выполнена")
        sys.exit(0)

    # Извлекаем последнее сообщение
    last_message = extract_last_assistant_message(transcript_path)

    if not last_message:
        speak("Готово")
        sys.exit(0)

    # Создаём ASMR-саммари через Gemini Flash
    asmr_summary = summarize_asmr(last_message)
    speak(asmr_summary)
    sys.exit(0)


if __name__ == "__main__":
    main()
