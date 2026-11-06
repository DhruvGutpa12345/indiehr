"""Scale benchmark for one cell (TENANCY_DESIGN.md §11.3, task P1-T1c). Bench databases only.

Each step prints ``BENCH {json}`` lines; ``scripts/tenancy-scale-bench.sh`` collects them.

``grow --to N``
    Make the cell hold N hospitals. The first is provisioned for real and loaded with CARE's demo
    data; it is the template. Fillers are copies of the template (django-tenants ``clone_schema``)
    because provisioning 200 hospitals for real would take hours on the dev machine. The last
    hospital is again provisioned for real, and timed: onboarding at that cell size.

``measure``
    Catalogue and database size; backend cache memory after one connection touches every
    hospital; ``migrate_schemas`` with nothing to do; a one-migration release (``emr`` 0083 rolled
    back and applied again) with the standard and the multiprocessing executor.
"""

import json
import sys
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django_tenants.clone import CloneSchema
from django_tenants.utils import schema_context

from ihr_platform.models import Domain, Hospital, generate_schema_name
from ihr_platform.seeding import load_demo_data

TEMPLATE_SLUG = "bench-template"
# Tables a typical request touches; reading them loads their relation and index caches.
TOUCHED_TABLES = (
    "emr_patient",
    "emr_encounter",
    "emr_observation",
    "emr_medicationrequest",
    "emr_chargeitem",
    "emr_invoice",
    "facility_facility",
    "users_user",
    "emr_facilitylocation",
    "emr_questionnaireresponse",
)
RELEASE_APP, RELEASE_FROM, RELEASE_TO = "emr", "0082", "0083"


def emit(step, **values):
    sys.stdout.write(
        "BENCH " + json.dumps({"step": step, **values}, default=float) + "\n"
    )
    sys.stdout.flush()


def tenants():
    return Hospital.objects.exclude(schema_name="public").order_by("created_on")


class Command(BaseCommand):
    help = "Scale benchmark for one cell (bench databases only)."

    def add_arguments(self, parser):
        parser.add_argument("step", choices=["grow", "measure"])
        parser.add_argument("--to", type=int, default=1, help="grow: hospitals wanted")

    def handle(self, *args, step, to, **options):
        name = settings.DATABASES["default"]["NAME"]
        if not str(name).startswith("bench_"):
            msg = f"Refusing to run on database {name!r}: use a database named bench_*."
            raise CommandError(msg)
        with schema_context("public"):
            if step == "grow":
                self.grow(to)
            else:
                self.measure()

    # --- grow --------------------------------------------------------------------------------

    def provision(self, slug):
        started = time.monotonic()
        call_command(
            "ihr_create_tenant",
            slug=slug,
            name=f"Bench {slug}",
            domain=[f"{slug}.bench.test"],
            verbosity=0,
        )
        connection.set_schema_to_public()
        return Hospital.objects.get(slug=slug), time.monotonic() - started

    def grow(self, target):
        count = tenants().count()
        if count == 0:
            template, seconds = self.provision(TEMPLATE_SLUG)
            emit("onboarding", n=1, seconds=round(seconds, 1))
            started = time.monotonic()
            load_demo_data(template.schema_name, f"{TEMPLATE_SLUG}.bench.test")
            connection.set_schema_to_public()
            emit("seed_template", seconds=round(time.monotonic() - started, 1))
            count = 1
        if count >= target:
            return
        template = Hospital.objects.get(slug=TEMPLATE_SLUG)
        cloner = CloneSchema()
        clone_times = []
        while count < target - 1:
            schema = generate_schema_name()
            started = time.monotonic()
            cloner.clone_schema(template.schema_name, schema, set_connection=False)
            hospital = Hospital(
                schema_name=schema,
                slug=f"bench-{count:04d}",
                name=f"Bench clone {count}",
                status=Hospital.Status.ACTIVE,
            )
            hospital.auto_create_schema = False  # the clone already made the schema
            hospital.save()
            Domain.objects.create(
                domain=f"bench-{count:04d}.bench.test", tenant=hospital
            )
            clone_times.append(time.monotonic() - started)
            count += 1
        if clone_times:
            emit(
                "clones",
                made=len(clone_times),
                mean_seconds=round(sum(clone_times) / len(clone_times), 2),
            )
        _, seconds = self.provision(f"bench-real-{target:04d}")
        emit("onboarding", n=target, seconds=round(seconds, 1))

    # --- measure -----------------------------------------------------------------------------

    def measure(self):
        n = tenants().count()
        schemas = list(tenants().values_list("schema_name", flat=True))
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT (SELECT count(*) FROM pg_class),
                       (SELECT count(*) FROM pg_attribute),
                       (SELECT count(*) FROM pg_depend),
                       (SELECT sum(pg_total_relation_size(c.oid)) FROM pg_class c
                          JOIN pg_namespace s ON s.oid = c.relnamespace
                         WHERE s.nspname = 'pg_catalog' AND c.relkind = 'r'),
                       pg_database_size(current_database())
                """
            )
            pg_class, pg_attribute, pg_depend, catalog_bytes, db_bytes = (
                cursor.fetchone()
            )
        emit(
            "catalogue",
            n=n,
            pg_class=pg_class,
            pg_attribute=pg_attribute,
            pg_depend=pg_depend,
            catalog_mb=round(catalog_bytes / 2**20, 1),
            database_mb=round(db_bytes / 2**20, 1),
        )
        self.measure_connection_memory(n, schemas)
        self.measure_migrations(n)

    def measure_connection_memory(self, n, schemas):
        """One fresh connection visits every hospital, as a long-lived app connection would."""
        connection.close()
        marks = {1, 10, 50, 100, len(schemas)}
        samples = []
        started = time.monotonic()
        with connection.cursor() as cursor:
            cursor.execute("SELECT sum(total_bytes) FROM pg_backend_memory_contexts")
            baseline = cursor.fetchone()[0]
            for index, schema in enumerate(schemas, start=1):
                cursor.execute(f'SET search_path TO "{schema}", public')
                for table in TOUCHED_TABLES:
                    cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
                if index in marks:
                    cursor.execute(
                        "SELECT sum(total_bytes) FROM pg_backend_memory_contexts"
                    )
                    samples.append(
                        {"tenants": index, "mb": round(cursor.fetchone()[0] / 2**20, 1)}
                    )
            cursor.execute("SET search_path TO public")
        elapsed = time.monotonic() - started
        connection.close()
        emit(
            "connection_memory",
            n=n,
            baseline_mb=round(baseline / 2**20, 1),
            after=samples,
            ms_per_tenant_visit=round(1000 * elapsed / max(len(schemas), 1), 1),
        )

    def migrate(self, *args):
        connection.close()
        started = time.monotonic()
        call_command("migrate_schemas", *args, "--tenant", verbosity=0)
        connection.close()
        connection.set_schema_to_public()
        return round(time.monotonic() - started, 1)

    def measure_migrations(self, n):
        noop = self.migrate()
        back = self.migrate(RELEASE_APP, RELEASE_FROM)
        forward = self.migrate(RELEASE_APP, RELEASE_TO)
        self.migrate(RELEASE_APP, RELEASE_FROM)
        forward_mp = self.migrate(RELEASE_APP, RELEASE_TO, "--executor=multiprocessing")
        emit(
            "migrations",
            n=n,
            noop_seconds=noop,
            rollback_seconds=back,
            release_seconds=forward,
            release_multiprocessing_seconds=forward_mp,
            processes=getattr(settings, "TENANT_MULTIPROCESSING_MAX_PROCESSES", 2),
        )
