"""PatientUser rows that give nothing any more ("After the attack" §3.3; reviews S1 and C5).

    manage.py ihr_patient_user_check            # in one hospital's schema (tenant_command)
    scripts/care-dev.sh shell → python manage.py tenant_command ihr_patient_user_check --schema=<s>

Two kinds, each printed as one line per row (row id, user id and patient id; no names):

- ``stale``: a row other than ``MRD Records Access`` held by a restricted person (one of our
  restricted roles and no clinical slug through any membership, ``ihr_access.state.restricted``):
  a care-team entry from the person's earlier job. ihr_access.authz ignores it at read time (a
  restricted role on it gives demographics only); the admin guide says to remove them. The
  command exits 1 when there is any.
- ``grant-ended``: a ``MRD Records Access`` row whose records grant is no longer live. It counts
  for nothing (authz); the records-grant end routine removes it (P1-T11b4), so it is listed for
  information only and never makes the command fail. A row under a live grant is legitimate,
  audited access and is not listed.
"""

from django.core.management.base import BaseCommand, CommandError
from ihr_roles.registry import HIDDEN

from ihr_access import state
from ihr_access.models import RecordsGrant


def check_rows():
    """(stale rows, hidden-role rows whose grant ended), each ordered by id."""
    from care.emr.models import PatientUser

    role_ids = state.our_role_ids()
    hidden = role_ids.get(HIDDEN)
    live = set(RecordsGrant.live().values_list("user_id", "patient_id"))
    stale, ended, restricted = [], [], {}
    for row in (
        PatientUser.objects.order_by("id")
        .select_related("user")
        .only("id", "user_id", "patient_id", "role_id", "user")
    ):
        if row.role_id == hidden:
            if (row.user_id, row.patient_id) not in live:
                ended.append(row)
            continue
        if row.user_id not in restricted:
            restricted[row.user_id] = state.restricted(row.user, role_ids)
        if restricted[row.user_id]:
            stale.append(row)
    return stale, ended


def stale_rows():
    return check_rows()[0]


class Command(BaseCommand):
    help = (
        "List PatientUser rows of restricted people (stale) and records-access rows whose "
        "grant ended."
    )

    def handle(self, *args, **options):
        stale, ended = check_rows()
        for kind, rows in (("stale", stale), ("grant-ended", ended)):
            for row in rows:
                self.stdout.write(
                    f"{kind} patient_user={row.pk} user={row.user_id} "
                    f"patient={row.patient_id}"
                )
        if ended:
            self.stdout.write(
                f"{len(ended)} records-access row(s) whose grant ended: the grant end "
                "routine removes them"
            )
        if stale:
            msg = f"{len(stale)} PatientUser row(s) held by restricted people: remove them"
            raise CommandError(msg)
        self.stdout.write("no PatientUser row is held by a restricted person")
