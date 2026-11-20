import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from django.conf import settings
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404

from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied, APIException
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .models import (
    Course,
    Enrollment,
    CourseInstructor,
    LiveBatch,
    LiveBatchStudent,
    HistoricalRecordingSession,
    HistoricalRecordingFile,
)

logger = logging.getLogger(__name__)

RECORDING_BUCKET = "natya-wise-recordings"
RECORDING_PREFIX = "wise-recordings/"
URL_EXPIRY_SECONDS = 900


class HistoricalRecordingPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100


class HistoricalRecordingSessionListView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = HistoricalRecordingPagination

    def get_queryset(self):
        user = self.request.user
        course = get_object_or_404(Course, pk=self.kwargs["course_id"])

        if not (user.is_staff or user.is_superuser):
            is_student = (
                Enrollment.objects.filter(user=user, course=course).exists()
                or LiveBatchStudent.objects.filter(
                    student=user, batch__course=course
                ).exists()
            )

            is_assigned_instructor = (
                CourseInstructor.objects.filter(
                    user=user, course=course
                ).exists()
                or LiveBatch.objects.filter(course=course)
                .filter(Q(instructor=user) | Q(mentor=user))
                .exists()
            )

            if not (is_student or is_assigned_instructor):
                raise PermissionDenied(
                    "You do not have access to recordings for this course."
                )

        verified_files = HistoricalRecordingFile.objects.filter(
            is_verified=True,
            s3_bucket=RECORDING_BUCKET,
            s3_key__startswith=RECORDING_PREFIX,
        ).order_by("file_type", "id")

        return (
            HistoricalRecordingSession.objects
            .filter(course=course)
            .prefetch_related(
                Prefetch("files", queryset=verified_files)
            )
        )

    @staticmethod
    def get_s3_client():
        kwargs = {
            "region_name": getattr(
                settings, "AWS_S3_REGION_NAME", "ap-south-1"
            ) or "ap-south-1",
            "config": Config(signature_version="s3v4"),
        }

        access_key = getattr(settings, "AWS_ACCESS_KEY_ID", None)
        secret_key = getattr(settings, "AWS_SECRET_ACCESS_KEY", None)

        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key

        return boto3.client("s3", **kwargs)

    @staticmethod
    def valid_file(file_obj):
        key = file_obj.s3_key or ""

        if file_obj.s3_bucket != RECORDING_BUCKET:
            return False

        if not key.startswith(RECORDING_PREFIX) or "\\" in key:
            return False

        if any(part in (".", "..") for part in key.split("/")):
            return False

        lower_key = key.lower()

        if file_obj.file_type == HistoricalRecordingFile.FileType.MP4:
            return lower_key.endswith(".mp4")

        if file_obj.file_type == HistoricalRecordingFile.FileType.VTT:
            return lower_key.endswith(".vtt")

        return False

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)

        try:
            s3_client = self.get_s3_client()
            results = []

            for session in page:
                files = []

                for file_obj in session.files.all():
                    if not self.valid_file(file_obj):
                        continue

                    content_type = (
                        "video/mp4"
                        if file_obj.file_type == "MP4"
                        else "text/vtt"
                    )

                    try:
                        playback_url = s3_client.generate_presigned_url(
                            "get_object",
                            Params={
                                "Bucket": RECORDING_BUCKET,
                                "Key": file_obj.s3_key,
                                "ResponseContentDisposition": "inline",
                                "ResponseContentType": content_type,
                            },
                            ExpiresIn=URL_EXPIRY_SECONDS,
                        )
                    except (ClientError, BotoCoreError):
                        logger.exception(
                            "Historical recording URL generation failed; "
                            "file_id=%s",
                            file_obj.pk,
                        )
                        raise APIException(
                            "Unable to generate recording playback links."
                        )

                    files.append({
                        "id": file_obj.pk,
                        "file_type": file_obj.file_type,
                        "filename": file_obj.filename,
                        "file_size_bytes": file_obj.file_size_bytes,
                        "content_type": content_type,
                        "url": playback_url,
                        "expires_in": URL_EXPIRY_SECONDS,
                    })

                results.append({
                    "id": session.pk,
                    "source_session_id": session.source_session_id,
                    "source_class_id": session.source_class_id,
                    "source_class_name": session.source_class_name,
                    "title": session.session_title,
                    "teacher_name": session.teacher_name,
                    "actual_start": (
                        session.actual_start.isoformat()
                        if session.actual_start else None
                    ),
                    "actual_end": (
                        session.actual_end.isoformat()
                        if session.actual_end else None
                    ),
                    "duration": session.duration_text,
                    "files": files,
                })

            return self.get_paginated_response(results)

        except APIException:
            raise
        except Exception:
            logger.exception(
                "Historical recording list failed; course_id=%s",
                self.kwargs.get("course_id"),
            )
            raise APIException(
                "Unable to load historical recordings."
            )

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)
