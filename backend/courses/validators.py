"""
Phase 4.7. Mirrors users/validators.py's exact pattern (FileExtensionValidator
for the whitelist + a plain size-cap validator function) -- kept in the
courses app since assignment submissions are a courses-app concept, the
same way profile-image validation lives in users/validators.py.
"""
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat

ALLOWED_SUBMISSION_FILE_EXTENSIONS = ['pdf', 'doc', 'docx', 'txt', 'jpg', 'jpeg', 'png', 'zip']
MAX_SUBMISSION_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


def validate_submission_file_size(file):
    """Rejects an uploaded assignment submission file larger than
    MAX_SUBMISSION_FILE_SIZE_BYTES. Used alongside
    django.core.validators.FileExtensionValidator (which already covers
    extension whitelisting) -- same two-part pattern as
    users.validators.validate_profile_image_size."""
    if file.size > MAX_SUBMISSION_FILE_SIZE_BYTES:
        raise ValidationError(
            f"File too large ({filesizeformat(file.size)}). "
            f"Maximum size is {filesizeformat(MAX_SUBMISSION_FILE_SIZE_BYTES)}."
        )
