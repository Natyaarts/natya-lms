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

    transcript = lesson.transcript
    if not transcript:
        print(f"No transcript found for lesson {lesson_id}")
        return

    import re
    # Remove timestamps like '00:02 - ' or '00:02' to help Google Translate
    transcript = re.sub(r'\d{2}:\d{2}\s*-\s*', '', transcript)
    transcript = re.sub(r'\d{2}:\d{2}', '', transcript)

    target_languages = ['hi', 'ta', 'ml']

    for lang in target_languages:
        print(f"Processing {lang} for lesson {lesson_id}...")
        
        # 1. Check if it already exists to overwrite it
        audio_obj, created = TranslatedAudio.objects.get_or_create(
            lesson=lesson,
            language_code=lang,
            defaults={'status': 'processing'}
        )
        # Force regeneration even if completed
        audio_obj.status = 'processing'
        audio_obj.save()

        # 2. Translate Text
        config = LANGUAGE_MAP.get(lang)
        if not config:
            continue
            
        translated_text = translate_text(transcript, config['translate'])
        if not translated_text:
            audio_obj.status = 'failed'
            audio_obj.save()
            continue

        # 3. Generate Audio
        audio_bytes = text_to_speech(translated_text, config['tts'], config['voice'])
        if not audio_bytes:
            audio_obj.status = 'failed'
            audio_obj.save()
            continue

        # 4. Save Audio File to Model
        filename = f"lesson_{lesson_id}_{lang}.mp3"
        audio_obj.audio_file.save(filename, ContentFile(audio_bytes), save=False)
        audio_obj.status = 'completed'
        audio_obj.save()
        
        print(f"Successfully generated {lang} audio for lesson {lesson_id}!")
