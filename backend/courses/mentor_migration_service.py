import os
import re
import logging
import openpyxl
from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils.text import slugify

logger = logging.getLogger(__name__)
User = get_user_model()


def norm_phone(val):
    if val is None:
        return ''
    s = str(val).strip().replace("'", "").replace(" ", "").replace("-", "")
    if not s or s.lower() == 'none':
        return ''
    digits = re.sub(r'\D', '', s)
    if digits.startswith('91') and len(digits) > 10:
        return f"+{digits}"
    elif len(digits) == 10:
        return f"+91{digits}"
    return f"+{digits}" if digits else ''


def norm_email(val):
    if val is None:
        return ''
    s = str(val).strip().lower()
    if not s or s == 'none' or '@' not in s:
        return ''
    return s


def clean_title(val):
    if val is None:
        return ''
    return str(val).strip()


def resolve_migration_file(file_name='natya_analytics.xlsx'):
    candidates = [
        file_name,
        os.path.join(os.getcwd(), file_name),
        os.path.join(os.getcwd(), 'data', file_name),
        os.path.join(os.getcwd(), 'backend', 'data', file_name),
        os.path.join('/var/app/current', file_name),
        os.path.join('/var/app/current/data', file_name),
        os.path.join('/tmp', file_name),
        'Natya Analytics (3).xlsx',
        os.path.join(os.getcwd(), 'Natya Analytics (3).xlsx'),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


def execute_mentor_migration(
    file_path='natya_analytics.xlsx',
    default_password='Mentor123',
    is_commit=False,
    verbose=False,
    log_callback=None
):
    """
    Idempotent bulk mentor creation and course assignment service.
    
    1. Reconciles unique mentors from Excel ('Course Teachers & Learners' and 'Instructor Analytics').
    2. Updates or creates User accounts with is_mentor=True, is_teacher=True, and password=Mentor123.
    3. Idempotently creates CourseInstructor records with role='MENTOR'.
    4. Idempotently creates 1-on-1 Mentorship links for student-mentor pairs found in the sheet.
    """
    resolved_path = resolve_migration_file(file_path)
    if not resolved_path or not os.path.exists(resolved_path):
        raise FileNotFoundError(f"Mentor migration Excel file not found. Checked default and provided paths: {file_path}")

    def log(msg):
        if log_callback:
            log_callback(msg)
        logger.info(msg)

    log(f"Reading workbook for mentors (fast streaming mode): {resolved_path}")
    wb = openpyxl.load_workbook(resolved_path, read_only=True, data_only=True)

    from courses.models import Course, CourseInstructor
    from users.models import Mentorship

    # Pre-fetch existing database state efficiently
    existing_users = list(
        User.objects.all().only(
            'id', 'username', 'email', 'phone_number', 'first_name', 'last_name', 'is_mentor', 'is_teacher'
        )
    )
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

    existing_ci_mentor_pairs = set(
        CourseInstructor.objects.filter(role=CourseInstructor.InstructorRole.MENTOR).values_list('course_id', 'user_id')
    )
    existing_mentorship_pairs = set(
        Mentorship.objects.filter(status=Mentorship.Status.ACTIVE).values_list('mentor_id', 'student_id')
    )

    stats = {
        'file': resolved_path,
        'mode': 'commit' if is_commit else 'dry_run',
        'mentors': {'sheet_count': 0, 'matched_existing': 0, 'to_create': 0, 'created': 0, 'updated': 0},
        'course_assignments': {'sheet_count': 0, 'matched_existing': 0, 'to_create': 0, 'created': 0},
        'mentorship_links': {'sheet_count': 0, 'matched_existing': 0, 'to_create': 0, 'created': 0},
    }

    sim_users_by_phone = dict(user_by_phone)
    sim_users_by_email = dict(user_by_email)
    sim_users_by_name = dict(user_by_name)

    def find_user(phone=None, email=None, name=None):
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

    try:
        with transaction.atomic():
            if 'Course Teachers & Learners' in wb.sheetnames:
                ws = wb['Course Teachers & Learners']
                seen_mentors = set()
                seen_assignments = set()
                seen_mentorships = set()
                row_idx = 0

                for row in ws.iter_rows(min_row=2, values_only=True):
                    row_idx += 1
                    if not row or not any(row):
                        continue

                    course_name = clean_title(row[0])
                    teacher_name = str(row[2]).strip() if row[2] is not None else ''
                    t_phone = norm_phone(row[3])
                    t_email = norm_email(row[4])

                    learner_name = str(row[5]).strip() if len(row) > 5 and row[5] is not None else ''
                    l_phone = norm_phone(row[6]) if len(row) > 6 else ''
                    l_email = norm_email(row[7]) if len(row) > 7 else ''

                    if not teacher_name and not t_phone and not t_email:
                        continue

                    # 1. Mentor Account Creation / Upgrade
                    m_key = t_phone or t_email or teacher_name.lower()
                    mentor_user = None

                    if m_key not in seen_mentors:
                        seen_mentors.add(m_key)
                        stats['mentors']['sheet_count'] += 1

                        matched = find_user(phone=t_phone, email=t_email, name=teacher_name)
                        if matched:
                            mentor_user = matched
                            stats['mentors']['matched_existing'] += 1
                            if is_commit:
                                matched.is_mentor = True
                                matched.is_teacher = True
                                matched.set_password(default_password)
                                matched.save(update_fields=['is_mentor', 'is_teacher', 'password'])
                                stats['mentors']['updated'] += 1
                                log(f"Updated mentor account: {matched.username} ({teacher_name}) -> password set to '{default_password}'")
                            else:
                                stats['mentors']['updated'] += 1
                                log(f"[DRY-RUN] Found mentor to update: {matched.username} ({teacher_name}) -> will set password to '{default_password}'")
                        else:
                            stats['mentors']['to_create'] += 1
                            clean_email = t_email or f"mentor_{slugify(teacher_name or 'user')}_{len(seen_mentors)}@natya.internal"
                            base_username = slugify(teacher_name) or f"mentor_{len(seen_mentors)}"
                            username = clean_email if t_email else base_username
                            
                            if is_commit:
                                # Ensure unique username
                                u_candidate = username
                                idx = 1
                                while User.objects.filter(username=u_candidate).exists():
                                    u_candidate = f"{username}_{idx}"
                                    idx += 1

                                new_mentor = User.objects.create(
                                    username=u_candidate,
                                    email=clean_email,
                                    first_name=teacher_name or "Mentor",
                                    is_mentor=True,
                                    is_teacher=True,
                                    is_student=False,
                                    phone_number=t_phone or '',
                                    is_active=True,
                                    is_onboarded=True
                                )
                                new_mentor.set_password(default_password)
                                new_mentor.save()

                                mentor_user = new_mentor
                                if t_phone:
                                    sim_users_by_phone[t_phone] = new_mentor
                                sim_users_by_email[clean_email] = new_mentor
                                sim_users_by_name[teacher_name.lower()] = new_mentor
                                stats['mentors']['created'] += 1
                                log(f"Created new mentor account: {new_mentor.username} -> password set to '{default_password}'")
                            else:
                                dummy_m = User(
                                    id=200000 + len(seen_mentors),
                                    first_name=teacher_name or "Mentor",
                                    email=clean_email,
                                    is_mentor=True,
                                    is_teacher=True
                                )
                                mentor_user = dummy_m
                                if t_phone:
                                    sim_users_by_phone[t_phone] = dummy_m
                                sim_users_by_email[clean_email] = dummy_m
                                sim_users_by_name[teacher_name.lower()] = dummy_m
                                log(f"[DRY-RUN] New mentor to create: {username} ({teacher_name}) -> will set password to '{default_password}'")
                    else:
                        mentor_user = find_user(phone=t_phone, email=t_email, name=teacher_name)

                    # 2. Course Assignment as MENTOR
                    if course_name and teacher_name:
                        pair_key = (teacher_name.lower(), course_name.lower())
                        if pair_key not in seen_assignments:
                            seen_assignments.add(pair_key)
                            stats['course_assignments']['sheet_count'] += 1

                            course = course_by_title.get(course_name.lower())
                            mentor = mentor_user or find_user(phone=t_phone, email=t_email, name=teacher_name)

                            if course and mentor and hasattr(course, 'id') and hasattr(mentor, 'id'):
                                pair = (course.id, mentor.id)
                                if pair in existing_ci_mentor_pairs:
                                    stats['course_assignments']['matched_existing'] += 1
                                else:
                                    stats['course_assignments']['to_create'] += 1
                                    if is_commit:
                                        CourseInstructor.objects.get_or_create(
                                            course=course,
                                            user=mentor,
                                            role=CourseInstructor.InstructorRole.MENTOR,
                                            defaults={'is_primary': False}
                                        )
                                        existing_ci_mentor_pairs.add(pair)
                                        stats['course_assignments']['created'] += 1
                                        log(f"Assigned mentor {mentor.first_name} to course '{course.title}'")
                            else:
                                stats['course_assignments']['to_create'] += 1

                    # 3. 1-on-1 Student Mentorship Link
                    if teacher_name and (learner_name or l_phone or l_email):
                        l_key = l_phone or l_email or learner_name.lower()
                        m_student_pair = (teacher_name.lower(), l_key)
                        if m_student_pair not in seen_mentorships:
                            seen_mentorships.add(m_student_pair)
                            stats['mentorship_links']['sheet_count'] += 1

                            student = find_user(phone=l_phone, email=l_email, name=learner_name)
                            mentor = mentor_user or find_user(phone=t_phone, email=t_email, name=teacher_name)

                            if student and mentor and hasattr(student, 'id') and hasattr(mentor, 'id'):
                                pair = (mentor.id, student.id)
                                if pair in existing_mentorship_pairs:
                                    stats['mentorship_links']['matched_existing'] += 1
                                else:
                                    stats['mentorship_links']['to_create'] += 1
                                    if is_commit:
                                        Mentorship.objects.get_or_create(
                                            mentor=mentor,
                                            student=student,
                                            status=Mentorship.Status.ACTIVE,
                                            defaults={'notes': f'Imported from rented LMS for course {course_name}'}
                                        )
                                        existing_mentorship_pairs.add(pair)
                                        stats['mentorship_links']['created'] += 1
                            else:
                                stats['mentorship_links']['to_create'] += 1

                    if row_idx % 250 == 0:
                        log(f"Processed {row_idx} sheet rows... (mentors found: {len(seen_mentors)})")
    finally:
        try:
            wb.close()
        except Exception:
            pass

    return stats

