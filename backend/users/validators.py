"""
Phase 3.9. File-upload validation for TeacherProfile/MentorProfile
profile_image fields -- previously plain ImageFields with no explicit
type/size limit beyond Pillow's own format sniffing (confirmed via audit:
no FileExtensionValidator, no size cap, anywhere in the backend).

Kept intentionally small and dependency-free: Django's own
FileExtensionValidator already covers extension whitelisting; only the
size cap needs a small custom validator, since Django has no built-in one.
"""
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat

# 5 MB -- generous enough for a real profile photo, small enough that a
# malicious/accidental huge upload can't quietly consume storage/bandwidth.
MAX_PROFILE_IMAGE_SIZE_BYTES = 5 * 1024 * 1024

ALLOWED_PROFILE_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']


def validate_profile_image_size(file):
    """Rejects an uploaded file larger than MAX_PROFILE_IMAGE_SIZE_BYTES.
    Used alongside django.core.validators.FileExtensionValidator (which
    covers the allowed-extension check) on TeacherProfile.profile_image /
    MentorProfile.profile_image."""
    if file.size > MAX_PROFILE_IMAGE_SIZE_BYTES:
        raise ValidationError(
            f"Image file too large ({filesizeformat(file.size)}). "
            f"Maximum size is {filesizeformat(MAX_PROFILE_IMAGE_SIZE_BYTES)}."
        )
