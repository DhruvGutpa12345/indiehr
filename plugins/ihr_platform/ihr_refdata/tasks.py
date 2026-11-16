"""New and restored facilities get the per-facility packs (P1-T15b3; design.md §4 and "After the
attack" item 14, Q-K67 A).

- **On creation:** a Facility saved as created queues, once its transaction commits, one task
  per hospital (deduplicated by ONE Redis key that carries the hospital's schema,
  ``<schema>:ihr:packs:facility-task``). The task clears the key, then applies the per-facility
  packs only, and only in the facilities that lack the current release of one of them (the new
  ones: never a facility that has it, never a hospital-wide pack), under the hospital's lock.
  A busy hospital is tried again 3 times with backoff; then the task gives up with the line
  ``ihr-packs: hospital=<slug> outcome=deferred facility=<external id>`` (the id the list,
  ``ihr_pack_pending --facility`` and the API take) and leaves it to the backstop.
- **The backstop:** beat sends ``ihr_refdata.fill_facilities`` every 15 minutes to each ACTIVE
  hospital (ihr_platform/beat.py). It finds, in one query, the live facilities that lack the
  current release of a per-facility pack (a give-up of the task, a facility restored after it
  was deleted, rows made without signals) or hold an item for a missing link (Q-K68 A: tried
  again automatically; reconciled only once all its targets are there, item 11), and fills
  them the same way. A facility held only for a missing link whose targets are still missing
  is dropped before the lock (review E2), and a pack this build ships older than the version
  applied here (an image roll-back) is left alone until the roll-forward (review T3). One it
  cannot fill now is named by the same line at every run, which the alert
  PackFacilityDeferred (deploy/logs/alerts/packs.yml) reports after two hours; when the pack
  files themselves are refused, the line names ``facility=unknown`` (review Q7).

Both only read the fleet's record of releases (``prepare(record=False)``: the workers run as the
app role, which may not write it): a release not recorded yet is left alone (the start-up
one-shot records it). Neither ever touches a hospital that is not served: held for a release
or a restore, behind this build, suspended or not live (``release.servable``); the Celery gate
(ihr_platform/celery_gate.py) defers a queued task for such a hospital as well.
"""

import logging
import os
import uuid
from functools import partial

from celery import Celery, current_app, shared_task
from django.db import connection, transaction
from django_tenants.utils import get_public_schema_name, schema_context

log = logging.getLogger("ihr.refdata")
QUEUE_KEY = "ihr:packs:facility-task"
QUEUED_SECONDS = (
    900  # outlasts a queued task's wait; a lost task frees the key on its own
)
BACKOFF_SECONDS = (30, 120, 300)  # the 3 tries after a busy hospital, then the give-up
FILL_EVERY_SECONDS = 15 * 60
TASK_ACTOR = "packs:facility-task"
BEAT_ACTOR = "packs:facility-beat"


def _emit(line):
    """The whole fixed line on standard error (the worker's log format would prefix it)."""
    os.write(2, f"{line}\n".encode())


UNKNOWN = "unknown"  # the facility named when the pack files are refused (review Q7)


def deferred_line(slug, external_id):
    """The line the PackFacilityDeferred alert matches (deploy/logs/alerts/packs.yml): the
    facility's external id (review Q8), or ``unknown``."""
    named = UNKNOWN if external_id == UNKNOWN else str(uuid.UUID(str(external_id)))
    return f"ihr-packs: hospital={slug} outcome=deferred facility={named}"


def queue_key(schema):
    """The one dedup key of a hospital: its schema first, as every Redis key must be."""
    return f"{schema}:{QUEUE_KEY}"


def _redis():
    from django_redis import get_redis_connection

    return get_redis_connection("default")


def facility_saved(sender, instance, created=False, raw=False, **kwargs):
    """Facility post_save: a new facility queues the task once its creation commits."""
    if not created or raw:
        return
    schema = getattr(connection, "schema_name", get_public_schema_name())
    if schema == get_public_schema_name():
        return
    transaction.on_commit(partial(queue, schema, instance.pk), robust=True)


def queue(schema, facility_id):
    """Send the task for ``schema`` unless one is queued already (True when sent). A failure
    here is left to the backstop: the facility exists either way."""
    try:
        if not _redis().set(
            queue_key(schema), int(facility_id), nx=True, ex=QUEUED_SECONDS
        ):
            return False
        with schema_context(schema):
            fill_new_facilities.apply_async(args=(int(facility_id), 0))
    except Exception:  # never fails the request that made the facility
        log.exception("ihr-packs: could not queue the new-facility task (backstop)")
        return False
    return True


def fill(schema, *, actor, missing_links=False):
    """Apply the per-facility packs to ``schema``'s facilities that need them (the task and the
    backstop). Returns (outcome, facilities): "none", "held", "unreleased", "done", "busy" or
    "failed", and the facilities it was for (None: not known, the pack files are refused)."""
    from ihr_platform import release as releases_of_code
    from ihr_platform.models import Hospital

    from ihr_refdata import engine, kinds

    with schema_context(get_public_schema_name()):
        hospital = Hospital.objects.filter(schema_name=schema).first()
        # Never a hospital that is not served: held (release, restore), behind, not live
        if hospital is None or not releases_of_code.servable(hospital):
            return "held", []
    try:
        found = engine.verified_releases(cached=True)
    except engine.PackError:
        log.exception(
            "ihr-packs: hospital=%s the pack files are refused", hospital.slug
        )
        return "failed", None
    mine = {
        pack: versions[-1]
        for pack, versions in found.items()
        if kinds.get(versions[-1].kind).SCOPE == "facility"
    }
    if not mine:
        return "none", []
    places = []
    try:
        return _fill(hospital, schema, mine, places, actor, missing_links)
    except Exception:  # review Q7: whatever failed, the facilities it was for are named
        log.exception("ihr-packs: hospital=%s per-facility packs failed", hospital.slug)
        return "failed", places


def _fill(hospital, schema, mine, places, actor, missing_links):
    """``fill`` once the hospital is served and the build has per-facility packs; ``places``
    is filled in as soon as the facilities are known."""
    from ihr_audit import context as audit
    from ihr_platform.provisioning import runner

    from ihr_refdata import engine

    with schema_context(schema):
        places += engine.lagging_facilities(mine, missing_links=missing_links)
        if places:
            # Review T3: a pack older than the one applied here (an image roll-back) is
            # skipped by the apply; it is not asked for until the roll-forward
            ahead = engine.applied_ahead(mine)
            if ahead:
                log.warning(
                    "ihr-packs: hospital=%s %s: this build's release is older than the one "
                    "applied here; its facilities wait for the roll-forward",
                    hospital.slug,
                    ", ".join(sorted(ahead)),
                )
                mine = {p: r for p, r in mine.items() if p not in ahead}
                places[:] = (
                    engine.lagging_facilities(mine, missing_links=missing_links)
                    if mine
                    else []
                )
            # Review E2: a facility held only for a missing link still missing waits
            places[:] = [f for f in places if engine.needs_filling(mine, f)]
    if not places:
        return "none", []
    if not all(engine.recorded(r) for r in mine.values()):
        log.warning(
            "ihr-packs: hospital=%s a per-facility release is not recorded yet: left to "
            "the next start-up",
            hospital.slug,
        )
        return "unreleased", places
    releases = engine.prepare(actor, record=False, cached=True)
    try:
        with (
            audit.job({"job": "packs", "via": actor.split(":", 1)[1]}, actor),
            runner.exclusive(hospital.slug),
        ):
            engine.apply_packs(
                hospital, operator=actor, releases=releases, facilities=places
            )
    except runner.ProvisioningError:
        return "busy", places
    return "done", places


@shared_task(name="ihr_refdata.fill_new_facilities", ignore_result=True)
def fill_new_facilities(facility_id, attempt=0):
    """The task a new facility queues (see the module's text)."""
    schema = connection.schema_name
    if schema == get_public_schema_name():
        return
    if attempt == 0:
        _redis().delete(queue_key(schema))  # a facility made from now on queues again
    outcome, places = fill(schema, actor=TASK_ACTOR)
    if outcome == "busy" and attempt < len(BACKOFF_SECONDS):
        fill_new_facilities.apply_async(
            args=(facility_id, attempt + 1), countdown=BACKOFF_SECONDS[attempt]
        )
        return
    if outcome in ("busy", "failed"):  # left to the backstop, and named
        _give_up(schema, places or _external_ids([facility_id]))


@shared_task(name="ihr_refdata.fill_facilities", ignore_result=True)
def fill_facilities():
    """The 15-minute backstop, per active hospital (beat)."""
    schema = connection.schema_name
    if schema == get_public_schema_name():
        return
    outcome, places = fill(schema, actor=BEAT_ACTOR, missing_links=True)
    if outcome in ("busy", "failed"):
        _give_up(schema, places)


def _external_ids(facility_ids):
    """The external ids of these facilities of the current hospital (gone ones left out)."""
    from care.facility.models import Facility

    return list(
        Facility._base_manager.filter(pk__in=facility_ids).values_list(  # noqa: SLF001
            "external_id", flat=True
        )
    )


def _give_up(schema, places):
    """One deferred line per facility (Facility rows or external ids); ``None``: the pack
    files are refused, so which facilities lag is not known (one ``unknown`` line)."""
    from ihr_platform.models import Hospital

    with schema_context(get_public_schema_name()):
        slug = (
            Hospital.objects.filter(schema_name=schema)
            .values_list("slug", flat=True)
            .first()
        )
    if places is None:
        _emit(deferred_line(slug, UNKNOWN))
        return
    for place in places:
        _emit(deferred_line(slug, getattr(place, "external_id", place)))


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender: Celery, **kwargs):
    sender.add_periodic_task(
        FILL_EVERY_SECONDS, fill_facilities.s(), name="ihr_refdata_fill_facilities"
    )
