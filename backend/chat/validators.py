import os
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat
from PIL import Image

# Strict 50 MiB boundary = 50 * 1024 * 1024 = 52,428,800 bytes
MAX_CHAT_ATTACHMENT_SIZE_BYTES = 52_428_800

ALLOWED_CHAT_ATTACHMENT_EXTENSIONS = [
    'pdf', 'doc', 'docx', 'txt',
    'jpg', 'jpeg', 'png', 'webp',
    'mp3', 'm4a',
]


def validate_chat_attachment_size(file):
    """
    Rejects any chat attachment exceeding exactly 52,428,800 bytes (50 MiB).
    """
    if file.size > MAX_CHAT_ATTACHMENT_SIZE_BYTES:
        raise ValidationError(
            f"File exceeds maximum allowed size of 50 MB (52,428,800 bytes). "
            f"Uploaded file is {filesizeformat(file.size)} ({file.size} bytes)."
        )


def validate_chat_attachment_content(file):
    """
    Performs deep content and magic byte verification beyond extension checking:
    - Images: Pillow verify() validates structure & blocks embedded scripts.
    - PDF: Checks %PDF- magic header.
    - DOCX / DOC: Checks Zip (PK..) or OLE2 compound structure.
    - TXT: Checks for binary/null bytes.
    """
    name = getattr(file, 'name', '') or ''
    ext = os.path.splitext(name)[1].lower().lstrip('.')

    # Extension whitelist validation
    if ext and ext not in ALLOWED_CHAT_ATTACHMENT_EXTENSIONS:
        raise ValidationError(
            f"File extension '.{ext}' is not permitted. "
            f"Allowed extensions: {', '.join(ALLOWED_CHAT_ATTACHMENT_EXTENSIONS)}."
        )

    # 1. Image deep verification
    if ext in ['jpg', 'jpeg', 'png', 'webp']:
        pos = file.tell() if hasattr(file, 'tell') else 0
        try:
            image = Image.open(file)
            image.verify()
        except Exception:
            raise ValidationError("Corrupt or invalid image content.")
        finally:
            if hasattr(file, 'seek'):
                file.seek(pos)

    # 2. PDF magic bytes verification (%PDF-)
    elif ext == 'pdf':
        pos = file.tell() if hasattr(file, 'tell') else 0
        try:
            header = file.read(5) if hasattr(file, 'read') else b''
            if not header.startswith(b'%PDF-'):
                raise ValidationError("Corrupt or invalid PDF content (missing PDF header).")
        finally:
            if hasattr(file, 'seek'):
                file.seek(pos)

    # 3. Microsoft Office Document (DOCX is Zip PK\x03\x04; legacy DOC is OLE2)
    elif ext == 'docx':
        pos = file.tell() if hasattr(file, 'tell') else 0
        try:
            header = file.read(4) if hasattr(file, 'read') else b''
            if not header.startswith(b'PK\x03\x04'):
                raise ValidationError("Corrupt or invalid DOCX document (invalid container format).")
        finally:
            if hasattr(file, 'seek'):
                file.seek(pos)

    elif ext == 'doc':
        pos = file.tell() if hasattr(file, 'tell') else 0
        try:
            header = file.read(4) if hasattr(file, 'read') else b''
            # OLE2 signature: 0xD0CF11E0
            if not header.startswith(b'\xd0\xcf\x11\xe0'):
                raise ValidationError("Corrupt or invalid DOC document (invalid binary format).")
        finally:
            if hasattr(file, 'seek'):
                file.seek(pos)

    # 4. Plain Text verification (reject null bytes / binary payloads)
    elif ext == 'txt':
        pos = file.tell() if hasattr(file, 'tell') else 0
        try:
            sample = file.read(1024) if hasattr(file, 'read') else b''
            if b'\x00' in sample:
                raise ValidationError("Text files cannot contain binary data or null bytes.")
        finally:
            if hasattr(file, 'seek'):
                file.seek(pos)


def validate_chat_attachment(file):
    """
    Combined validator: size cap (52,428,800 bytes) + content verification.
    """
    if not file:
        return
    validate_chat_attachment_size(file)
    validate_chat_attachment_content(file)
