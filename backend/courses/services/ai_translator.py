import os
import requests
import json
import logging
import subprocess
import tempfile
import io
from django.core.files.base import ContentFile
from django.conf import settings
from courses.models import VideoLesson, TranslatedAudio
from pydub import AudioSegment

logger = logging.getLogger(__name__)

LANGUAGE_MAP = {
    'hi': {'translate': 'hi', 'tts': 'hi-IN', 'voice': 'hi-IN-Wavenet-A'},
    'ta': {'translate': 'ta', 'tts': 'ta-IN', 'voice': 'ta-IN-Wavenet-A'},
    'ml': {'translate': 'ml', 'tts': 'ml-IN', 'voice': 'ml-IN-Wavenet-A'},
    'te': {'translate': 'te', 'tts': 'te-IN', 'voice': 'te-IN-Wavenet-A'},
    'fr': {'translate': 'fr', 'tts': 'fr-FR', 'voice': 'fr-FR-Wavenet-A'},
    'de': {'translate': 'de', 'tts': 'de-DE', 'voice': 'de-DE-Wavenet-A'},
}

def translate_text(text, target_lang):
    """Translate text using Google Cloud Translation API (REST)"""
    google_key = getattr(settings, 'GOOGLE_API_KEY', '')
    if not google_key:
        raise ValueError("GOOGLE_API_KEY is not configured in Django settings.")

    url = f"https://translation.googleapis.com/language/translate/v2?key={google_key}"
    data = {
        "q": text,
        "source": "en",
        "target": target_lang,
        "format": "text"
    }
    response = requests.post(url, json=data, timeout=30)
    if response.status_code == 200:
        return response.json()['data']['translations'][0]['translatedText']
    else:
        logger.error(f"Translation Error status {response.status_code}: {response.text}")
        response.raise_for_status()
        return None

def text_to_speech(text, lang_code, voice_name):
    """Generate speech using Google Cloud Text-to-Speech API (REST)"""
    google_key = getattr(settings, 'GOOGLE_API_KEY', '')
    if not google_key:
        raise ValueError("GOOGLE_API_KEY is not configured in Django settings.")

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={google_key}"
    data = {
        "input": {"text": text},
        "voice": {"languageCode": lang_code, "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3"}
    }
    response = requests.post(url, json=data, timeout=30)
    if response.status_code == 200:
        import base64
        audio_content = response.json()['audioContent']
        return base64.b64decode(audio_content)
    else:
        logger.error(f"TTS Error status {response.status_code}: {response.text}")
        response.raise_for_status()
        return None

def parse_srt(srt_text):
    import re
    # Match standard SRT blocks: index, start --> end, text
    pattern = re.compile(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:.*(?:\n|$))+?)(?=\n\d+\n|\Z)', re.MULTILINE)
    blocks = []
    for match in pattern.finditer(srt_text):
        try:
            start_time_str = match.group(2)
            end_time_str = match.group(3)
            text = match.group(4).strip()

            def time_to_ms(time_str):
                h, m, s_ms = time_str.split(':')
                s, ms = s_ms.split(',')
                return int(h)*3600000 + int(m)*60000 + int(s)*1000 + int(ms)

            blocks.append({
                'start': time_to_ms(start_time_str),
                'end': time_to_ms(end_time_str),
                'text': text
            })
        except Exception as e:
            logger.error(f"Error parsing block: {e}")
            continue
    return blocks

def parse_timed_transcript(timed_transcript_text):
    """
    Parses the simple admin-entered timed transcript format:
    Each line is: HH:MM:SS --> Text spoken at that time
    Returns blocks in the same format as parse_srt().
    """
    blocks = []
    lines = timed_transcript_text.strip().splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or '-->' not in line:
            continue
        try:
            time_part, text_part = line.split('-->', 1)
            time_part = time_part.strip()
            text_part = text_part.strip()
            if not text_part:
                continue

            # Parse HH:MM:SS or MM:SS
            parts = time_part.split(':')
            if len(parts) == 3:
                h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            elif len(parts) == 2:
                h, m, s = 0, int(parts[0]), int(parts[1])
            else:
                continue
            start_ms = h * 3600000 + m * 60000 + s * 1000

            # End time = next block's start, filled in later
            blocks.append({'start': start_ms, 'end': None, 'text': text_part})
        except Exception as e:
            logger.error(f"Error parsing timed_transcript line '{line}': {e}")
            continue

    # Fill in end times: each block ends when the next one starts
    for i, block in enumerate(blocks):
        if i + 1 < len(blocks):
            block['end'] = blocks[i + 1]['start']
        else:
            # Last block: give it 5 seconds
            block['end'] = block['start'] + 5000

    return blocks

def generate_dubbed_audio(lesson_id, target_languages=None):
    """
    Main function to auto-transcribe, translate, and generate timed audio.
    If lesson.timed_transcript is set, it uses that directly for perfect sync.
    Otherwise, it falls back to Whisper auto-transcription.
    """
    if target_languages is None:
        target_languages = ['hi', 'ta', 'ml']

    logger.info(f"Starting dubbing for lesson {lesson_id} in languages: {target_languages}")
    try:
        lesson = VideoLesson.objects.get(id=lesson_id)
    except VideoLesson.DoesNotExist:
        logger.error(f"VideoLesson {lesson_id} does not exist.")
        return

    if not lesson.video_file:
        logger.error(f"No video file found for lesson {lesson_id}")
        return

    source_url = lesson.video_file.url
    if source_url.startswith('/'):
        source_url = "http://localhost:8000" + source_url

    with tempfile.TemporaryDirectory() as temp_dir:
        # Extract audio using ffmpeg to get duration
        logger.info(f"Extracting mono, low-bitrate audio from {source_url}...")
        temp_audio_path = os.path.join(temp_dir, "extracted.mp3")

        ffmpeg_timeout = getattr(settings, 'FFMPEG_TIMEOUT_SECONDS', 600)

        try:
            # Downsample to mono 32kbps to shrink file size significantly
            subprocess.run([
                "ffmpeg", "-i", source_url, "-vn",
                "-acodec", "libmp3lame", "-ac", "1", "-b:a", "32k",
                "-y", temp_audio_path
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=ffmpeg_timeout)
        except subprocess.TimeoutExpired as e:
            logger.error(f"FFmpeg extraction timed out after {ffmpeg_timeout}s for lesson {lesson_id}")
            raise e
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg extraction failed for lesson {lesson_id}: {e}")
            raise e

        # ─────────────────────────────────────────────────────────────
        # STEP 1: Get speech blocks (Manual timed transcript OR Whisper)
        # ─────────────────────────────────────────────────────────────
        if lesson.timed_transcript and lesson.timed_transcript.strip():
            logger.info("Using manual timed_transcript for sync...")
            blocks = parse_timed_transcript(lesson.timed_transcript)
        else:
            openai_key = getattr(settings, 'OPENAI_API_KEY', '')
            if not openai_key:
                raise ValueError("OPENAI_API_KEY is not configured in settings, and no timed_transcript was provided.")

            from openai import OpenAI
            client = OpenAI(api_key=openai_key)

            file_size_bytes = os.path.getsize(temp_audio_path)
            MAX_SIZE_BYTES = 24 * 1024 * 1024  # 24MB threshold for Whisper upload

            if file_size_bytes <= MAX_SIZE_BYTES:
                logger.info("Uploading full audio to Whisper...")
                with open(temp_audio_path, "rb") as audio_file:
                    srt_text = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="srt"
                    )
                blocks = parse_srt(srt_text)
            else:
                logger.info("Audio file exceeds Whisper limit. Splitting into chunks...")
                audio = AudioSegment.from_file(temp_audio_path)
                total_duration_ms = len(audio)

                # Split into 15-minute chunks
                chunk_duration_ms = 15 * 60 * 1000
                blocks = []

                for start_ms in range(0, total_duration_ms, chunk_duration_ms):
                    end_ms = min(start_ms + chunk_duration_ms, total_duration_ms)
                    logger.info(f"Transcribing chunk {start_ms}ms to {end_ms}ms...")

                    chunk = audio[start_ms:end_ms]
                    chunk_path = os.path.join(temp_dir, f"whisper_chunk_{start_ms}.mp3")

                    chunk.export(chunk_path, format="mp3", bitrate="32k")
                    with open(chunk_path, "rb") as chunk_file:
                        srt_text = client.audio.transcriptions.create(
                            model="whisper-1",
                            file=chunk_file,
                            response_format="srt"
                        )
                    chunk_blocks = parse_srt(srt_text)

                    # Add chunk offset
                    for cb in chunk_blocks:
                        cb['start'] += start_ms
                        cb['end'] += start_ms
                        blocks.append(cb)

                    # Explicitly free memory
                    del chunk

            # Save transcript for reference
            if not lesson.transcript and blocks:
                lesson.transcript = "\n".join([b['text'] for b in blocks])
                lesson.save()

        if not blocks:
            raise ValueError("No speech blocks found. Cannot generate audio.")

        # Load original audio to get total duration
        original_audio = AudioSegment.from_file(temp_audio_path)
        duration_ms = len(original_audio)
        del original_audio

        # ─────────────────────────────────────────────────────────────
        # STEP 2: Generate dubbed audio for each language
        # ─────────────────────────────────────────────────────────────
        for lang in target_languages:
            logger.info(f"Processing {lang} auto-dubbing...")
            audio_obj, _ = TranslatedAudio.objects.get_or_create(
                lesson=lesson,
                language_code=lang,
                defaults={'status': 'processing'}
            )
            audio_obj.status = 'processing'
            audio_obj.save()

            config = LANGUAGE_MAP.get(lang)
            if not config:
                logger.warning(f"Language configuration not found for: {lang}")
                continue

            try:
                from pydub.effects import speedup

                # Memory-safe sequential processing
                current_position = 0
                current_segment = AudioSegment.empty()
                exported_wav_files = []
                MAX_SEGMENT_MS = 10 * 60 * 1000 # 10 minutes

                def flush_segment():
                    nonlocal current_segment
                    if len(current_segment) > 0:
                        chunk_path = os.path.join(temp_dir, f"{lang}_segment_{len(exported_wav_files)}.wav")
                        current_segment.export(chunk_path, format="wav")
                        exported_wav_files.append(chunk_path)
                        current_segment = AudioSegment.empty()

                for i, block in enumerate(blocks):
                    if not block['text']:
                        continue

                    translated_text = translate_text(block['text'], config['translate'])
                    if not translated_text:
                        continue

                    audio_bytes = text_to_speech(translated_text, config['tts'], config['voice'])
                    if not audio_bytes:
                        continue

                    chunk = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")

                    if i + 1 < len(blocks):
                        available_window = blocks[i + 1]['start'] - block['start']
                    else:
                        available_window = duration_ms - block['start']

                    if available_window <= 0:
                        available_window = 3000

                    if len(chunk) > available_window and available_window > 200:
                        speed_ratio = len(chunk) / available_window
                        speed_ratio = min(speed_ratio, 1.5)
                        try:
                            chunk = speedup(chunk, playback_speed=speed_ratio, chunk_size=50, crossfade=25)
                        except Exception as e:
                            logger.warning(f"Speedup failed for chunk: {e}")

                    if len(chunk) > available_window:
                        chunk = chunk[:available_window]

                    # Calculate gap from current_position to this block's start
                    gap = block['start'] - current_position
                    if gap > 0:
                        current_segment += AudioSegment.silent(duration=gap)
                        current_position += gap

                    current_segment += chunk
                    current_position += len(chunk)

                    if len(current_segment) >= MAX_SEGMENT_MS:
                        flush_segment()

                # End padding
                if current_position < duration_ms:
                    current_segment += AudioSegment.silent(duration=duration_ms - current_position)

                flush_segment()

                # Concatenate all wav files using ffmpeg for memory safety
                concat_file_path = os.path.join(temp_dir, f"{lang}_concat.txt")
                with open(concat_file_path, "w", encoding="utf-8") as f:
                    for wav_file in exported_wav_files:
                        # Use forward slashes for ffmpeg concat file path on windows
                        safe_wav_file = wav_file.replace('\\', '/')
                        f.write(f"file '{safe_wav_file}'\n")

                final_mp3_path = os.path.join(temp_dir, f"lesson_{lesson_id}_{lang}_timed.mp3")
                subprocess.run([
                    "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file_path,
                    "-acodec", "libmp3lame", "-ab", "128k", "-y", final_mp3_path
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                with open(final_mp3_path, "rb") as f:
                    output_bytes = f.read()

                filename = f"lesson_{lesson_id}_{lang}_timed.mp3"
                audio_obj.audio_file.save(filename, ContentFile(output_bytes), save=False)
                audio_obj.status = 'completed'
                audio_obj.save()
                logger.info(f"Successfully generated TIMED {lang} audio for lesson {lesson_id}!")

            except MemoryError as e:
                logger.error(f"MemoryError during {lang} audio generation for lesson {lesson_id}", exc_info=True)
                audio_obj.status = 'failed'
                audio_obj.save()
                raise e
            except Exception as e:
                logger.error(f"Error dubbing {lang}: {e}", exc_info=True)
                audio_obj.status = 'failed'
                audio_obj.save()
                raise e
