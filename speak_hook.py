#!/usr/bin/env python3
"""
Claude Code Stop Hook for TTS
Extracts last assistant message and sends to TTS daemon.

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
from pathlib import Path

# Paths
SOCKET_PATH = Path("/tmp/claude-tts.sock")

# Config
MAX_TEXT_LENGTH = 1000


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

    # Send raw text to daemon (limited to MAX_TEXT_LENGTH)
    text_to_send = last_message[:MAX_TEXT_LENGTH]
    send_to_daemon(text_to_send)
    sys.exit(0)


if __name__ == "__main__":
    main()
