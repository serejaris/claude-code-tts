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
import wave
from pathlib import Path
from typing import Optional

# Paths
CLAUDE_DIR = Path.home() / ".claude"
SOCKET_PATH = CLAUDE_DIR / "tts.sock"
PID_FILE = CLAUDE_DIR / "tts_daemon.pid"
CACHE_DIR = CLAUDE_DIR / "tts_cache"
LOG_FILE = CLAUDE_DIR / "tts_daemon.log"

# Config
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = "gemini-2.5-flash-preview-native-audio-dialog"
VOICE = "Aoede"  # Aoede, Kore, Puck, Charon, Fenrir, Leda, Orus, Zephyr
SYSTEM_INSTRUCTION = "Speak softly and calmly, like a gentle ASMR narrator. Keep responses brief."

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
        self.client = None
        self.reconnect_delay = 1
        self.max_reconnect_delay = 30

        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def get_cache_path(self, text: str) -> Path:
        """Generate cache path based on text hash."""
        hash_key = hashlib.md5(f"{text}:{VOICE}".encode()).hexdigest()
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

    async def connect_live_api(self):
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

            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
                    )
                ),
                system_instruction=SYSTEM_INSTRUCTION
            )

            self.session = await self.client.aio.live.connect(model=MODEL, config=config)
            self.reconnect_delay = 1  # Reset on successful connect
            logger.info(f"Connected to Gemini Live API (model={MODEL}, voice={VOICE})")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Live API: {e}")
            return False

    async def synthesize_live(self, text: str) -> Optional[bytes]:
        """Synthesize speech using Live API WebSocket."""
        from google.genai import types

        if not self.session:
            if not await self.connect_live_api():
                return None

        try:
            # Send text to synthesize
            prompt = f"Say exactly this: {text}"
            await self.session.send_client_content(
                turns=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                turn_complete=True
            )

            # Collect audio chunks
            audio_chunks = []
            async for response in self.session.receive():
                if response.server_content:
                    if response.server_content.model_turn:
                        for part in response.server_content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                audio_chunks.append(part.inline_data.data)

                    # Check if turn is complete
                    if response.server_content.turn_complete:
                        break

            if audio_chunks:
                return b''.join(audio_chunks)
            return None

        except Exception as e:
            logger.error(f"Live API synthesis error: {e}")
            self.session = None  # Force reconnect
            return None

    async def speak(self, text: str):
        """Synthesize and play text, using cache if available."""
        if not text.strip():
            return

        text = text.strip()
        cache_path = self.get_cache_path(text)

        # Check cache
        if cache_path.exists():
            logger.debug(f"Cache hit: {text[:50]}...")
            self.play_audio_async(cache_path)
            return

        # Synthesize
        logger.info(f"Synthesizing: {text[:50]}...")
        pcm_data = await self.synthesize_live(text)

        if pcm_data:
            # Save to cache and play
            self.save_audio(pcm_data, cache_path)
            self.play_audio_async(cache_path)
        else:
            logger.error("Synthesis failed")

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
                logger.info("Attempting to connect...")
                if await self.connect_live_api():
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

            # Initial connection
            await self.connect_live_api()

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

        if self.session:
            try:
                await self.session.close()
            except:
                pass

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
