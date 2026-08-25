"""Export formatters for JournalEntry lists (app/erp/journal.py) into real ERP import formats.

Tally XML: structure verified directly against Tally's own published sample XML
(help.tallysolutions.com/sample-xml/) before writing this, not approximated from memory --
ENVELOPE/HEADER/BODY/DATA/TALLYMESSAGE/VOUCHER/LEDGERENTRIES.LIST, DATE in YYYYMMDD,
ISDEEMEDPOSITIVE=Yes for a debit line with a NEGATIVE amount, ISDEEMEDPOSITIVE=No for a credit
line with a positive amount -- an unusual sign convention, but it's what Tally's own sample
documents, so it's followed exactly rather than the more "obvious" positive-debit convention.

Zoho Books CSV and the generic CSV are a standard, defensible double-entry column shape
(Date, Reference, Account, Debit, Credit, Description) -- not independently verified against
Zoho's current live import template the way the Tally structure was, and that's disclosed
honestly in BUILD_LOG.md/README.md rather than presented with the same confidence.
"""

import csv
import io
from datetime import date
from xml.sax.saxutils import escape

from app.erp.journal import JournalEntry


def to_tally_xml(entries: list[JournalEntry], voucher_date: date | None = None) -> str:
    voucher_date = voucher_date or date.today()
    date_str = voucher_date.strftime("%Y%m%d")

    messages = []
    for i, entry in enumerate(entries, start=1):
        if not entry.lines:
            continue
        ledger_xml = []
        for line in entry.lines:
            if line.debit:
                is_positive, amount = "Yes", -line.debit
            else:
                is_positive, amount = "No", line.credit
            ledger_xml.append(
                f"""          <LEDGERENTRIES.LIST>
            <LEDGERNAME>{escape(line.account)}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>{is_positive}</ISDEEMEDPOSITIVE>
            <AMOUNT>{amount / 100:.2f}</AMOUNT>
          </LEDGERENTRIES.LIST>"""
            )
        messages.append(
            f"""      <TALLYMESSAGE>
        <VOUCHER>
          <DATE>{date_str}</DATE>
          <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
          <VOUCHERNUMBER>{i}</VOUCHERNUMBER>
          <NARRATION>{escape(entry.transaction_id)}{"" if entry.finalized else " (pending review)"}</NARRATION>
          <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
          <ISINVOICE>No</ISINVOICE>
{chr(10).join(ledger_xml)}
        </VOUCHER>
      </TALLYMESSAGE>"""
        )

    body = "\n".join(messages)
    return f"""<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Import</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>Vouchers</ID>
  </HEADER>
  <BODY>
    <DESC></DESC>
    <DATA>
{body}
    </DATA>
  </BODY>
</ENVELOPE>
"""


def to_zoho_books_csv(entries: list[JournalEntry], journal_date: date | None = None) -> str:
    journal_date = journal_date or date.today()
    date_str = journal_date.strftime("%m/%d/%Y")  # Zoho's documented date format for CSV import

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Journal Date", "Journal Number", "Account", "Debit", "Credit", "Description", "Reference Number"])
    for i, entry in enumerate(entries, start=1):
        for line in entry.lines:
            writer.writerow(
                [
                    date_str,
                    f"JE-{i:05d}",
                    line.account,
                    f"{line.debit / 100:.2f}" if line.debit else "",
                    f"{line.credit / 100:.2f}" if line.credit else "",
                    line.description,
                    entry.transaction_id,
                ]
            )
    return buf.getvalue()


def to_generic_csv(entries: list[JournalEntry], journal_date: date | None = None) -> str:
    journal_date = journal_date or date.today()
    date_str = journal_date.isoformat()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "transaction_id", "account", "debit", "credit", "description", "finalized"])
    for entry in entries:
        for line in entry.lines:
            writer.writerow(
                [
                    date_str,
                    entry.transaction_id,
                    line.account,
                    f"{line.debit / 100:.2f}" if line.debit else "0.00",
                    f"{line.credit / 100:.2f}" if line.credit else "0.00",
                    line.description,
                    "yes" if entry.finalized else "no",
                ]
            )
    return buf.getvalue()
