import os
import requests
import json
from django.core.files.base import ContentFile
from django.conf import settings
from courses.models import VideoLesson, TranslatedAudio

# The API key was provided by the user
GOOGLE_API_KEY = getattr(settings, 'GOOGLE_API_KEY', 'AIzaSyAn8Hh8FMsv7BPjzfVfTSrgCvEzTG1a3ug')

LANGUAGE_MAP = {
    'hi': {'translate': 'hi', 'tts': 'hi-IN', 'voice': 'hi-IN-Wavenet-A'},
    'ta': {'translate': 'ta', 'tts': 'ta-IN', 'voice': 'ta-IN-Wavenet-A'},
    'ml': {'translate': 'ml', 'tts': 'ml-IN', 'voice': 'ml-IN-Wavenet-A'},
    'te': {'translate': 'te', 'tts': 'te-IN', 'voice': 'te-IN-Wavenet-A'},
}

def translate_text(text, target_lang):
    """Translate text using Google Cloud Translation API (REST)"""
    url = f"https://translation.googleapis.com/language/translate/v2?key={GOOGLE_API_KEY}"
    data = {
        "q": text,
        "source": "en",
        "target": target_lang,
        "format": "text"
    }
    response = requests.post(url, json=data)
    if response.status_code == 200:
        return response.json()['data']['translations'][0]['translatedText']
    else:
        print(f"Translation Error: {response.text}")
        return None

def text_to_speech(text, lang_code, voice_name):
    """Generate speech using Google Cloud Text-to-Speech API (REST)"""
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_API_KEY}"
    data = {
        "input": {"text": text},
        "voice": {"languageCode": lang_code, "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3"}
    }
    response = requests.post(url, json=data)
    if response.status_code == 200:
        import base64
        audio_content = response.json()['audioContent']
        return base64.b64decode(audio_content)
    else:
        print(f"TTS Error: {response.text}")
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
            print(f"Error parsing block: {e}")
            continue
    return blocks

def generate_dubbed_audio(lesson_id):
    """
    Main function to auto-transcribe, translate, and generate timed audio.
    """
    try:
        lesson = VideoLesson.objects.get(id=lesson_id)
    except VideoLesson.DoesNotExist:
        return

    openai_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not openai_key:
        print("OPENAI_API_KEY is not configured.")
        return

    if not lesson.video_file:
        print(f"No video file found for lesson {lesson_id}")
        return

    source_url = lesson.video_file.url
    if source_url.startswith('/'):
        source_url = "http://localhost:8000" + source_url

    import subprocess
    import tempfile
    import os
    from pydub import AudioSegment
    import io
    from openai import OpenAI

    target_languages = ['hi', 'ta', 'ml']
    
    # Extract audio using ffmpeg
    print(f"Extracting audio from {source_url}...")
    temp_audio_fd, temp_audio_path = tempfile.mkstemp(suffix=".mp3")
    os.close(temp_audio_fd)
    
    try:
        subprocess.run(["ffmpeg", "-i", source_url, "-vn", "-acodec", "libmp3lame", "-q:a", "2", "-y", temp_audio_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Transcribe with Whisper
        print("Transcribing with Whisper...")
        client = OpenAI(api_key=openai_key)
        with open(temp_audio_path, "rb") as audio_file:
            srt_text = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file, 
                response_format="srt"
            )
            
        blocks = parse_srt(srt_text)
        print(f"Whisper found {len(blocks)} speech blocks.")
        
        # Save transcript for reference
        if not lesson.transcript and blocks:
            lesson.transcript = "\n".join([b['text'] for b in blocks])
            lesson.save()

        # Load original audio to get total duration
        original_audio = AudioSegment.from_file(temp_audio_path)
        duration_ms = len(original_audio)

        for lang in target_languages:
            print(f"Processing {lang} auto-dubbing...")
            audio_obj, _ = TranslatedAudio.objects.get_or_create(
                lesson=lesson,
                language_code=lang,
                defaults={'status': 'processing'}
            )
            audio_obj.status = 'processing'
            audio_obj.save()

            config = LANGUAGE_MAP.get(lang)
            if not config:
                continue

            try:
                # Create a silent canvas
                canvas = AudioSegment.silent(duration=duration_ms)
                
                from pydub.effects import speedup
                
                for i, block in enumerate(blocks):
                    if not block['text']: continue
                    
                    translated_text = translate_text(block['text'], config['translate'])
                    if not translated_text: continue
                    
                    audio_bytes = text_to_speech(translated_text, config['tts'], config['voice'])
                    if not audio_bytes: continue
                    
                    # Convert to AudioSegment
                    chunk = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
                    
                    # Calculate available time window
                    # If this is the last block, window is until end of video
                    # Otherwise, window is until the start of the next block
                    if i + 1 < len(blocks):
                        available_window = blocks[i+1]['start'] - block['start']
                    else:
                        available_window = duration_ms - block['start']
                        
                    # If the TTS is longer than the available window, it will overlap with the next person!
                    # So we speed it up so it fits exactly in the window.
                    if len(chunk) > available_window and available_window > 200:
                        speed_ratio = len(chunk) / available_window
                        # Cap speed up at 1.5x so it doesn't sound entirely like a chipmunk
                        if speed_ratio > 1.5:
                            speed_ratio = 1.5
                        
                        # Speed it up without altering pitch too drastically
                        try:
                            chunk = speedup(chunk, playback_speed=speed_ratio, chunk_size=50, crossfade=25)
                        except:
                            pass # If speedup fails, just use original
                    
                    # Overlay chunk at exact timestamp
                    canvas = canvas.overlay(chunk, position=block['start'])
                
                # Export final stitched audio
                output_io = io.BytesIO()
                canvas.export(output_io, format="mp3", bitrate="128k")
                output_bytes = output_io.getvalue()
                
                filename = f"lesson_{lesson_id}_{lang}_timed.mp3"
                audio_obj.audio_file.save(filename, ContentFile(output_bytes), save=False)
                audio_obj.status = 'completed'
                audio_obj.save()
                print(f"Successfully generated TIMED {lang} audio for lesson {lesson_id}!")
                
            except Exception as e:
                print(f"Error dubbing {lang}: {e}")
                audio_obj.status = 'failed'
                audio_obj.save()

    finally:
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
