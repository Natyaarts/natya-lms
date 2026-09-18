"""
Manual/debug script for exercising the dubbing pipeline against a real
lesson from the command line. NOT a Django test -- run directly with
`python manual_dubbing_debug.py`, never picked up by `manage.py test`.

Renamed from test_dubbing.py: the old name matched Django's test*.py
discovery glob, so `manage.py test` (with no app labels, which defaults to
discovering from the current working directory) imported this file and
executed its module-level dubbing job as an import side effect. All setup/
import/execution below is now also gated behind `if __name__ == "__main__"`
as defense in depth, so nothing runs even if this file is ever imported
rather than run directly.
"""
import os
import sys


def main():
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


if __name__ == "__main__":
    main()
