# ADR-0006 Header contracts and drift pause for Google Sheets
Contracts come from live workbook metadata. Any header change pauses that tab (prior snapshot kept) until the contract is updated through code review. Identity = composite key; staging is immutable and keeps hyperlink targets. Alternative (best-effort column guessing) rejected by spec 04 §6.
