"""CARE's batch endpoint, limited to what the web app batches (P1-T20a; TENANCY_DESIGN §6.5).

``POST /api/v1/batch_requests/`` runs several API calls in one transaction, calling each view
directly (no middleware). Every sub-request is signed in again by DRF, so a batch cannot reach
more than its caller could one call at a time; this keeps it to the routes and methods the web
app really sends in a batch (``batch_allowlist.txt``, generated from the web app's call sites by
``scripts/batch-allowlist.py``) as defence in depth.

Every sub-request is checked **before** any runs, on the very request objects that then run
(review A3): the decoded ``path_info`` the resolver sees and the request's method, never the raw
URL alone. One refused sub-request refuses the whole batch: nothing runs, and the answer has
CARE's batch error shape (``{"results": [{reference_id, status_code: 403, data}]}``), which the
web app already shows. Each refusal writes one fixed line (``refusal_line``, alert
BatchRequestRefused in deploy/logs/alerts/batch.yml): the hospital, a reason code, the method and
the route's URL name, never a value from the request.
"""

import functools
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from care.emr.api.viewsets.batch_request import (
    BatchRequest,
    BatchRequestView,
    HandledError,
    UnHandledError,
)
from care.emr.utils.batch_requests import (
    convert_batch_request_to_wsgi,
    execute_serially,
    get_response,
)
from django.db import connection, transaction
from django.urls import Resolver404, get_urlconf, resolve
from rest_framework.response import Response

ALLOW_LIST = Path(__file__).with_name("batch_allowlist.txt")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Never needed by a UUID, a slug or a route: an encoded slash, path parameters, a backslash, an
# empty segment, a dot segment, a query or fragment, whitespace or control characters
BAD_URL = re.compile(r"[%;\\?#\s\x00-\x1f\x7f]|//|/\.\.?(?:/|$)")
# A path parameter's value in a batched URL: an id or a slug, one segment
PARAM = r"[A-Za-z0-9_-]+"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
SAMPLE = "00000000-0000-4000-8000-000000000000"
REASONS = ("not_listed", "bad_url", "bad_method", "nested", "override")
REFUSAL_PREFIX = "ihr-batch: refused"
ROUTE_NAME = re.compile(
    r"[A-Za-z0-9_.:-]{1,100}"
)  # what the log alert accepts as a route
REFUSED = {"detail": "This request is not allowed in a batch."}


def load_allow_list(path=ALLOW_LIST):
    """{(METHOD, path template)} from the generated list; comments and blank lines skipped."""
    entries = set()
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        method, template = line.split()
        if method not in METHODS or not template.startswith("/api/v1/"):
            msg = f"batch_allowlist.txt: bad entry {line!r}"
            raise ValueError(msg)
        entries.add((method, template))
    return frozenset(entries)


def template_regex(template):
    parts = PLACEHOLDER.split(template)
    # split() alternates literal text and placeholder names
    return re.compile(
        "".join(re.escape(p) if i % 2 == 0 else PARAM for i, p in enumerate(parts))
    )


def sample_path(template):
    return PLACEHOLDER.sub(SAMPLE, template)


@functools.cache
def _compiled():
    return tuple(
        (method, template, template_regex(template))
        for method, template in sorted(load_allow_list())
    )


@functools.cache
def _expected_route(urlconf, template):
    """The route CARE resolves the template to (the resolved route of a filled sample)."""
    try:
        return resolve(sample_path(template), urlconf).route
    except Resolver404:
        return None


def _match(path_info):
    try:
        return resolve(path_info)
    except Resolver404:
        return None


def listed(method, path_info, match):
    """Is (method, decoded path) a reviewed entry, and does it resolve to that entry's route?"""
    if match is None:
        return False
    for entry_method, template, regex in _compiled():
        if entry_method == method and regex.fullmatch(path_info):
            return match.route == _expected_route(get_urlconf(), template)
    return False


def refusal_line(reason, method, route):
    """The fixed line the log alert matches: no value from the request, only our own words."""
    slug = getattr(getattr(connection, "tenant", None), "slug", None) or "unknown"
    if not ROUTE_NAME.fullmatch(route):
        route = "unnamed"
    return f"{REFUSAL_PREFIX} hospital={slug} reason={reason} method={method} route={route}"


def _emit(line):
    # Straight to standard error as one whole line: a logger would put a prefix in front
    os.write(2, f"{line}\n".encode())


def check(sub, wsgi):
    """The reason ``sub`` (the parsed sub-request) with its built request ``wsgi`` is refused,
    or None. Judged on what will run: ``wsgi.path_info`` (decoded) and ``wsgi.method``."""
    method = wsgi.method if wsgi.method in METHODS else "other"
    match = _match(wsgi.path_info)
    route = (match.url_name or "unnamed") if match else "unresolved"
    reason = None
    if sub.method not in METHODS or wsgi.method != sub.method:
        reason = "bad_method"
    elif (
        "HTTP_X_HTTP_METHOD_OVERRIDE" in wsgi.META
        or "HTTP_X_METHOD_OVERRIDE" in wsgi.META
    ):
        reason = "override"
    else:
        parts = urlsplit(sub.url)
        if (
            parts.scheme
            or parts.netloc
            or BAD_URL.search(sub.url)
            or not sub.url.startswith("/api/v1/")
            or wsgi.path_info != sub.url
            or (sub.method == "GET" and sub.body)
        ):
            reason = "bad_url"
        elif match is not None and issubclass(
            getattr(match.func, "cls", type(None)), BatchRequestView
        ):
            reason = "nested"
        elif not listed(wsgi.method, wsgi.path_info, match):
            reason = "not_listed"
    return (reason, method, route) if reason else None


class IhrBatchRequestView(BatchRequestView):
    """CARE's batch view with the allow-list. The run itself is CARE's ``create`` on the checked
    request objects (``test_batch.UpstreamDrift`` pins CARE's method, so a change upstream is
    reviewed here)."""

    def create(self, request, *args, **kwargs):
        requests = BatchRequest(**request.data)
        refused = []
        for sub in requests.requests:
            if sub.method not in METHODS:
                # CARE would look the method up on its request factory (any attribute name)
                refused.append((sub, ("bad_method", "other", "unresolved")))
        if not refused:
            wsgi_requests = convert_batch_request_to_wsgi(request, requests)
            for sub, wsgi in zip(requests.requests, wsgi_requests, strict=True):
                if outcome := check(sub, wsgi):
                    refused.append((sub, outcome))
        if refused:
            for _sub, outcome in refused:
                _emit(refusal_line(*outcome))
            results = [
                {"reference_id": sub.reference_id, "status_code": 403, "data": REFUSED}
                for sub, _outcome in refused
            ]
            return Response({"results": results}, status=400)
        return self._run(requests, wsgi_requests)

    def _run(self, requests, wsgi_requests):
        # CARE's BatchRequestView.create from here on, run on the checked request objects
        errored = False
        loop = 0
        try:
            with transaction.atomic():
                responses = execute_serially(wsgi_requests, get_response)
                structured_responses = []
                for response in responses:
                    if response["status_code"] > 299:  # noqa: PLR2004
                        errored = True
                    if response["status_code"] >= 500:  # noqa: PLR2004
                        structured_responses.append(
                            {
                                "reference_id": requests.requests[loop].reference_id,
                                "status_code": response["status_code"],
                            }
                        )
                        raise UnHandledError
                    structured_responses.append(
                        {
                            "reference_id": requests.requests[loop].reference_id,
                            "data": response["data"],
                            "status_code": response["status_code"],
                        }
                    )
                    loop += 1
                if errored:
                    raise HandledError
        except HandledError:
            return Response({"results": structured_responses}, status=400)
        except UnHandledError:
            return Response({"results": structured_responses}, status=500)
        return Response({"results": structured_responses})
