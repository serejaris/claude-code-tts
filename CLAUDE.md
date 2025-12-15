# TTS Hook for Claude Code

Text-to-speech integration for Claude Code using Google Gemini Live API with streaming audio playback.

## Architecture

The system consists of three main components:

- **tts_daemon.py** — Persistent WebSocket daemon that maintains connection to Gemini Live API. Handles audio streaming, caching, and low-latency playback via StreamingAudioPlayer.
- **speak_hook.py** — Claude Code Stop Hook that extracts last assistant message from transcript and sends to daemon via Unix socket.
- **Configuration** — `~/.claude/tts_config.json` with mode, voice, style, language, and custom prompts.
- **StreamingAudioPlayer** — Low-latency audio playback using sounddevice for real-time streaming (with graceful fallback to afplay/paplay if unavailable).

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

### 1. Dependencies
Install required packages for low-latency audio streaming:
```bash
pip3 install --break-system-packages sounddevice numpy
```
These enable streaming playback with minimal latency. Without them, the system falls back to batch-mode playback (afplay/paplay) which is slower.

### 2. API Key
Set `GEMINI_API_KEY` environment variable:
```bash
export GEMINI_API_KEY="your-api-key"
```

### 3. Configuration
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

### 4. Install Hook
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
- **asmr** — Soft, gentle, at normal pace without pauses
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
- **First audio**: ~2 seconds from hook trigger (Gemini API response time)
- **Streaming playback**: Begins immediately after receiving first PCM chunks, providing real-time audio as it streams
- **Config changes**: Add ~3-4 seconds overhead for daemon reconnection
- **Caching**: Each unique text (hash-based) is cached in `~/.claude/tts_cache/` for instant playback on repeat requests
- **Audio quality**: 24-bit PCM at 24kHz, streamed in real-time for natural speech progression

## Files

- `tts_daemon.py` — Main daemon (Python 3.10+)
- `speak_hook.py` — Claude Code hook integration
- `tts_config.example.json` — Configuration template
- `speak.py` — Standalone CLI tool for testing
