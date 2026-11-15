from config.patient_otp_authentication import PatientOtpObject
from django.test import SimpleTestCase

from ihr_access import state


class OtpCallerTests(SimpleTestCase):
    """A patient signed in by OTP is authenticated but is not staff: the guard's role checks
    must answer "not restricted" without touching the database or ``pk``."""

    def test_an_otp_patient_is_not_signed_in_staff(self):
        obj = PatientOtpObject()
        obj.phone_number = "+919999999999"
        self.assertTrue(obj.is_authenticated)
        self.assertFalse(state._signed_in(obj))
        self.assertFalse(state.restricted(obj, role_ids={}))
        self.assertFalse(state.restricted_only(obj, role_ids={}))
