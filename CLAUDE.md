# TTS Hook for Claude Code

Text-to-speech integration for Claude Code using Google Gemini Live API with ~100ms latency.

## Architecture

The system consists of three main components:

- **tts_daemon.py** — Persistent WebSocket daemon that maintains connection to Gemini Live API. Handles audio streaming, caching, and playback.
- **speak_hook.py** — Claude Code Stop Hook that extracts last assistant message from transcript and sends to daemon via Unix socket.
- **Configuration** — `~/.claude/tts_config.json` with mode, voice, style, language, and custom prompts.

```
Claude Code
    ↓ (transcript_path)
speak_hook.py (parse transcript)
    ↓ (last message via socket)
tts_daemon.py (Unix socket listener)
    ↓ (persistent WebSocket)
Gemini Live API
    ↓ (PCM audio stream)
Audio Player
```

## Setup

### 1. API Key
Set `GEMINI_API_KEY` environment variable:
```bash
export GEMINI_API_KEY="your-api-key"
```

### 2. Configuration
Create `~/.claude/tts_config.json`:
```json
{
  "mode": "summary",
  "voice": "Aoede",
  "style": "asmr",
  "language": "russian",
  "max_chars": 1000,
  "custom_styles": {}
}
```

### 3. Install Hook
Copy `speak_hook.py` to `~/.claude/hooks/` and configure in `~/.claude/settings.json`:
```json
{
  "hooks": {
    "Stop": [{
      "type": "command",
      "command": "python3 ~/.claude/hooks/speak_hook.py",
      "timeout": 15
    }]
  }
}
```

## Running

### Start Daemon
```bash
# Production
python3 tts_daemon.py

# Debug mode (verbose logging)
python3 tts_daemon.py --debug
```

### Test
```bash
echo "Hello world" | nc -U ~/.claude/tts.sock
```

### Logs
```bash
tail -f ~/.claude/tts_daemon.log
```

## Configuration

### Modes
- **summary** — Brief 1-2 sentence summarization
- **full** — Read text as provided

### Voices
Aoede, Kore, Puck, Charon, Fenrir, Leda, Orus, Zephyr

### Styles
- **neutral** — Natural, clear speech
- **asmr** — Soft, gentle, with pauses
- **energetic** — Enthusiastic, energetic
- **custom** — Define in `custom_styles` field

### Languages
russian, english, german, spanish, french, chinese, japanese

### Custom Styles
Add to `custom_styles` in config:
```json
{
  "custom_styles": {
    "mentor": "Speak like a wise mentor, calm and thoughtful",
    "excited": "Speak with excitement, like sharing great news"
  }
}
```

## Development

### Modifying Daemon
After changes to `tts_daemon.py`, restart daemon:
```bash
pkill -f "python3 tts_daemon.py"
python3 tts_daemon.py
```

### Config Reloading
Configuration is reloaded automatically before each TTS request. Changes to `~/.claude/tts_config.json` take effect immediately without daemon restart.

### Performance Notes
- Latency target: ~100ms from hook trigger to audio playback
- Achieved through persistent WebSocket connection and audio caching
- Each unique text (hash-based) is cached in `~/.claude/tts_cache/`

## Files

- `tts_daemon.py` — Main daemon (Python 3.10+)
- `speak_hook.py` — Claude Code hook integration
- `tts_config.example.json` — Configuration template
- `speak.py` — Standalone CLI tool for testing
