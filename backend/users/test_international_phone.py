"""
International phone numbers: E.164 normalization (India +91, Ireland +353, others),
OTP send/verify, account lookup for numbers stored in older formats, duplicate
safety, admin create/edit, onboarding, password login and Interakt rejections.
Interakt itself is replaced by a mock here; real delivery to Ireland must be
checked with `manage.py whatsapp_otp_check --confirm` against the real account.
"""
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from users.models import OTPVerification
from users.phone import normalize_phone, phone_region, same_phone_users

User = get_user_model()
INTERAKT = dict(INTERAKT_SECRET_KEY="test-key", INTERAKT_TEMPLATE_NAME="otp_template", IS_STAGING=False, DEBUG=False)


def interakt_ok():
    r = mock.MagicMock(status_code=201, text='{"result": true}')
    r.json.return_value = {"result": True, "message": "Message created successfully", "id": "m-1"}
    return r


def interakt_rejected(status=400, message="Number is not on WhatsApp"):
    r = mock.MagicMock(status_code=status, text=message)
    r.json.return_value = {"result": False, "message": message}
    return r


class NormalizePhoneTests(SimpleTestCase):
    def test_indian_numbers(self):
        for raw in ("+919876543210", "+91 98765 43210", "+91-98765-43210", "9876543210", "09876543210",
                    "919876543210", "0091 98765 43210", "+0091 9876543210", "(+91) 98765.43210"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), "+919876543210")
                self.assertEqual(normalize_phone(raw, strict=True), "+919876543210")

    def test_irish_numbers(self):
        for raw in ("+353871234567", "+353 87 123 4567", "+353 087 123 4567", "00353871234567", "353871234567",
                    "+353-87-123-4567"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw, strict=True), "+353871234567")
        self.assertEqual(phone_region("+353871234567"), "IE")
        self.assertEqual(phone_region("+919876543210"), "IN")

    def test_other_countries(self):
        self.assertEqual(normalize_phone("+1 415 555 2671", strict=True), "+14155552671")
        self.assertEqual(normalize_phone("+44 7911 123456", strict=True), "+447911123456")
        self.assertEqual(normalize_phone("+971 50 123 4567", strict=True), "+971501234567")

    def test_rejects_garbage(self):
        for raw in ("", None, "abc", "+91 98765 4321O", "12", "+", "98765@43210", "+353 12345",
                    "+1234567890123456789", "९८७६५४३२१०"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw, strict=True), "")

    def test_lenient_keeps_previous_behaviour_for_plausible_numbers(self):
        # Valid-length but unassigned ranges stay usable for OTP login (never lock anyone out).
        self.assertEqual(normalize_phone("+91 2012345678"), "+912012345678")
        self.assertEqual(normalize_phone("+91 2012345678", strict=True), "")
        self.assertEqual(normalize_phone("+353 99 123 4567"), "+353991234567")
        self.assertEqual(normalize_phone("+353 99 123 4567", strict=True), "")


@override_settings(**INTERAKT)
class OTPLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def send(self, identifier, reply=None):
        with mock.patch("users.views.requests.post", return_value=reply or interakt_ok()) as post:
            res = self.client.post("/api/users/send-otp/", {"identifier": identifier}, format="json")
        return res, post

    def login(self, identifier, typed_again=None):
        res, post = self.send(identifier)
        self.assertEqual(res.status_code, 200, res.content)
        otp = post.call_args.kwargs["json"]["template"]["bodyValues"][0]
        return self.client.post("/api/users/verify-otp/", {"identifier": typed_again or identifier, "otp": otp},
                                format="json")

    def test_indian_otp_unchanged(self):
        res, post = self.send("+919876543210")
        self.assertEqual(res.status_code, 200)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["fullPhoneNumber"], "919876543210")  # exactly as before
        self.assertNotIn("countryCode", sent)
        self.assertTrue(OTPVerification.objects.filter(identifier="+919876543210").exists())

    def test_irish_otp_send_and_login(self):
        res, post = self.send("+353 87 123 4567")
        self.assertEqual(res.status_code, 200)
        sent = post.call_args.kwargs["json"]
        self.assertEqual((sent["countryCode"], sent["phoneNumber"]), ("+353", "871234567"))
        self.assertNotIn("fullPhoneNumber", sent)
        verify = self.login("+353871234567", typed_again="+353 087 123 4567")
        self.assertEqual(verify.status_code, 200, verify.content)
        self.assertTrue(verify.data["created"])
        self.assertEqual(User.objects.get(pk=verify.data["user_id"]).phone_number, "+353871234567")
        again = self.login("00353 87 123 4567")
        self.assertEqual((again.status_code, again.data["user_id"], again.data["created"]),
                         (200, verify.data["user_id"], False))

    def test_legacy_formatted_number_logs_into_existing_account_without_changing_it(self):
        legacy = User.objects.create_user(username="legacy_abc", phone_number="+91 98765 43210", password="x")
        res = self.login("+919876543210")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.data["user_id"], res.data["created"]), (legacy.pk, False))
        legacy.refresh_from_db()
        self.assertEqual(legacy.phone_number, "+91 98765 43210")  # stored value untouched
        self.assertEqual(User.objects.count(), 1)

    def test_number_stored_without_country_code_is_never_matched_for_login(self):
        raw = User.objects.create_user(username="raw", phone_number="9876543210", password="x")
        res = self.login("+919876543210")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotEqual(res.data["user_id"], raw.pk)  # ambiguous stored value: not an identity match
        raw.refresh_from_db()
        self.assertEqual(raw.phone_number, "9876543210")

    def test_shared_number_is_a_clear_error_not_a_crash(self):
        User.objects.create_user(username="a", phone_number="+919876543210", password="x")
        User.objects.create_user(username="b", phone_number="+919876543210", password="x")
        res = self.login("+919876543210")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(User.objects.count(), 2)

    def test_shared_number_in_different_formats_is_also_detected(self):
        User.objects.create_user(username="a", phone_number="+353871234567", password="x")
        User.objects.create_user(username="b", phone_number="+353 87 123 4567", password="x")
        self.assertEqual(len(same_phone_users("+353871234567")), 2)

    def test_interakt_rejection_is_reported_and_no_code_is_stored(self):
        res, _ = self.send("+353871234567", reply=interakt_rejected())
        self.assertEqual(res.status_code, 502)
        self.assertIn("WhatsApp", res.data["error"])
        self.assertFalse(OTPVerification.objects.exists())

    def test_interakt_result_false_with_2xx_is_a_rejection(self):
        r = interakt_ok()
        r.json.return_value = {"result": False, "message": "Template not approved"}
        res, _ = self.send("+919876543210", reply=r)
        self.assertEqual(res.status_code, 502)

    def test_invalid_identifier_rejected(self):
        res, post = self.send("hello")
        self.assertEqual(res.status_code, 400)
        post.assert_not_called()

    def test_email_identifier_unaffected(self):
        with mock.patch("users.email_utils.send_otp_email"), \
             override_settings(AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="y"):
            res = self.client.post("/api/users/send-otp/", {"identifier": "Student@Example.com"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(OTPVerification.objects.filter(identifier="student@example.com").exists())


class AdminStudentPhoneTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_p", password="x", is_staff=True, is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def create(self, phone, **extra):
        data = {"username": f"s{User.objects.count()}", "first_name": "S", "is_student": True, "phone_number": phone,
                "password": "Str0ng-Pass-123!"}
        data.update(extra)
        return self.client.post("/api/users/admin-users/", data, format="json")

    def test_create_irish_and_indian_students_stored_as_e164(self):
        ie = self.create("+353 87 123 4567")
        self.assertEqual(ie.status_code, 201, ie.content)
        self.assertEqual(User.objects.get(pk=ie.data["id"]).phone_number, "+353871234567")
        inn = self.create("+91 98765 43210", parent_phone="+353 87 765 4321")
        self.assertEqual(inn.status_code, 201, inn.content)
        u = User.objects.get(pk=inn.data["id"])
        self.assertEqual((u.phone_number, u.parent_phone), ("+919876543210", "+353877654321"))

    def test_duplicate_number_rejected_even_in_another_format(self):
        User.objects.create_user(username="existing", phone_number="+91 98765 43210", password="x")
        res = self.create("+919876543210")
        self.assertEqual(res.status_code, 400)
        self.assertIn("already linked", str(res.data["phone_number"]))
        res = self.create("+353871234567")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(self.create("+353 087 123 4567").status_code, 400)

    def test_invalid_numbers_rejected(self):
        for phone in ("98765", "+353 12345", "+91 98765 4321O"):
            with self.subTest(phone=phone):
                res = self.create(phone)
                self.assertEqual(res.status_code, 400)
                self.assertIn("country code", str(res.data["phone_number"]))

    def test_editing_a_student_keeps_an_untouched_legacy_number(self):
        legacy = User.objects.create_user(username="legacy_x", phone_number="+91 12345", password="x", is_student=True)
        res = self.client.patch(f"/api/users/admin-users/{legacy.pk}/",
                                {"first_name": "New", "phone_number": "+91 12345"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        legacy.refresh_from_db()
        self.assertEqual((legacy.first_name, legacy.phone_number), ("New", "+91 12345"))

    def test_changing_a_number_normalizes_and_checks_duplicates(self):
        other = User.objects.create_user(username="o", phone_number="+353871234567", password="x")
        s = User.objects.create_user(username="s", phone_number="+919876543210", password="x", is_student=True)
        dup = self.client.patch(f"/api/users/admin-users/{s.pk}/", {"phone_number": "+353 87 123 4567"}, format="json")
        self.assertEqual(dup.status_code, 400)
        ok = self.client.patch(f"/api/users/admin-users/{s.pk}/", {"phone_number": "+353 86 000 1111"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        s.refresh_from_db()
        self.assertEqual(s.phone_number, "+353860001111")
        same = self.client.patch(f"/api/users/admin-users/{other.pk}/", {"phone_number": "+353 87 123 4567"},
                                 format="json")
        self.assertEqual(same.status_code, 200)  # its own number, reformatted

    def test_max_length_fits_e164(self):
        self.assertEqual(User._meta.get_field("phone_number").max_length, 16)
        self.assertEqual(User._meta.get_field("parent_phone").max_length, 16)


class OnboardingAndPasswordLoginTests(TestCase):
    def test_onboarding_stores_normalized_number_and_refuses_duplicates(self):
        User.objects.create_user(username="taken", phone_number="+919876543210", password="x")
        u = User.objects.create_user(username="g", email="g@example.test", password="x")
        client = APIClient()
        client.force_authenticate(u)
        dup = client.post("/api/users/save-profile/", {"full_name": "G", "phone_number": "9876543210"}, format="json")
        self.assertEqual(dup.status_code, 400)
        self.assertIn("already linked", dup.data["error"])  # the key the mobile/web screens display
        bad = client.post("/api/users/save-profile/", {"full_name": "G", "phone_number": "+353 12"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = client.post("/api/users/save-profile/", {"full_name": "G", "phone_number": "+353 87 123 4567"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        u.refresh_from_db()
        self.assertEqual(u.phone_number, "+353871234567")

    def test_password_login_with_irish_number_in_any_format(self):
        User.objects.create_user(username="ie_user", phone_number="+353871234567", password="Pass-12345")
        for typed in ("+353 87 123 4567", "00353871234567", "+353 087 123 4567"):
            with self.subTest(typed=typed):
                res = APIClient().post("/api/auth/login/", {"username": typed, "password": "Pass-12345"}, format="json")
                self.assertEqual(res.status_code, 200, res.content)


@override_settings(**INTERAKT)
class InteraktTestCommandTests(TestCase):
    def test_dry_run_sends_nothing(self):
        out = StringIO()
        with mock.patch("requests.post") as post:
            call_command("whatsapp_otp_check", "--phone", "+353 87 123 4567", stdout=out)
        post.assert_not_called()
        self.assertIn("+353871234567 (region IE)", out.getvalue())
        self.assertIn("Dry run", out.getvalue())
        self.assertNotIn("test-key", out.getvalue())

    def test_dry_run_shows_both_recipient_formats(self):
        out = StringIO()
        call_command("whatsapp_otp_check", "--phone", "+353871234567", stdout=out)
        self.assertIn("'countryCode': '+353', 'phoneNumber': '871234567'", out.getvalue())
        out = StringIO()
        call_command("whatsapp_otp_check", "--phone", "+353871234567", "--full-number-format", stdout=out)
        self.assertIn("'fullPhoneNumber': '353871234567'", out.getvalue())

    def test_confirmed_send_reports_rejection(self):
        out = StringIO()
        with mock.patch("requests.post", return_value=interakt_rejected(message="Country not enabled")):
            call_command("whatsapp_otp_check", "--phone", "+353871234567", "--confirm", stdout=out)
        self.assertIn("REJECTED by Interakt: Country not enabled", out.getvalue())
        self.assertFalse(OTPVerification.objects.exists())
