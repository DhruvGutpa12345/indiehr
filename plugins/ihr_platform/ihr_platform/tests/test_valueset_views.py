"""A value set's recent views and favourites answer only for a value set this person can open
here (P2-T0a).

CARE 3.1 moved the value set routes from the slug to the external id. ``recent_views``,
``remove_recent_view`` and ``clear_recent_views`` touch only the caller's cache entry for that id
and never look the value set up, and ``favourites`` answers a warm cache entry before it does, so
another hospital's id answered 200 on this one (the isolation suite's cross-id probe).
"""

import uuid

from care.emr.models.valueset import ValueSet
from care.users.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from ihr_platform import route_swaps

HOST = "testserver"


class RecentViews(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username=f"vs-{uuid.uuid4().hex[:8]}", password="x-Unused-1"
        )
        self.api = APIClient(HTTP_HOST=HOST)
        self.api.force_authenticate(self.user)
        self.valueset = ValueSet.objects.create(
            slug=f"ihr-vs-{uuid.uuid4().hex[:8]}",
            name="Probe",
            status="active",
            auth_context="instance",
            compose={"include": [{"system": "http://snomed.info/sct"}]},
        )

    def test_an_id_this_hospital_does_not_have_is_not_found(self):
        missing = uuid.uuid4()
        self.assertEqual(
            self.api.get(f"/api/v1/valueset/{missing}/recent_views/").status_code, 404
        )
        response = self.api.post(
            f"/api/v1/valueset/{missing}/remove_recent_view/",
            {"system": "http://snomed.info/sct", "code": "1"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        response = self.api.post(f"/api/v1/valueset/{missing}/clear_recent_views/")
        self.assertEqual(response.status_code, 404)

    def test_a_warm_favourites_entry_is_not_answered_for_an_id_this_hospital_does_not_have(
        self,
    ):
        from care.emr.api.viewsets.valueset import ValueSetViewSet

        missing = str(uuid.uuid4())
        key = ValueSetViewSet().get_favourites_cache_key(missing, self.user.external_id)
        cache.set(key, [{"code": "x"}])
        self.addCleanup(cache.delete, key)
        response = self.api.get(f"/api/v1/valueset/{missing}/favourites/")
        self.assertEqual(response.status_code, 404, response.content[:300])

    def test_a_value_set_this_person_can_open_still_answers(self):
        response = self.api.get(
            f"/api/v1/valueset/{self.valueset.external_id}/recent_views/"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json(), [])
        response = self.api.post(
            f"/api/v1/valueset/{self.valueset.external_id}/clear_recent_views/"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        response = self.api.get(
            f"/api/v1/valueset/{self.valueset.external_id}/favourites/"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json(), [])

    def test_the_swap_took(self):
        from care.emr.api.viewsets.valueset import ValueSetViewSet

        self.assertEqual(
            route_swaps.swapped[ValueSetViewSet].__name__, "IhrValueSetViewSet"
        )
