import os
import re
import logging
from decimal import Decimal
from datetime import datetime
from collections import defaultdict

from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils.text import slugify

try:
    import openpyxl
except ImportError:
    openpyxl = None

User = get_user_model()
logger = logging.getLogger(__name__)


def norm_phone(p):
    """Normalize phone numbers to clean international format (+91...)."""
    if not p:
        return None
    p_str = str(p).strip().lstrip("'")
    digits_and_plus = re.sub(r'[^0-9+]', '', p_str)
    if not digits_and_plus:
        return None
    if not digits_and_plus.startswith('+'):
        if len(digits_and_plus) == 10:
            digits_and_plus = '+91' + digits_and_plus
        else:
            digits_and_plus = '+' + digits_and_plus
    return digits_and_plus


def norm_email(e):
    """Normalize email to clean lowercase."""
    if not e:
        return None
    e_str = str(e).strip().lower()
    if '@' not in e_str or '.' not in e_str:
        return None
    return e_str


def clean_title(t):
    """Clean and standardize course/batch title."""
    if not t:
        return ""
    return str(t).strip()


def resolve_migration_file(file_path=None):
    """Find the migration Excel file in known paths."""
    candidates = []
    if file_path:
        candidates.append(file_path)
        if not os.path.isabs(file_path):
            candidates.extend([
                os.path.join(os.getcwd(), file_path),
                os.path.join(os.getcwd(), 'data', file_path),
                os.path.join(os.getcwd(), 'backend', 'data', file_path),
                os.path.join(os.path.dirname(__file__), '..', 'data', file_path),
                os.path.join(os.path.dirname(__file__), '..', '..', file_path),
                os.path.join('/tmp', file_path),
            ])

    default_names = ['natya_analytics.xlsx', 'Natya Analytics (3).xlsx']
    for name in default_names:
        candidates.extend([
            os.path.join(os.getcwd(), 'data', name),
            os.path.join(os.getcwd(), 'backend', 'data', name),
            os.path.join(os.path.dirname(__file__), '..', 'data', name),
            os.path.join(os.path.dirname(__file__), '..', '..', name),
            os.path.join(os.getcwd(), name),
            os.path.join('/tmp', name),
            os.path.join('/var/app/current/data', name),
            os.path.join('/var/app/current', name),
        ])

    for cand in candidates:
        if cand and os.path.exists(cand):
            return os.path.abspath(cand)
    return None


def execute_rented_lms_migration(file_path=None, is_commit=False, verbose=False, log_callback=None):
    """
    Core reconciliation and idempotent migration engine.
    Reads master Natya Analytics sheet and reconciles with DB.
    """
    if openpyxl is None:
        raise ImportError("openpyxl is not installed. Install via: pip install openpyxl>=3.1.2")

    resolved_path = resolve_migration_file(file_path)
    if not resolved_path or not os.path.exists(resolved_path):
        raise FileNotFoundError(f"Migration Excel file not found. Checked default and provided paths: {file_path}")

    def log(msg):
        if log_callback:
            log_callback(msg)
        logger.info(msg)

    log(f"Reading workbook: {resolved_path}")
    wb = openpyxl.load_workbook(resolved_path, data_only=True)

    from courses.models import Course, CourseInstructor, LiveBatch, LiveBatchStudent, Enrollment

    # Pre-fetch existing database state
    existing_users = list(User.objects.all())
    user_by_phone = {}
    user_by_email = {}
    user_by_name = {}
    user_by_id = {}

    for u in existing_users:
        user_by_id[u.id] = u
        if getattr(u, 'phone_number', None):
            np = norm_phone(u.phone_number)
            if np:
                user_by_phone[np] = u
        if u.email:
            ne = norm_email(u.email)
            if ne:
                user_by_email[ne] = u
        name_clean = f"{u.first_name} {u.last_name}".strip().lower()
        if name_clean:
            user_by_name[name_clean] = u

    existing_courses = list(Course.objects.all())
    course_by_title = {clean_title(c.title).lower(): c for c in existing_courses}

    stats = {
        'file': resolved_path,
        'mode': 'commit' if is_commit else 'dry_run',
        'teachers': {'sheet_count': 0, 'matched_existing': 0, 'to_create': 0, 'created': 0},
        'students': {'sheet_count': 0, 'matched_existing': 0, 'to_create': 0, 'created': 0},
        'courses': {'sheet_count': 0, 'matched_existing': 0, 'to_create': 0, 'created': 0},
        'teacher_assignments': {'sheet_count': 0, 'matched_existing': 0, 'to_create': 0, 'created': 0},
        'enrollments': {'sheet_count': 0, 'matched_existing': 0, 'to_create': 0, 'created': 0},
        'live_batch_students': {'sheet_count': 0, 'matched_existing': 0, 'to_create': 0, 'created': 0},
    }

    sim_users_by_legacy_id = {}
    sim_users_by_phone = dict(user_by_phone)
    sim_users_by_email = dict(user_by_email)
    sim_users_by_name = dict(user_by_name)
    sim_courses_by_title = dict(course_by_title)

    def find_user(phone=None, email=None, name=None, legacy_id=None):
        if legacy_id and legacy_id in sim_users_by_legacy_id:
            return sim_users_by_legacy_id[legacy_id]
        if phone:
            np = norm_phone(phone)
            if np and np in sim_users_by_phone:
                return sim_users_by_phone[np]
        if email:
            ne = norm_email(email)
            if ne and ne in sim_users_by_email:
                return sim_users_by_email[ne]
        if name:
            nc = name.strip().lower()
            if nc and nc in sim_users_by_name:
                return sim_users_by_name[nc]
        return None

    atomic_ctx = transaction.atomic()

    with atomic_ctx:
        # =========================================================
        # 1. RECONCILE COURSES (from 'Course Analytics')
        # =========================================================
        if 'Course Analytics' in wb.sheetnames:
            ws_courses = wb['Course Analytics']
            for row in ws_courses.iter_rows(min_row=2, values_only=True):
                if not row or not any(row):
                    continue
                cid = row[0]
                title = clean_title(row[1])
                if not title:
                    continue
                stats['courses']['sheet_count'] += 1

                t_key = title.lower()
                if t_key in sim_courses_by_title:
                    stats['courses']['matched_existing'] += 1
                else:
                    stats['courses']['to_create'] += 1
                    is_live = any(k in title.lower() for k in ['batch', 'live', 'cmj', 'cms', 'g ', 'session', 'class'])
                    course_type = 'live' if is_live else 'recorded'

                    if is_commit:
                        base_slug = slugify(title)[:45] or f"course-{cid}"
                        slug = base_slug
                        idx = 1
                        while Course.objects.filter(slug=slug).exists():
                            slug = f"{base_slug}-{idx}"
                            idx += 1

                        new_course = Course.objects.create(
                            title=title,
                            slug=slug,
                            course_type=course_type,
                            description=f"Imported from master catalog: {title}",
                            is_published=True,
                            price=Decimal('0.00')
                        )
                        sim_courses_by_title[t_key] = new_course
                        stats['courses']['created'] += 1
                    else:
                        dummy_course = Course(id=300000 + int(cid) if str(cid).isdigit() else 777777, title=title, course_type=course_type)
                        sim_courses_by_title[t_key] = dummy_course

        # =========================================================
        # 2. RECONCILE TEACHERS & ASSIGNMENTS (from 'Course Teachers & Learners')
        # =========================================================
        if 'Course Teachers & Learners' in wb.sheetnames:
            ws_tl = wb['Course Teachers & Learners']
            seen_teachers = set()
            seen_assignments = set()
            existing_ci_pairs = set(CourseInstructor.objects.values_list('course_id', 'user_id'))

            for row in ws_tl.iter_rows(min_row=2, values_only=True):
                if not row or not any(row):
                    continue
                course_name = clean_title(row[0])
                teacher_name = str(row[2]).strip() if row[2] is not None else ''
                t_phone = norm_phone(row[3])
                t_email = norm_email(row[4])

                if not teacher_name and not t_phone and not t_email:
                    continue

                t_key = t_phone or t_email or teacher_name.lower()
                if t_key not in seen_teachers:
                    seen_teachers.add(t_key)
                    stats['teachers']['sheet_count'] += 1

                    matched_t = find_user(phone=t_phone, email=t_email, name=teacher_name)
                    if matched_t:
                        stats['teachers']['matched_existing'] += 1
                        if is_commit and not getattr(matched_t, 'is_teacher', False):
                            matched_t.is_teacher = True
                            matched_t.save(update_fields=['is_teacher'])
                    else:
                        stats['teachers']['to_create'] += 1
                        clean_email = t_email or f"teacher_{len(seen_teachers)}@natya.internal"
                        if is_commit:
                            new_teacher = User.objects.create(
                                username=clean_email,
                                email=clean_email,
                                first_name=teacher_name or "Teacher",
                                is_teacher=True,
                                is_student=False,
                                phone_number=t_phone or '',
                                is_active=True
                            )
                            new_teacher.set_unusable_password()
                            new_teacher.save()
                            if t_phone:
                                sim_users_by_phone[t_phone] = new_teacher
                            sim_users_by_email[clean_email] = new_teacher
                            sim_users_by_name[teacher_name.lower()] = new_teacher
                            stats['teachers']['created'] += 1
                        else:
                            dummy_t = User(id=100000 + len(seen_teachers), first_name=teacher_name or "Teacher", email=clean_email, is_teacher=True)
                            if t_phone:
                                sim_users_by_phone[t_phone] = dummy_t
                            sim_users_by_email[clean_email] = dummy_t
                            sim_users_by_name[teacher_name.lower()] = dummy_t

                # Teacher Assignment
                if course_name and teacher_name:
                    pair_key = (teacher_name.lower(), course_name.lower())
                    if pair_key not in seen_assignments:
                        seen_assignments.add(pair_key)
                        stats['teacher_assignments']['sheet_count'] += 1

                        course = sim_courses_by_title.get(course_name.lower())
                        teacher = find_user(phone=t_phone, email=t_email, name=teacher_name)

                        if course and teacher and hasattr(course, 'id') and hasattr(teacher, 'id'):
                            pair = (course.id, teacher.id)
                            if pair in existing_ci_pairs:
                                stats['teacher_assignments']['matched_existing'] += 1
                            else:
                                stats['teacher_assignments']['to_create'] += 1
                                if is_commit:
                                    CourseInstructor.objects.get_or_create(
                                        course=course,
                                        user=teacher,
                                        defaults={'role': CourseInstructor.InstructorRole.TEACHER, 'is_primary': False}
                                    )
                                    existing_ci_pairs.add(pair)
                                    stats['teacher_assignments']['created'] += 1
                        else:
                            stats['teacher_assignments']['to_create'] += 1

        # =========================================================
        # 3. RECONCILE STUDENTS & ENROLLMENTS (from 'Student Courses')
        # =========================================================
        if 'Student Courses' in wb.sheetnames:
            ws_sc = wb['Student Courses']
            seen_students = set()
            existing_enrollment_pairs = set(Enrollment.objects.values_list('course_id', 'user_id'))
            existing_lbs_pairs = set(LiveBatchStudent.objects.values_list('batch__course_id', 'student_id'))

            for row in ws_sc.iter_rows(min_row=2, values_only=True):
                if not row or not any(row):
                    continue
                sid = str(row[0]).strip() if row[0] is not None else ''
                sname = str(row[1]).strip() if row[1] is not None else ''
                semail = norm_email(row[2])
                sphone = norm_phone(row[3])
                cname = clean_title(row[6])

                s_key = sphone or semail or sid
                if not s_key:
                    continue

                if s_key not in seen_students:
                    seen_students.add(s_key)
                    stats['students']['sheet_count'] += 1

                    matched_s = find_user(phone=sphone, email=semail, name=sname, legacy_id=sid)
                    if matched_s:
                        stats['students']['matched_existing'] += 1
                        sim_users_by_legacy_id[sid] = matched_s
                    else:
                        stats['students']['to_create'] += 1
                        clean_email = semail or f"student_{sid}@natya.internal"
                        if is_commit:
                            uname = clean_email
                            if User.objects.filter(username=uname).exists():
                                uname = f"{clean_email}_{sid}"
                            new_student = User.objects.create(
                                username=uname,
                                email=clean_email,
                                first_name=sname or "Student",
                                is_student=True,
                                phone_number=sphone or '',
                                is_active=True
                            )
                            new_student.set_unusable_password()
                            new_student.save()
                            sim_users_by_legacy_id[sid] = new_student
                            if sphone:
                                sim_users_by_phone[sphone] = new_student
                            sim_users_by_email[clean_email] = new_student
                            sim_users_by_name[sname.lower()] = new_student
                            stats['students']['created'] += 1
                        else:
                            dummy_s = User(id=200000 + len(seen_students), first_name=sname or "Student", email=clean_email, is_student=True)
                            sim_users_by_legacy_id[sid] = dummy_s
                            if sphone:
                                sim_users_by_phone[sphone] = dummy_s
                            sim_users_by_email[clean_email] = dummy_s
                            sim_users_by_name[sname.lower()] = dummy_s

                # Reconcile Enrollment
                if cname:
                    stats['enrollments']['sheet_count'] += 1
                    course = sim_courses_by_title.get(cname.lower())
                    student = sim_users_by_legacy_id.get(sid) or find_user(phone=sphone, email=semail, name=sname)

                    if course and student and hasattr(course, 'id') and hasattr(student, 'id'):
                        pair = (course.id, student.id)
                        if pair in existing_enrollment_pairs:
                            stats['enrollments']['matched_existing'] += 1
                        else:
                            stats['enrollments']['to_create'] += 1
                            if is_commit:
                                Enrollment.objects.get_or_create(
                                    course=course,
                                    user=student,
                                    defaults={
                                        'payment_status': 'completed',
                                        'access_granted': True
                                    }
                                )
                                existing_enrollment_pairs.add(pair)
                                stats['enrollments']['created'] += 1
                    else:
                        stats['enrollments']['to_create'] += 1

                    # Reconcile LiveBatchStudent if course is a live batch
                    if course and any(k in cname.lower() for k in ['batch', 'live', 'cmj', 'cms', 'g ', 'session', 'class']):
                        stats['live_batch_students']['sheet_count'] += 1
                        if student and hasattr(course, 'id') and hasattr(student, 'id'):
                            pair = (course.id, student.id)
                            if pair in existing_lbs_pairs:
                                stats['live_batch_students']['matched_existing'] += 1
                            else:
                                stats['live_batch_students']['to_create'] += 1
                                if is_commit:
                                    batch = LiveBatch.objects.filter(course=course).first()
                                    if not batch:
                                        batch = LiveBatch.objects.create(
                                            course=course,
                                            batch_type=LiveBatch.BatchType.GROUP
                                        )
                                    LiveBatchStudent.objects.get_or_create(
                                        batch=batch,
                                        student=student
                                    )
                                    existing_lbs_pairs.add(pair)
                                    stats['live_batch_students']['created'] += 1
                        else:
                            stats['live_batch_students']['to_create'] += 1

        if not is_commit:
            transaction.set_rollback(True)

    return stats
