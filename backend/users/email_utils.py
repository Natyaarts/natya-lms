"""
Phase 3.9. Real OTP email delivery via AWS SES -- replaces the previous
`print(f"*** AWS SES MOCK: ...")` no-op (see users/views.py's git history
before this phase: email OTP was never actually sent to anyone).

Uses boto3 directly (already a direct dependency for S3 -- see
requirements.txt) rather than adding django-ses or django.core.mail's SMTP
backend as a new dependency. Reuses the same AWS_ACCESS_KEY_ID/
AWS_SECRET_ACCESS_KEY already configured for S3 -- the attached IAM
user/role must additionally be granted ses:SendEmail (and the FROM address/
domain must be verified in SES, or SES rejects every send) -- see the
Phase 3.9 report's "production configuration required" section.
"""
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

logger = logging.getLogger('users.email')


class OTPEmailDeliveryError(Exception):
    """Raised when SES genuinely fails to accept the send -- distinct from
    a plain return-False so the caller (SendOTPView) can tell "we tried
    and it failed" apart from "misconfigured, never attempted"."""
    pass


def send_otp_email(identifier, otp):
    """
    Sends the OTP to `identifier` (an email address) via AWS SES.
    Raises OTPEmailDeliveryError on any failure (missing credentials, SES
    rejection, network error) -- deliberately NOT swallowed here, unlike
    finance/notifications' own "never let a side-effect break the main
    flow" pattern, because email delivery IS the main flow for this
    request: if the OTP was never actually sent, the caller must know so
    it can tell the user "OTP sent successfully" only when that's true,
    rather than silently lying the way the previous print()-only mock did.
    """
    if not (settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY):
        raise OTPEmailDeliveryError("AWS credentials are not configured; cannot send OTP email.")

    client = boto3.client(
        'ses',
        region_name=settings.AWS_SES_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
    subject = "Your Natya Arts verification code"
    body_text = f"Your verification code is {otp}. It expires in 5 minutes. If you did not request this, you can ignore this email."

    try:
        client.send_email(
            Source=settings.DEFAULT_FROM_EMAIL,
            Destination={'ToAddresses': [identifier]},
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': {'Text': {'Data': body_text, 'Charset': 'UTF-8'}},
            },
        )
        logger.info("OTP email sent via SES to identifier ending in %s", identifier[-6:])
    except (BotoCoreError, ClientError) as e:
        logger.error("OTP email send via SES failed: %s", e, exc_info=True)
        raise OTPEmailDeliveryError(str(e)) from e
