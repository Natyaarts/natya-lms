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

def generate_dubbed_audio(lesson_id):
    """
    Main function to translate transcript and generate audio files.
    Usually run in a background task.
    """
    try:
        lesson = VideoLesson.objects.get(id=lesson_id)
    except VideoLesson.DoesNotExist:
        return

    # ElevenLabs API Key from Settings
    api_key = getattr(settings, 'ELEVENLABS_API_KEY', '')
    if not api_key:
        print("ELEVENLABS_API_KEY is not configured.")
        return

    if not lesson.video_file:
        print(f"No video file found for lesson {lesson_id}")
        return

    # Build the full URL to the video file
    # S3 urls are absolute. Local urls might be relative.
    source_url = lesson.video_file.url
    if source_url.startswith('/'):
        # For local development
        source_url = "http://localhost:8000" + source_url

    target_languages = ['hi', 'ta', 'ml']
    headers = {"xi-api-key": api_key}

    import time

    for lang in target_languages:
        print(f"Processing {lang} for lesson {lesson_id} with ElevenLabs...")
        
        # 1. Check if it already exists to overwrite it
        audio_obj, created = TranslatedAudio.objects.get_or_create(
            lesson=lesson,
            language_code=lang,
            defaults={'status': 'processing'}
        )
        audio_obj.status = 'processing'
        audio_obj.save()

        try:
            # 2. Trigger Dubbing Job
            dub_data = {
                "source_url": (None, source_url),
                "target_lang": (None, lang),
                "source_lang": (None, "en"),
                "num_speakers": (None, "0"),
                "watermark": (None, "false")
            }
            dub_res = requests.post("https://api.elevenlabs.io/v1/dubbing", headers=headers, files=dub_data)
            
            if dub_res.status_code != 200:
                print(f"ElevenLabs Dubbing Error for {lang}: {dub_res.text}")
                audio_obj.status = 'failed'
                audio_obj.save()
                continue
                
            dubbing_id = dub_res.json().get("dubbing_id")
            
            # 3. Poll for Completion
            completed = False
            while not completed:
                time.sleep(10) # Poll every 10 seconds
                status_res = requests.get(f"https://api.elevenlabs.io/v1/dubbing/{dubbing_id}", headers=headers)
                if status_res.status_code != 200:
                    print(f"Error fetching status: {status_res.text}")
                    break
                    
                status_data = status_res.json()
                current_status = status_data.get("status")
                
                if current_status == "dubbed":
                    completed = True
                elif current_status == "failed":
                    print(f"Dubbing failed for {lang}")
                    break
            
            if not completed:
                audio_obj.status = 'failed'
                audio_obj.save()
                continue
                
            # 4. Download Audio
            audio_res = requests.get(f"https://api.elevenlabs.io/v1/dubbing/{dubbing_id}/audio/{lang}", headers=headers)
            if audio_res.status_code != 200:
                print(f"Error downloading audio for {lang}: {audio_res.text}")
                audio_obj.status = 'failed'
                audio_obj.save()
                continue
                
            # Determine extension
            content_type = audio_res.headers.get('Content-Type', '')
            ext = 'mp4' if 'video' in content_type else 'mp3'
                
            filename = f"lesson_{lesson_id}_{lang}.{ext}"
            audio_obj.audio_file.save(filename, ContentFile(audio_res.content), save=False)
            audio_obj.status = 'completed'
            audio_obj.save()
            
            print(f"Successfully generated {lang} audio for lesson {lesson_id}!")
            
        except Exception as e:
            print(f"Exception during ElevenLabs dubbing for {lang}: {str(e)}")
            audio_obj.status = 'failed'
            audio_obj.save()
