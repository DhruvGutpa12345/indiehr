"""One log record, one line (the audit watch; P1-T21 review).

The log alerts AuditHospitalCheckFailed and AuditHospitalSealLate accept an exact line from the
audit-watch service, and the log pipeline splits a container's output at line breaks. So any
text in a record that came from outside the watch's own code (an object key or a body from the
fingerprint bucket, an error message, a traceback) must never start a line of its own: this
formatter escapes every line break and control character of the whole record, traceback
included. The watch's fixed lines are written straight to standard error (watch._emit), never
through logging. No Django import: the settings load it.
"""

import logging


def one_line(text):
    """``text`` with every non-printable character (line breaks, carriage returns, Unicode
    line and paragraph separators, other controls) escaped as Python would show it."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(text))


class OneLineFormatter(logging.Formatter):
    def format(self, record):
        return one_line(super().format(record))
