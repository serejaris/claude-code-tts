#!/usr/bin/env python3
"""
TTS Daemon for Claude Code
Maintains persistent WebSocket connection to Gemini Live API for minimal latency TTS.

Usage:
    python3 tts_daemon.py [--debug]

Test:
    echo "Hello world" | nc -U ~/.claude/tts.sock
"""

import asyncio
import hashlib
import json
import logging
import os
import signal
import socket
import struct
import sys
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

# Optional streaming audio support
try:
    import sounddevice as sd
    import numpy as np
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

# Paths
CLAUDE_DIR = Path.home() / ".claude"
SOCKET_PATH = CLAUDE_DIR / "tts.sock"
PID_FILE = CLAUDE_DIR / "tts_daemon.pid"
CACHE_DIR = CLAUDE_DIR / "tts_cache"
LOG_FILE = CLAUDE_DIR / "tts_daemon.log"
CONFIG_PATH = CLAUDE_DIR / "tts_config.json"

# Gemini Config
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

# Presets
MODES = {
    "summary": "Summarize briefly in 1-2 sentences, keep only the main point",
    "full": "Read the text as provided, naturally"
}

STYLES = {
    "asmr": "Speak softly, gently, with calm pauses, like ASMR",
    "neutral": "Speak naturally and clearly",
    "energetic": "Speak with energy and enthusiasm"
}

LANGUAGES = {
    "russian": "Speak in Russian",
    "english": "Speak in English",
    "german": "Speak in German",
    "spanish": "Speak in Spanish",
    "french": "Speak in French",
    "chinese": "Speak in Chinese",
    "japanese": "Speak in Japanese"
}

VOICES = ["Aoede", "Kore", "Puck", "Charon", "Fenrir", "Leda", "Orus", "Zephyr"]

DEFAULT_CONFIG = {
    "mode": "summary",
    "voice": "Aoede",
    "style": "asmr",
    "language": "russian",
    "max_chars": 1000,
    "custom_styles": {}
}


def load_config() -> dict:
    """Load config from file, fallback to defaults."""
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text())
            # Merge with defaults for missing keys
            return {**DEFAULT_CONFIG, **config}
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load config: {e}, using defaults")
    return DEFAULT_CONFIG.copy()


def build_instruction(config: dict) -> str:
    """Build system_instruction from config."""
    parts = []

    # Mode
    mode = config.get("mode", "summary")
    if mode in MODES:
        parts.append(MODES[mode])

    # Style (preset or custom)
    style = config.get("style", "asmr")
    custom_styles = config.get("custom_styles", {})
    if style in STYLES:
        parts.append(STYLES[style])
    elif style in custom_styles:
        parts.append(custom_styles[style])

    # Language
    lang = config.get("language", "russian")
    if lang in LANGUAGES:
        parts.append(LANGUAGES[lang])

    return ". ".join(parts) + "."


class StreamingAudioPlayer:
    """Low-latency audio player using sounddevice with queue-based streaming."""

    def __init__(self, sample_rate: int = 24000, channels: int = 1, pre_buffer_chunks: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.pre_buffer_chunks = pre_buffer_chunks
        self.queue: Queue = Queue()
        self.stream = None
        self.started = False
        self.finished = False
        self.chunks_received = 0
        self.lock = threading.Lock()
        self.total_bytes_fed = 0

    def _audio_callback(self, outdata, frames, time_info, status):
        """Callback called by sounddevice to fill audio buffer."""
        if status:
            logger.warning(f"Audio callback status: {status}")

        bytes_needed = frames * self.channels * 2  # 16-bit = 2 bytes
        data = b''

        while len(data) < bytes_needed:
            try:
                chunk = self.queue.get_nowait()
                data += chunk
            except Empty:
                if self.finished:
                    # No more data coming, pad with silence
                    data += b'\x00' * (bytes_needed - len(data))
                    break
                else:
                    # Underrun - pad with silence and continue
                    data += b'\x00' * (bytes_needed - len(data))
                    break

        # Trim if we got too much
        data = data[:bytes_needed]

        # Convert to numpy array
        audio_array = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        outdata[:] = audio_array.reshape(-1, self.channels)

    def start(self):
        """Start the audio stream."""
        if self.started:
            return
        self.started = True
        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=np.float32,
            callback=self._audio_callback,
            blocksize=1024
        )
        self.stream.start()
        logger.debug("Audio stream started")

    def feed(self, pcm_data: bytes):
        """Feed PCM data to the player."""
        self.queue.put(pcm_data)
        self.chunks_received += 1
        self.total_bytes_fed += len(pcm_data)

        # Start playback after pre-buffer is filled
        if not self.started and self.chunks_received >= self.pre_buffer_chunks:
            self.start()

    def finish(self):
        """Signal that no more data is coming."""
        self.finished = True

        # If we never started (very short audio), start now
        if not self.started and self.chunks_received > 0:
            self.start()

    async def wait_done(self, timeout: float = 30.0):
        """Wait for playback to complete."""
        if not self.stream:
            return

        # Calculate expected duration based on total bytes fed
        duration = self.total_bytes_fed / (self.sample_rate * self.channels * 2)  # 2 bytes per Int16 sample
        logger.debug(f"Expecting {duration:.2f}s of audio ({self.total_bytes_fed} bytes)")

        # Wait for queue to drain
        while not self.queue.empty():
            await asyncio.sleep(0.05)

        # Sleep for the expected duration to let audio finish playing
        await asyncio.sleep(duration)

        self.stream.stop()
        self.stream.close()
        logger.debug("Audio stream closed")


# Audio config
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit

# Setup logging
def setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler() if debug else logging.NullHandler()
        ]
    )

logger = logging.getLogger(__name__)


class TTSDaemon:
    def __init__(self):
        self.running = False
        self.session = None
        self.session_cm = None  # Context manager for session
        self.client = None
        self.reconnect_delay = 1
        self.max_reconnect_delay = 30
        self.current_config = None  # Track config for reconnection

        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def get_cache_path(self, text: str, config: dict) -> Path:
        """Generate cache path based on text and config hash."""
        cache_key = f"{text}:{config.get('voice')}:{config.get('style')}:{config.get('mode')}:{config.get('language')}"
        hash_key = hashlib.md5(cache_key.encode()).hexdigest()
        return CACHE_DIR / f"{hash_key}.wav"

    def save_audio(self, pcm_data: bytes, path: Path):
        """Save PCM data as WAV file."""
        with wave.open(str(path), 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_data)
        logger.debug(f"Saved audio to {path}")

    def play_audio_async(self, audio_path: Path):
        """Play audio file asynchronously."""
        if sys.platform == "darwin":
            cmd = ["afplay", str(audio_path)]
        else:
            # Try common Linux audio players
            for player in [["paplay"], ["aplay", "-q"], ["mpv", "--really-quiet"]]:
                try:
                    subprocess.run([player[0], "--version"], capture_output=True)
                    cmd = player + [str(audio_path)]
                    break
                except FileNotFoundError:
                    continue
            else:
                logger.error("No audio player found")
                return

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        logger.debug(f"Playing {audio_path}")

    async def play_audio_streaming(self, audio_chunks: list[bytes]):
        """Play audio with streaming - start playback as soon as first chunk arrives."""
        if not audio_chunks:
            return

        # Combine all chunks
        pcm_data = b''.join(audio_chunks)

        # Save to temp file and play
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_path = Path(tmp.name)
            self.save_audio(pcm_data, tmp_path)

        self.play_audio_async(tmp_path)

        # Schedule cleanup after playback (estimate duration + buffer)
        duration = len(pcm_data) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)
        await asyncio.sleep(duration + 1)
        try:
            tmp_path.unlink()
        except:
            pass

    async def connect_live_api(self, tts_config: dict):
        """Establish WebSocket connection to Gemini Live API."""
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            logger.error("google-genai not installed. Run: pip install google-genai")
            return False

        if not GEMINI_API_KEY:
            logger.error("GEMINI_API_KEY not set")
            return False

        try:
            self.client = genai.Client(api_key=GEMINI_API_KEY)

            voice = tts_config.get("voice", "Aoede")
            instruction = build_instruction(tts_config)

            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                    )
                ),
                system_instruction=instruction
            )

            # Store current config for cache key comparison
            self.current_config = tts_config.copy()

            # Get the async context manager and enter it manually
            self.session_cm = self.client.aio.live.connect(model=MODEL, config=config)
            self.session = await self.session_cm.__aenter__()
            self.reconnect_delay = 1  # Reset on successful connect
            logger.info(f"Connected to Gemini Live API (voice={voice}, instruction={instruction[:50]}...)")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Live API: {e}")
            return False

    def config_changed(self, new_config: dict) -> bool:
        """Check if config changed requiring reconnection."""
        if not self.current_config:
            return True
        # Check relevant fields that affect the session
        for key in ["voice", "style", "mode", "language"]:
            if self.current_config.get(key) != new_config.get(key):
                return True
        # Check custom_styles if current style is custom
        style = new_config.get("style", "")
        if style not in STYLES:
            old_custom = self.current_config.get("custom_styles", {}).get(style)
            new_custom = new_config.get("custom_styles", {}).get(style)
            if old_custom != new_custom:
                return True
        return False

    async def synthesize_live(self, text: str, tts_config: dict, player: Optional['StreamingAudioPlayer'] = None) -> Optional[bytes]:
        """Synthesize speech using Live API WebSocket with optional streaming playback."""
        from google.genai import types

        # Reconnect if config changed
        if self.config_changed(tts_config):
            if self.session_cm:
                try:
                    await self.session_cm.__aexit__(None, None, None)
                except:
                    pass
                self.session = None
                self.session_cm = None
            logger.info("Config changed, reconnecting...")

        if not self.session:
            if not await self.connect_live_api(tts_config):
                return None

        try:
            # Send text to synthesize (summarization handled by system_instruction)
            await self.session.send_client_content(
                turns=[types.Content(role="user", parts=[types.Part(text=text)])],
                turn_complete=True
            )

            # Collect audio chunks (for cache) while streaming to player
            audio_chunks = []
            async for response in self.session.receive():
                logger.debug(f"API response: {type(response).__name__}, has_server_content={bool(response.server_content)}")

                if response.server_content:
                    if response.server_content.model_turn:
                        parts_count = len(response.server_content.model_turn.parts)
                        logger.debug(f"model_turn: {parts_count} parts")
                        for i, part in enumerate(response.server_content.model_turn.parts):
                            logger.debug(f"Part {i}: has_inline_data={bool(part.inline_data)}, has_text={bool(part.text if hasattr(part, 'text') else False)}")
                            if part.inline_data:
                                logger.debug(f"  inline_data.mime_type={part.inline_data.mime_type if hasattr(part.inline_data, 'mime_type') else 'N/A'}")
                                logger.debug(f"  inline_data.has_data={bool(part.inline_data.data)}")
                            if part.inline_data and part.inline_data.data:
                                chunk = part.inline_data.data
                                logger.debug(f"Audio chunk: {len(chunk)} bytes")
                                audio_chunks.append(chunk)
                                # Stream to player immediately
                                if player:
                                    player.feed(chunk)

                    # Check if turn is complete
                    if response.server_content.turn_complete:
                        logger.debug(f"Turn complete, total chunks: {len(audio_chunks)}")
                        if player:
                            player.finish()
                        break

            if audio_chunks:
                return b''.join(audio_chunks)
            logger.warning(f"No audio chunks received for text: {text[:100]}...")
            return None

        except Exception as e:
            logger.error(f"Live API synthesis error: {e}")
            # Close session and force reconnect
            if self.session_cm:
                try:
                    await self.session_cm.__aexit__(None, None, None)
                except:
                    pass
            self.session = None
            self.session_cm = None
            return None

    async def speak(self, text: str):
        """Synthesize and play text, using cache if available."""
        if not text.strip():
            return

        # Load config on each request
        tts_config = load_config()

        text = text.strip()

        # Apply max_chars limit
        max_chars = tts_config.get("max_chars", 1000)
        if len(text) > max_chars:
            text = text[:max_chars]
            logger.debug(f"Truncated to {max_chars} chars")

        cache_path = self.get_cache_path(text, tts_config)

        # Check cache
        if cache_path.exists():
            logger.debug(f"Cache hit: {text[:50]}...")
            if HAS_SOUNDDEVICE:
                # Stream from cache file
                await self.play_cached_streaming(cache_path)
            else:
                self.play_audio_async(cache_path)
            return

        # Synthesize
        logger.info(f"Synthesizing: {text[:50]}...")
        pcm_data = await self.synthesize_live(text, tts_config)

        if pcm_data:
            # Save to cache first
            self.save_audio(pcm_data, cache_path)

            # Play using direct method
            if HAS_SOUNDDEVICE:
                logger.debug("Starting playback via sounddevice")
                audio_array = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
                duration = len(audio_array) / SAMPLE_RATE
                logger.debug(f"Audio duration: {duration:.2f}s")

                def play_with_wait(audio):
                    sd.play(audio, samplerate=SAMPLE_RATE)
                    sd.wait()
                    time.sleep(0.5)  # buffer to ensure audio hardware finishes

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, play_with_wait, audio_array)
                logger.debug("Playback completed")
            else:
                self.play_audio_async(cache_path)
        else:
            logger.error("Synthesis failed")

    async def play_cached_streaming(self, cache_path: Path):
        """Play cached audio file using direct playback."""
        import wave
        with wave.open(str(cache_path), 'rb') as wf:
            pcm_data = wf.readframes(wf.getnframes())

        audio_array = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
        duration = len(audio_array) / SAMPLE_RATE
        logger.debug(f"Playing cached audio: {duration:.2f}s")

        def play_with_wait(audio):
            sd.play(audio, samplerate=SAMPLE_RATE)
            sd.wait()
            time.sleep(0.5)  # buffer to ensure audio hardware finishes

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, play_with_wait, audio_array)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle incoming socket connection."""
        try:
            data = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            if data:
                text = data.decode('utf-8').strip()
                logger.debug(f"Received: {text[:100]}...")
                await self.speak(text)
        except asyncio.TimeoutError:
            logger.warning("Client read timeout")
        except Exception as e:
            logger.error(f"Client handler error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def start_socket_server(self):
        """Start Unix socket server."""
        # Remove existing socket
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        server = await asyncio.start_unix_server(
            self.handle_client,
            path=str(SOCKET_PATH)
        )

        # Make socket accessible
        os.chmod(SOCKET_PATH, 0o666)
        logger.info(f"Listening on {SOCKET_PATH}")

        return server

    async def maintain_connection(self):
        """Keep WebSocket connection alive with periodic reconnection."""
        while self.running:
            if not self.session:
                tts_config = load_config()
                logger.info("Attempting to connect...")
                if await self.connect_live_api(tts_config):
                    self.reconnect_delay = 1
                else:
                    logger.info(f"Reconnecting in {self.reconnect_delay}s...")
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
            else:
                await asyncio.sleep(5)  # Check connection every 5s

    def write_pid(self):
        """Write PID file."""
        PID_FILE.write_text(str(os.getpid()))
        logger.debug(f"PID {os.getpid()} written to {PID_FILE}")

    def cleanup(self):
        """Clean up resources."""
        logger.info("Cleaning up...")

        if SOCKET_PATH.exists():
            try:
                SOCKET_PATH.unlink()
            except:
                pass

        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except:
                pass

    async def run(self):
        """Main daemon loop."""
        self.running = True
        self.write_pid()

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        try:
            # Start socket server
            server = await self.start_socket_server()

            # Initial connection with config
            tts_config = load_config()
            await self.connect_live_api(tts_config)

            # Start connection maintainer
            maintain_task = asyncio.create_task(self.maintain_connection())

            # Run server
            async with server:
                await server.serve_forever()

        except asyncio.CancelledError:
            pass
        finally:
            self.cleanup()

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self.running = False

        if self.session_cm:
            try:
                await self.session_cm.__aexit__(None, None, None)
            except:
                pass
            self.session = None
            self.session_cm = None

        # Stop event loop
        asyncio.get_event_loop().stop()


def is_daemon_running() -> bool:
    """Check if daemon is already running."""
    if not PID_FILE.exists():
        return False

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def main():
    debug = "--debug" in sys.argv
    setup_logging(debug)

    if is_daemon_running():
        print("TTS daemon is already running")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY environment variable not set")
        sys.exit(1)

    print(f"Starting TTS daemon (socket={SOCKET_PATH})")

    daemon = TTSDaemon()

    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass
    finally:
        daemon.cleanup()


if __name__ == "__main__":
    main()
