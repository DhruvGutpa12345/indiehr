"""Our own endpoints for the restricted roles (P1-T11b4; design-b.md §7, §8).

    POST   /api/ihr/records-access/          {patient, reason, reference}: a records grant (MRD)
    GET    /api/ihr/records-access/          the caller's own live grants
    DELETE /api/ihr/records-access/<id>/     end one early: its holder, or a hospital-wide admin
    GET    /api/ihr/owner/totals/            the Owner's totals (totals.py)
    GET    /api/ihr/my-access/?facility=<id> the web app's mode for the caller (P1-T11c1)

Staff sign-in only (the default authentication classes); every request is in the audit trail
(a records request with ``purpose: records``). The web-app screens are P1-T11c's (Q-K45).
"""

import uuid

from care.facility.models import Facility
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from ihr_identity.staff_access import hospital_wide_admin
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ihr_access import branches, records, state, totals

TOTALS_SLUG = "ihr_view_hospital_totals"
TOTALS_REFUSED = "Only the hospital's owner sees its totals."
NO_GRANT = "No such records access of yours."
NOT_A_FACILITY = "Not a facility id."


def grant_body(grant, now=None):
    now = now or timezone.now()
    return {
        "id": grant.pk,
        "patient": str(grant.patient.external_id),
        "reason": grant.reason,
        "reference": grant.reference,
        "created_at": grant.created_at.isoformat(),
        "expires_at": grant.expires_at.isoformat(),
        "seconds_left": max(0, int((grant.expires_at - now).total_seconds())),
    }


class RecordsAccessView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        from ihr_access.models import RecordsGrant

        now = timezone.now()
        mine = (
            RecordsGrant.objects.filter(
                user=request.user, ended_at__isnull=True, expires_at__gt=now
            )
            .select_related("patient")
            .order_by("expires_at")
        )
        return Response({"results": [grant_body(g, now) for g in mine]})

    def post(self, request):
        data = request.data if hasattr(request.data, "get") else {}
        try:
            grant = records.create_grant(
                request.user,
                data.get("patient"),
                data.get("reason"),
                data.get("reference") or "",
            )
        except records.GrantRefusedError as refused:
            return Response({"detail": refused.message}, status=refused.status)
        return Response(grant_body(grant), status=201)


class RecordsAccessEndView(APIView):
    permission_classes = (IsAuthenticated,)

    def delete(self, request, pk):
        from ihr_access.models import RecordsGrant

        if pk > 2**63 - 1:
            return Response({"detail": NO_GRANT}, status=404)
        rows = RecordsGrant.objects.filter(pk=pk, ended_at__isnull=True)
        if not hospital_wide_admin(request.user):
            rows = rows.filter(user=request.user)
        grant = rows.first()
        if grant is None:
            return Response({"detail": NO_GRANT}, status=404)
        records.end_grant(grant, "ended")
        return Response(status=204)


class OwnerTotalsView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        user = request.user
        if not branches.holds(user, TOTALS_SLUG):
            return Response({"detail": TOTALS_REFUSED}, status=403)
        params = request.query_params
        try:
            start, end, by = totals.parse_range(params)
        except totals.TotalsError as wrong:
            return Response({wrong.field: wrong.message}, status=400)
        value = params.get("facility")
        if not value:
            return Response({"facility": "Name the facility."}, status=400)
        try:
            facility = Facility.objects.filter(external_id=value).first()
        except (DjangoValidationError, ValueError):  # not an id
            facility = None
        # A facility that is not theirs (or none) answers as one they may not see
        if facility is None or not branches.holds(user, TOTALS_SLUG, facility=facility):
            return Response({"detail": TOTALS_REFUSED}, status=403)
        return Response(totals.compute(facility, start, end, by))


class MyAccessView(APIView):
    """The web app's mode for the signed-in person (P1-T11c1; design-c.md §13.2, Q-K49 A').

    ``GET /api/ihr/my-access/[?facility=<uuid>]`` answers
    ``{"restricted": <bool>, "bills_here": <bool>}``:

    - ``restricted``: the backend's own rule (``state.restricted``, this request's state), the
      one the guard and the swaps act on; a superuser is never restricted;
    - ``bills_here``: holds Cashier or TPA Desk in an organisation of that facility
      (``RequestState.bills_at``, Q-K37a B: the guard then shows them the visit's bed). False
      without ``facility``, for a facility id this hospital does not have (the schema is the
      hospital, so another hospital's facility is simply unknown here and answers as any
      facility where one does not bill: existence is never told), and for a superuser.

    A ``facility`` that is not a UUID is 400. Staff sign-in only (IsAuthenticated, like every
    /api/ihr/ staff route); no patient data, no database write.
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        user = request.user
        value = request.query_params.get("facility")
        facility_pk = None
        if value:
            try:
                key = uuid.UUID(str(value))
            except ValueError:
                return Response({"facility": NOT_A_FACILITY}, status=400)
            facility_pk = (
                Facility.objects.filter(external_id=key)
                .values_list("pk", flat=True)
                .first()
            )
        current = state.for_user(user)
        superuser = bool(getattr(user, "is_superuser", False))
        return Response(
            {
                "restricted": False if superuser else bool(current.restricted),
                "bills_here": False
                if superuser or facility_pk is None
                else bool(current.bills_at(facility_pk)),
            }
        )
