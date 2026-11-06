"""Fast (<1s) bulk-assign of the 5 mentors to Music & Dance courses and students,
plus reconciliation of teacher course assignments across matching teacher login accounts.
"""
import os
import re
import django

if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
    django.setup()

import openpyxl
from django.db import transaction
from django.contrib.auth import get_user_model
from courses.models import Course, CourseInstructor, Enrollment
from users.models import Mentorship

User = get_user_model()
XLSX = os.environ.get('XLSX', '/var/app/current/data/natya_analytics.xlsx')
if not os.path.exists(XLSX):
    XLSX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'natya_analytics.xlsx')


def norm_teacher_key(val):
    if not val:
        return ''
    s = str(val).lower().strip()
    for token in ('kalamandalam ', 'kalakshetra ', 'kalashree ', 'rlv ', ' expert'):
        s = s.replace(token, '')
    return re.sub(r'[^a-z0-9]', '', s)


def norm_phone10(val):
    if not val:
        return ''
    digits = re.sub(r'\D', '', str(val))
    return digits[-10:] if len(digits) >= 10 else ''


def run():
    if not os.path.exists(XLSX):
        print(f"[SKIP] {XLSX} not found.")
        return

    # 1. Fetch or ensure the 5 real mentors exist and have is_mentor=True, is_teacher=True
    mentor_specs = [
        ('+919074704288', 'ATHULYA MUSIC MENTOR', 'athulya_mentor'),
        ('+918075712014', 'ANJALI', 'anjali_mentor'),
        ('+919778230233', 'PRIYANKA', 'priyanka_mentor'),
        ('+917012525707', 'RESHMA', 'reshma_mentor'),
        ('+918075298052', 'AGNAS', 'agnas_mentor'),
    ]
    from django.contrib.auth.hashers import make_password
    default_pw_hash = None
    mentors = {}
    real_mentor_ids = set()
    for phone, name, default_uname in mentor_specs:
        p10 = norm_phone10(phone)
        u = User.objects.filter(phone_number__endswith=p10).first() if p10 else None
        if not u:
            u = User.objects.filter(username__iexact=default_uname).first()
        if not u:
            if default_pw_hash is None:
                default_pw_hash = make_password('Mentor123')
            u = User.objects.create(
                username=default_uname,
                first_name=name,
                phone_number=phone,
                email=f"{default_uname}@natyaarts.com",
                password=default_pw_hash,
                is_mentor=True,
                is_teacher=True,
                is_student=False,
                is_active=True,
            )
        elif not u.is_mentor or not u.is_teacher:
            u.is_mentor = True
            u.is_teacher = True
            u.save(update_fields=['is_mentor', 'is_teacher'])
        mentors[phone] = u
        real_mentor_ids.add(u.id)

    athulya = mentors['+919074704288']
    dance_mentors = [
        mentors['+918075712014'],  # ANJALI
        mentors['+919778230233'],  # PRIYANKA
        mentors['+917012525707'],  # RESHMA
        mentors['+918075298052'],  # AGNAS
    ]

    # 2. Classify Music vs Dance courses & Teacher -> Courses map from Excel
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb['Course Teachers & Learners']
    music_titles = set()
    dance_titles = set()
    teacher_courses_map = {}  # teacher_norm_key -> {'phone10': ..., 'email': ..., 'titles': set()}

    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[0]:
            continue
        c_str = str(r[0]).strip().lower()
        cat_str = str(r[1]).strip().upper() if len(r) > 1 and r[1] else ''
        if 'MUSIC' in cat_str or 'CM' in cat_str or 'CARNATIC' in cat_str:
            music_titles.add(c_str)
        else:
            dance_titles.add(c_str)

        t_name = str(r[2]).strip() if len(r) > 2 and r[2] else ''
        t_phone = norm_phone10(r[3]) if len(r) > 3 else ''
        t_email = str(r[4]).strip().lower() if len(r) > 4 and r[4] and '@' in str(r[4]) else ''
        t_key = norm_teacher_key(t_name)
        if t_key:
            entry = teacher_courses_map.setdefault(t_key, {
                'raw_name': t_name,
                'full_key': re.sub(r'[^a-z0-9]', '', t_name.lower()),
                'phone10': t_phone,
                'email': t_email,
                'titles': set(),
            })
            entry['titles'].add(c_str)
    wb.close()
    dance_titles -= music_titles

    courses_by_title = {c.title.strip().lower(): c for c in Course.objects.all()}
    music_courses = [courses_by_title[t] for t in music_titles if t in courses_by_title]
    dance_courses = [courses_by_title[t] for t in dance_titles if t in courses_by_title]

    # 3. Bulk link CourseInstructor (role='MENTOR') and Mentorship (status='ACTIVE')
    with transaction.atomic():
        existing_ci = set(
            CourseInstructor.objects.filter(role='MENTOR').values_list('course_id', 'user_id')
        )
        new_ci = []
        for c in music_courses:
            if (c.id, athulya.id) not in existing_ci:
                new_ci.append(CourseInstructor(course=c, user=athulya, role='MENTOR', is_primary=False))
                existing_ci.add((c.id, athulya.id))
        for c in dance_courses:
            for dm in dance_mentors:
                if (c.id, dm.id) not in existing_ci:
                    new_ci.append(CourseInstructor(course=c, user=dm, role='MENTOR', is_primary=False))
                    existing_ci.add((c.id, dm.id))
        if new_ci:
            CourseInstructor.objects.bulk_create(new_ci, ignore_conflicts=True)

        existing_ms = set(
            Mentorship.objects.filter(status='ACTIVE').values_list('mentor_id', 'student_id')
        )
        new_ms = []
        music_user_ids = set(
            Enrollment.objects.filter(course__in=music_courses).values_list('user_id', flat=True)
        )
        for uid in music_user_ids:
            if uid != athulya.id and (athulya.id, uid) not in existing_ms:
                new_ms.append(Mentorship(mentor=athulya, student_id=uid, status='ACTIVE'))
                existing_ms.add((athulya.id, uid))

        dance_user_ids = set(
            Enrollment.objects.filter(course__in=dance_courses).values_list('user_id', flat=True)
        )
        dance_mentor_ids = {dm.id for dm in dance_mentors}
        for uid in dance_user_ids:
            if uid in dance_mentor_ids:
                continue
            for dm in dance_mentors:
                if (dm.id, uid) not in existing_ms:
                    new_ms.append(Mentorship(mentor=dm, student_id=uid, status='ACTIVE'))
                    existing_ms.add((dm.id, uid))
        if new_ms:
            Mentorship.objects.bulk_create(new_ms, ignore_conflicts=True)

        # 4. Sync Teacher CourseInstructor assignments across all matching teacher accounts
        #    (e.g. 'Athiraravi' <-> 'Kalamandalam Athira Ravi') so any teacher login sees their exact courses
        all_teachers = list(User.objects.filter(is_teacher=True).exclude(id__in=real_mentor_ids))
        existing_teacher_ci = set(
            CourseInstructor.objects.filter(role='TEACHER').values_list('course_id', 'user_id')
        )
        new_teacher_ci = []
        matched_teacher_ids = set()

        for t_key, info in teacher_courses_map.items():
            target_courses = [courses_by_title[t] for t in info['titles'] if t in courses_by_title]
            if not target_courses:
                continue
            matched_users = []
            for u in all_teachers:
                u_p10 = norm_phone10(u.phone_number)
                u_email = (u.email or '').strip().lower()
                u_uname_key = norm_teacher_key(u.username.split('@')[0] if '@' in u.username else u.username)
                u_name_key = norm_teacher_key(f"{u.first_name} {u.last_name}")
                if (
                    (info['phone10'] and u_p10 == info['phone10']) or
                    (info['email'] and u_email == info['email']) or
                    (u_uname_key and u_uname_key in (t_key, info['full_key'])) or
                    (u_name_key and u_name_key in (t_key, info['full_key']))
                ):
                    matched_users.append(u)
                    matched_teacher_ids.add(u.id)

            for u in matched_users:
                for c in target_courses:
                    if (c.id, u.id) not in existing_teacher_ci:
                        new_teacher_ci.append(CourseInstructor(course=c, user=u, role='TEACHER', is_primary=False))
                        existing_teacher_ci.add((c.id, u.id))

        if new_teacher_ci:
            CourseInstructor.objects.bulk_create(new_teacher_ci, ignore_conflicts=True)

        # Clean up any regular teachers who were mistakenly marked is_mentor=True by earlier scripts
        if matched_teacher_ids:
            User.objects.filter(id__in=matched_teacher_ids, is_mentor=True).update(is_mentor=False)
            CourseInstructor.objects.filter(user_id__in=matched_teacher_ids, role='MENTOR').delete()

    print(f"\n[OK] ATHULYA MUSIC MENTOR linked to {len(music_courses)} Music courses ({len(music_user_ids)} students)")
    print(f"[OK] 4 DANCE MENTORS linked to {len(dance_courses)} Dance courses each ({len(dance_user_ids)} students)")
    print(f"[OK] Created {len(new_ci)} new course-mentor links, {len(new_ms)} new student-mentor links, and {len(new_teacher_ci)} teacher-course links.")
    print("SUCCESS: All mentors, courses, teachers, and students linked!")


if __name__ == '__main__' or True:
    run()
