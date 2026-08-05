import os
import sys
import django

sys.path.append('c:/Users/91811/OneDrive/Desktop/NEW-LMS/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from courses.models import VideoLesson
from courses.services.ai_translator import generate_dubbed_audio

# Let's create a dummy video lesson or use an existing one to test
lesson = VideoLesson.objects.first()
if not lesson:
    print("No lessons found.")
else:
    print(f"Testing with lesson: {lesson.title} (ID: {lesson.id})")
    # Just run it for one language to test output
    generate_dubbed_audio(lesson.id)
