#!/usr/bin/env python3
"""
Claude Code Stop Hook for TTS
Extracts last assistant message, summarizes it, and sends to TTS daemon.

Usage in ~/.claude/settings.json:
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "python3 ~/.claude/hooks/speak_hook.py",
        "timeout": 15
      }]
    }]
  }
}
"""

import json
import os
import socket
import sys
import urllib.request
from pathlib import Path

# Paths
SOCKET_PATH = Path.home() / ".claude" / "tts.sock"

# Config
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SUMMARIZE_MODEL = "gemini-2.0-flash"

ASMR_PROMPT = """Summarize in 1 short sentence. Soft, calm tone. First person. Same language as input.
Skip file paths and technical details. Focus on what was accomplished.

Examples:
- "I updated config and fixed bug in auth.py:42" → "Fixed the auth bug for you..."
- "Добавил endpoint и написал тесты" → "Добавила экспорт и тесты..."
- "Refactored database, added pooling" → "Database refactored with pooling..."

Summarize this:"""


def extract_last_assistant_message(transcript_path: str) -> str:
    """Extract last assistant text message from JSONL transcript."""
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

        # Find last assistant message (reverse order)
        for entry in reversed(messages):
            if entry.get("type") == "assistant":
                message = entry.get("message", {})
                content = message.get("content", [])

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


def summarize_asmr(text: str) -> str:
    """Create ASMR-style summary using Gemini Flash."""
    if not GEMINI_API_KEY or not text:
        return text[:200] if text else ""

    # Limit input to avoid long processing
    text = text[:1000]

    request_body = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": f"{ASMR_PROMPT}\n{text}"}]}
        ],
        "generationConfig": {
            "maxOutputTokens": 60,
            "temperature": 0.7
        }
    }).encode()

    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{SUMMARIZE_MODEL}:generateContent",
        data=request_body,
        headers={
            "x-goog-api-key": GEMINI_API_KEY,
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        sys.stderr.write(f"Summary error: {e}\n")
        # Fallback: first sentence or truncated
        first_sentence = text.split('.')[0]
        return first_sentence[:100] if first_sentence else "Done"


def send_to_daemon(text: str) -> bool:
    """Send text to TTS daemon via Unix socket."""
    if not SOCKET_PATH.exists():
        sys.stderr.write(f"TTS daemon not running (socket not found: {SOCKET_PATH})\n")
        return False

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(str(SOCKET_PATH))
        sock.sendall(text.encode('utf-8'))
        sock.close()
        return True
    except Exception as e:
        sys.stderr.write(f"Socket error: {e}\n")
        return False


def main():
    # Read hook input from stdin
    try:
        hook_data = json.load(sys.stdin)
    except Exception as e:
        sys.stderr.write(f"JSON input error: {e}\n")
        sys.exit(0)

    transcript_path = hook_data.get("transcript_path", "")

    # Fallback message if no transcript
    if not transcript_path or not os.path.exists(transcript_path):
        send_to_daemon("Done")
        sys.exit(0)

    # Extract last message
    last_message = extract_last_assistant_message(transcript_path)

    if not last_message:
        send_to_daemon("Ready")
        sys.exit(0)

    # Summarize and send
    summary = summarize_asmr(last_message)
    send_to_daemon(summary)
    sys.exit(0)


if __name__ == "__main__":
    main()
