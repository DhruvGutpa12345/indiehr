"""The Owner's totals (P1-T11b4; design-b.md §8; Q-K21, Q-K39 A auto-accepted, awaiting owner
confirmation).

    GET /api/ihr/owner/totals/?facility=<id>&from=DD-MM-YYYY&to=DD-MM-YYYY[&by=day|month]

Aggregates only, computed in the database (``aggregate()`` and ``values(...).annotate()``):
no model instance of a patient, visit, bill or payment is ever built, so the audit trail records
no patient shown (ihr_audit/reads.py) and nothing identifies anyone. Never ids, names, rows, or
a split by department, service, doctor or diagnosis (that is MIS, P2-T16). Small numbers are not
suppressed (Q-K39 A): without a clinical dimension a small count points to no one's condition.

Days are India's (IST); ``from`` and ``to`` are included; at most ``MAX_DAYS``; never before
``MIN_DAY`` nor more than ``MAX_DAYS_AHEAD`` days after today (review A2: a far date would
overflow the date arithmetic and answer 500). Amounts are INR
strings with two decimals (lakh and crore formatting is the screen's job, P1-T11c).

- ``encounters``: visits whose period starts in the range (no start: their creation), counted
  by class and status;
- ``series``: per day (or month): admissions and discharges (inpatient visits starting or
  ending), amounts invoiced (issued and balanced bills, by issue date) and collected (active,
  completed payments and advances, by payment date; credit notes apart as ``refunded``). A
  payment whose outcome is not ``complete`` (queued, partial, error: a declined card) is not
  money received, as in CARE's own ``Account.total_paid`` and an invoice's ``total_payments``
  (review A1). Adjustments are not counted as collected (they move no money), although CARE's
  ``total_paid`` (and so ``outstanding_now``) includes them: Q-K46, open, awaiting the owner;
- ``inpatients_now``: inpatient visits in progress or on hold now;
- ``beds``: bed places now, and how many hold a visit;
- ``invoices_by_status``: bills dated in the range (issue date, else creation), count and
  amount per status; ``collected_by_method``: payments per method;
- ``outstanding_now``: the balance of the facility's open accounts now.
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.db.models import (
    Case,
    Count,
    DateTimeField,
    F,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, Trunc

IST = ZoneInfo("Asia/Kolkata")
MAX_DAYS = 366
DAY_FORMAT = "%d-%m-%Y"
MONTH_FORMAT = "%m-%Y"
INPATIENT = "imp"
IN_HOSPITAL = ("in_progress", "on_hold")
BILLED = ("issued", "balanced")
RECEIVED_TYPES = ("payment", "advance")
# Only a completed payment is money received (CARE's sync_items.py and invoice spec agree)
RECEIVED_OUTCOME = "complete"
# The earliest day asked about, and how far past today a range may end (review A2)
MIN_DAY = datetime.date(2000, 1, 1)
MAX_DAYS_AHEAD = 366
OPEN_ACCOUNTS = ("active", "on_hold")
BED = "bd"
# A period's start or end as CARE stores it (an ISO date-time); anything else counts as none
ISO_START = r"^\d{4}-\d{2}-\d{2}T"
CENTS = Decimal("0.01")


class TotalsError(ValueError):
    """A parameter is wrong: ``field`` and ``message`` for the 400."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def parse_day(value, field):
    try:
        return datetime.datetime.strptime(value or "", DAY_FORMAT).date()  # noqa: DTZ007 (a calendar day)
    except ValueError:
        raise TotalsError(field, "Use DD-MM-YYYY.") from None


def parse_range(params):
    """(first day, last day, "day"|"month") from the query, or TotalsError."""
    start = parse_day(params.get("from"), "from")
    end = parse_day(params.get("to"), "to")
    if start < MIN_DAY:
        raise TotalsError("from", f"Not before {MIN_DAY.strftime(DAY_FORMAT)}.")
    today = datetime.datetime.now(IST).date()
    if end > today + datetime.timedelta(days=MAX_DAYS_AHEAD):
        raise TotalsError("to", f"At most {MAX_DAYS_AHEAD} days after today.")
    if end < start:
        raise TotalsError("to", "Must not be before from.")
    if (end - start).days + 1 > MAX_DAYS:
        raise TotalsError("to", f"At most {MAX_DAYS} days.")
    by = params.get("by") or "day"
    if by not in ("day", "month"):
        raise TotalsError("by", "day or month.")
    return start, end, by


def money(value):
    return str((value or Decimal(0)).quantize(CENTS, rounding=ROUND_HALF_UP))


def _bounds(start, end):
    """IST midnight of the first day and of the day after the last."""
    return (
        datetime.datetime.combine(start, datetime.time(), IST),
        datetime.datetime.combine(
            end + datetime.timedelta(days=1), datetime.time(), IST
        ),
    )


def _period_time(key):
    """The visit's period ``start`` or ``end`` as a time, or null when it is not one."""
    return Case(
        When(
            **{f"period__{key}__regex": ISO_START},
            then=Cast(KeyTextTransform(key, "period"), DateTimeField()),
        ),
        default=Value(None),
        output_field=DateTimeField(),
    )


def _periods(start, end, by):
    """Every day (or month) of the range, in order, as the keys the series uses."""
    out = []
    if by == "day":
        day = start
        while day <= end:
            out.append(day)
            day += datetime.timedelta(days=1)
        return out
    month = start.replace(day=1)
    while month <= end:
        out.append(month)
        month = (month + datetime.timedelta(days=32)).replace(day=1)
    return out


def _label(day, by):
    return day.strftime(DAY_FORMAT if by == "day" else MONTH_FORMAT)


def _bucket(value, by):
    """A truncated database value (a date-time in IST, or a date) as the series key."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        value = value.astimezone(IST).date()
    return value if by == "day" else value.replace(day=1)


def _per_period(queryset, field, by, measure):
    """{period key: measure} from one grouped query (``Trunc`` in IST)."""
    rows = (
        queryset.annotate(ihr_bucket=Trunc(field, by, tzinfo=IST))
        .values("ihr_bucket")
        .annotate(ihr_measure=measure)
        .order_by("ihr_bucket")
    )
    found = {}
    for row in rows:
        key = _bucket(row["ihr_bucket"], by)
        found[key] = found.get(key, 0) + (row["ihr_measure"] or 0)
    return found


def compute(facility, start, end, by):
    """The totals of ``facility`` for the IST days ``start`` .. ``end``."""
    from care.emr.models import Encounter
    from care.emr.models.account import Account
    from care.emr.models.invoice import Invoice
    from care.emr.models.location import FacilityLocation
    from care.emr.models.payment_reconciliation import PaymentReconciliation

    low, high = _bounds(start, end)
    # A visit with no period start (CARE's demo data, an import) counts from its creation
    visits = Encounter.objects.filter(facility=facility).annotate(
        started=Coalesce(_period_time("start"), F("created_date")),
        ended=_period_time("end"),
    )
    in_range = visits.filter(started__gte=low, started__lt=high)
    encounters = [
        {
            "encounter_class": row["encounter_class"],
            "status": row["status"],
            "count": row["count"],
        }
        for row in in_range.values("encounter_class", "status")
        .annotate(count=Count("id"))
        .order_by("encounter_class", "status")
    ]
    inpatient = visits.filter(encounter_class=INPATIENT)
    admissions = _per_period(
        inpatient.filter(started__gte=low, started__lt=high), "started", by, Count("id")
    )
    discharges = _per_period(
        inpatient.filter(ended__gte=low, ended__lt=high), "ended", by, Count("id")
    )
    inpatients = Encounter.objects.filter(
        facility=facility, encounter_class=INPATIENT, status__in=IN_HOSPITAL
    )

    beds = (
        FacilityLocation.objects.filter(facility=facility, form=BED, mode="instance")
        .exclude(status="inactive")
        .aggregate(
            total=Count("id"),
            occupied=Count("id", filter=Q(current_encounter__isnull=False)),
        )
    )

    bills = Invoice.objects.filter(facility=facility).annotate(
        dated=Coalesce("issue_date", "created_date")
    )
    billed = bills.filter(status__in=BILLED, issue_date__gte=low, issue_date__lt=high)
    invoiced = _per_period(billed, "issue_date", by, Sum("total_gross"))
    by_status = [
        {"status": row["status"], "count": row["count"], "amount": money(row["amount"])}
        for row in bills.filter(dated__gte=low, dated__lt=high)
        .values("status")
        .annotate(count=Count("id"), amount=Sum("total_gross"))
        .order_by("status")
    ]

    payments = PaymentReconciliation.objects.filter(
        facility=facility,
        status="active",
        outcome=RECEIVED_OUTCOME,
        reconciliation_type__in=RECEIVED_TYPES,
    ).annotate(paid=Coalesce("payment_datetime", "created_date"))
    paid = payments.filter(paid__gte=low, paid__lt=high)
    received = paid.filter(is_credit_note=False)
    collected = _per_period(received, "paid", by, Sum("amount"))
    by_method = [
        {"method": row["method"], "count": row["count"], "amount": money(row["amount"])}
        for row in received.values("method")
        .annotate(count=Count("id"), amount=Sum("amount"))
        .order_by("method")
    ]
    refunded = paid.filter(is_credit_note=True).aggregate(amount=Sum("amount"))
    outstanding = Account.objects.filter(
        facility=facility, status__in=OPEN_ACCOUNTS
    ).aggregate(amount=Sum(F("total_balance")))

    series = [
        {
            "period": _label(key, by),
            "admissions": admissions.get(key, 0),
            "discharges": discharges.get(key, 0),
            "invoiced": money(invoiced.get(key)),
            "collected": money(collected.get(key)),
        }
        for key in _periods(start, end, by)
    ]
    return {
        "from": start.strftime(DAY_FORMAT),
        "to": end.strftime(DAY_FORMAT),
        "by": by,
        "time_zone": "Asia/Kolkata",
        "currency": "INR",
        "encounters": encounters,
        "inpatients_now": inpatients.count(),
        "beds": {"total": beds["total"], "occupied": beds["occupied"]},
        "invoices_by_status": by_status,
        "collected_by_method": by_method,
        "refunded": money(refunded["amount"]),
        "outstanding_now": money(outstanding["amount"]),
        "series": series,
    }
