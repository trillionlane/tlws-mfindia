"""Trusted adapter composition seam; default behavior is the existing publisher.

A preparation callback may capture this validated invocation as a typed value.
It must not pretend to be a committed receipt. This seam grants no authority,
changes no SQL, and supplies no live environment or provider defaults.
"""


class PreparationError(ValueError):
    def __init__(self, phase):
        if phase in ('archive',):
            self.stage, self.reason = 'archive', 'SOURCE_ARCHIVE_FAILED'
        elif phase in ('catalogue','catalogues','English_catalogues','discovery',
                       'acquisition','books','English_books','directory','identities',
                       'reviewed_historical_source'):
            self.stage, self.reason = 'acquisition', 'SOURCE_RESPONSE_INVALID'
        else:
            self.stage, self.reason = 'preparation', 'PDF_DATE_OR_LAYOUT_INVALID'
        super().__init__(self.reason)


def invoke(callback, conn, publisher, *args, **kwargs):
    if callback is None:
        return publisher(conn, *args, **kwargs)
    return callback(conn, publisher, *args, **kwargs)
