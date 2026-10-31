"""Exporting a generated turn to a file the attorney can send on.

FR-17's requirement is specific and easy to half-implement: the unreviewed-draft
notice has to be **inside the exported file**, not only in the UI. A document that
looks reviewed once it leaves the browser is the failure mode — the file is what gets
emailed, and by then the banner is gone.

FR-19 is also load-bearing here: this produces bytes and returns them. It does not
send, file or serve anything.
"""

from .render import ExportResult, export_message

__all__ = ["ExportResult", "export_message"]
