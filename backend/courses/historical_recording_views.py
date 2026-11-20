import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Max, Min, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from rest_framework import generics, permissions
from rest_framework.exceptions import APIException
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .historical_recording_access import (
    RECORDING_BUCKET,
    RECORDING_PREFIX,
    is_recording_admin,
    mapping_pending_for,
    playable_files,
    visible_sessions,
)
from .models import (
    Course,
    HistoricalRecordingSession,
    HistoricalRecordingFile,
)

logger = logging.getLogger(__name__)

URL_EXPIRY_SECONDS = 900
# Dates in filters are interpreted in the academy's local time.
LOCAL_TZ = ZoneInfo("Asia/Kolkata")


class HistoricalRecordingPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100


class HistoricalRecordingSessionListView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = HistoricalRecordingPagination

    def get_queryset(self):
        course = get_object_or_404(Course, pk=self.kwargs["course_id"])

        # Course enrollment / course instructorship no longer grants access:
        # a course spans many batches and teachers. See historical_recording_access.
        verified_files = playable_files().order_by("file_type", "id")

        return (
            visible_sessions(self.request.user)
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


# ---------------------------------------------------------------------------
# Global historical recordings (Admin -> Live Classes -> History)
# ---------------------------------------------------------------------------

def _local_day_bounds(value, end=False):
    """YYYY-MM-DD in academy local time -> aware datetime (start of day, or start of next day)."""
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    if end:
        day = day + timedelta(days=1)
    return datetime.combine(day, time.min, tzinfo=LOCAL_TZ)


class HistoricalRecordingListView(generics.GenericAPIView):
    """
    GET /api/courses/historical-recordings/

    Query params: q (title / course / teacher / class), course, teacher (exact
    teacher name), date_from, date_to (YYYY-MM-DD, local time), has_video=1,
    page, page_size. Lists session metadata and verified file metadata only;
    playback URLs are issued per file by HistoricalRecordingPlaybackView.
    """
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = HistoricalRecordingPagination

    def get_queryset(self):
        params = self.request.query_params
        qs = visible_sessions(self.request.user)

        q = (params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(session_title__icontains=q)
                | Q(course__title__icontains=q)
                | Q(teacher_name__icontains=q)
                | Q(source_class_name__icontains=q)
            )
        course_id = params.get("course")
        if course_id and str(course_id).isdigit():
            qs = qs.filter(course_id=int(course_id))
        teacher = (params.get("teacher") or "").strip()
        if teacher:
            qs = qs.filter(teacher_name=teacher)
        start = _local_day_bounds(params.get("date_from"))
        if start:
            qs = qs.filter(actual_start__gte=start)
        end = _local_day_bounds(params.get("date_to"), end=True)
        if end:
            qs = qs.filter(actual_start__lt=end)
        if params.get("has_video") in ("1", "true"):
            qs = qs.filter(
                files__in=playable_files().filter(file_type=HistoricalRecordingFile.FileType.MP4)
            ).distinct()

        return qs.prefetch_related(
            Prefetch("files", queryset=playable_files().order_by("file_type", "id"))
        ).order_by("-actual_start", "-id")

    def get(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        results = []
        for session in page:
            files = [
                {
                    "id": f.pk,
                    "file_type": f.file_type,
                    "filename": f.filename,
                    "file_size_bytes": f.file_size_bytes,
                }
                for f in session.files.all()
                if HistoricalRecordingSessionListView.valid_file(f)
            ]
            results.append({
                "id": session.pk,
                "title": session.session_title,
                "course": {"id": session.course_id, "title": session.course.title},
                "class_name": session.source_class_name,
                "teacher_name": session.teacher_name,
                "actual_start": session.actual_start.isoformat() if session.actual_start else None,
                "actual_end": session.actual_end.isoformat() if session.actual_end else None,
                "duration": session.duration_text,
                "files": files,
            })
        response = self.get_paginated_response(results)
        response.data["access"] = {
            "scope": "all" if is_recording_admin(request.user) else "mapped",
            "mapping_pending": mapping_pending_for(request.user),
        }
        return response


class HistoricalRecordingFiltersView(APIView):
    """GET /api/courses/historical-recordings/filters/ -> courses, teachers and date range within the caller's visibility."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = visible_sessions(request.user)
        courses = (
            qs.values("course_id", "course__title")
            .annotate(count=Count("id"))
            .order_by("course__title")
        )
        teachers = (
            qs.exclude(teacher_name="")
            .values("teacher_name")
            .annotate(count=Count("id"))
            .order_by("teacher_name")
        )
        span = qs.aggregate(first=Min("actual_start"), last=Max("actual_start"))
        return Response({
            "total": qs.count(),
            "courses": [{"id": c["course_id"], "title": c["course__title"], "count": c["count"]} for c in courses],
            "teachers": [{"name": t["teacher_name"], "count": t["count"]} for t in teachers],
            "first_recording": span["first"].isoformat() if span["first"] else None,
            "last_recording": span["last"].isoformat() if span["last"] else None,
            "mapping_pending": mapping_pending_for(request.user),
        })


class HistoricalRecordingPlaybackView(APIView):
    """
    GET /api/courses/historical-recordings/files/<file_id>/playback/

    Fresh 15-minute signed URL for one verified file the caller may see. For a
    video, the session's verified subtitle file (if any) is signed alongside it.
    Anything not visible returns 404 so file ids cannot be probed.
    """
    permission_classes = [permissions.IsAuthenticated]

    def _sign(self, client, file_obj):
        content_type = "video/mp4" if file_obj.file_type == HistoricalRecordingFile.FileType.MP4 else "text/vtt"
        return client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": RECORDING_BUCKET,
                "Key": file_obj.s3_key,
                "ResponseContentDisposition": "inline",
                "ResponseContentType": content_type,
            },
            ExpiresIn=URL_EXPIRY_SECONDS,
        ), content_type

    def get(self, request, file_id):
        file_obj = (
            playable_files()
            .select_related("session", "session__course")
            .filter(pk=file_id, session__in=visible_sessions(request.user))
            .first()
        )
        if file_obj is None or not HistoricalRecordingSessionListView.valid_file(file_obj):
            raise Http404

        subtitle = None
        if file_obj.file_type == HistoricalRecordingFile.FileType.MP4:
            subtitle = next(
                (
                    f for f in playable_files()
                    .filter(session_id=file_obj.session_id, file_type=HistoricalRecordingFile.FileType.VTT)
                    .order_by("id")
                    if HistoricalRecordingSessionListView.valid_file(f)
                ),
                None,
            )

        try:
            client = HistoricalRecordingSessionListView.get_s3_client()
            url, content_type = self._sign(client, file_obj)
            subtitle_url = self._sign(client, subtitle)[0] if subtitle else None
        except (ClientError, BotoCoreError):
            logger.exception("Historical recording URL generation failed; file_id=%s", file_obj.pk)
            raise APIException("Unable to generate recording playback link.")

        session = file_obj.session
        return Response({
            "file_id": file_obj.pk,
            "file_type": file_obj.file_type,
            "content_type": content_type,
            "url": url,
            "subtitle_url": subtitle_url,
            "expires_in": URL_EXPIRY_SECONDS,
            "session": {
                "id": session.pk,
                "title": session.session_title,
                "course": session.course.title,
                "teacher_name": session.teacher_name,
                "actual_start": session.actual_start.isoformat() if session.actual_start else None,
            },
        })
