"""
Sends ONE test WhatsApp OTP through the real Interakt path, to check whether a
country (e.g. Ireland, +353) can receive it. Without --confirm it only shows
what would be sent.

The test code is random and is NOT stored, so it cannot be used to log in.
Prints Interakt's answer (status and message); never prints the API key.

  python manage.py whatsapp_otp_check --phone "+353 87 123 4567"            # dry run
  python manage.py whatsapp_otp_check --phone "+353 87 123 4567" --confirm  # sends
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send one test WhatsApp OTP via Interakt to a phone number (dry run unless --confirm)."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True)
        parser.add_argument("--confirm", action="store_true", help="Actually send the WhatsApp message.")
        parser.add_argument(
            "--full-number-format", action="store_true",
            help='Send as {"fullPhoneNumber": "353..."} (the form class reminders use) instead of the OTP form.',
        )

    def handle(self, *args, **opts):
        import requests

        from users.phone import interakt_recipient, normalize_phone, phone_region
        from users.views import _generate_otp, _interakt_rejected

        number = normalize_phone(opts["phone"], strict=True)
        if not number:
            raise CommandError("Not a valid phone number for its country.")
        self.stdout.write(f"Number: {number} (region {phone_region(number)})")
        recipient = {"fullPhoneNumber": number.lstrip("+")} if opts["full_number_format"] else interakt_recipient(number)
        self.stdout.write(f"Template: {getattr(settings, 'INTERAKT_TEMPLATE_NAME', '') or '(not set)'}")
        self.stdout.write(f"Recipient fields sent to Interakt: {recipient}")
        if not getattr(settings, "INTERAKT_SECRET_KEY", ""):
            raise CommandError("INTERAKT_SECRET_KEY is not configured here.")
        if not opts["confirm"]:
            self.stdout.write("Dry run: nothing sent. Add --confirm to send one test message.")
            return

        otp = _generate_otp()
        payload = {
            **recipient,
            "type": "Template",
            "template": {
                "name": settings.INTERAKT_TEMPLATE_NAME,
                "languageCode": "en",
                "bodyValues": [otp],
                "buttonValues": {"0": [otp]},
            },
        }
        try:
            res = requests.post(
                "https://api.interakt.ai/v1/public/message/",
                json=payload,
                headers={"Authorization": f"Basic {settings.INTERAKT_SECRET_KEY}", "Content-Type": "application/json"},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise CommandError(f"Could not reach Interakt: {exc.__class__.__name__}")
        rejected, reason = _interakt_rejected(res)
        try:
            body = res.json()
            summary = {k: body.get(k) for k in ("result", "message", "id") if isinstance(body, dict)}
        except ValueError:
            summary = (res.text or "")[:200]
        self.stdout.write(f"Interakt HTTP {res.status_code}: {summary}")
        if rejected:
            self.stdout.write(self.style.ERROR(f"REJECTED by Interakt: {reason}"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Accepted by Interakt. Test code sent: {otp} -- confirm it arrives on WhatsApp "
                "(accepted does not yet mean delivered; check Interakt's message log for delivery status)."
            ))
