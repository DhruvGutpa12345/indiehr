"""CARE's value set routes, with every cache-only detail action looking the value set up (P2-T0a).

CARE 3.1 moved the value set routes from the slug to the external id. ``recent_views``,
``remove_recent_view`` and ``clear_recent_views`` read or change only the caller's own cache
entry for that id (the key is the hospital's, ``RecentViewsManager._tenant_key``) and never look
the value set up, and ``favourites`` answers a warm cache entry before it does, so any id,
another hospital's too, answered 2xx. Here each first opens the value set as the other detail
actions do (``get_object``: CARE's ``get_filtered_valuesets``), so an id this person cannot open
in this hospital is a 404, and the isolation suite's cross-id probe holds. The other detail
actions (``add_favourite``, ``remove_favourite``, ``clear_favourites``, ``add_recent_view``)
already open it. Swapped in by ``route_swaps.SWAPS``.
"""

from care.emr.api.viewsets.valueset import ValueSetViewSet
from care.emr.fhir.resources.code_concept import MinimalCodeConcept
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action


class IhrValueSetViewSet(ValueSetViewSet):
    @extend_schema(request=MinimalCodeConcept, responses={200: None}, methods=["POST"])
    @action(detail=True, methods=["POST"])
    def remove_recent_view(self, request, *args, **kwargs):
        self.get_object()
        return ValueSetViewSet.remove_recent_view(self, request, *args, **kwargs)

    @action(detail=True, methods=["GET"])
    def recent_views(self, request, *args, **kwargs):
        self.get_object()
        return ValueSetViewSet.recent_views(self, request, *args, **kwargs)

    @action(detail=True, methods=["POST"])
    def clear_recent_views(self, request, *args, **kwargs):
        self.get_object()
        return ValueSetViewSet.clear_recent_views(self, request, *args, **kwargs)

    @action(detail=True, methods=["GET"])
    def favourites(self, request, *args, **kwargs):
        self.get_object()
        return ValueSetViewSet.favourites(self, request, *args, **kwargs)
