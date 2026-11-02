# ADR-004: Accounting handoff — Tally export and an ERPNext v16 connector

- **Status:** Accepted
- **Date:** 2026-11-03
- **Decided by:** product owner (Q-C1: "Both from the start")

## Context

Hospital billing (OPD/IPD bills, receipts, refunds, deposits, pharmacy sales and purchases) happens inside IndieHR. General-ledger accounting is done elsewhere:

- most Indian hospitals keep their books in **Tally Prime**;
- some want a full ERP (general ledger, procurement, HR/payroll).

ERPNext v16 supports **MariaDB 11.8 only**; Postgres support is not official.

## Decision

1. **IndieHR is the source of truth for patient billing.**
   - It produces **accounting events**: sales invoice, receipt, refund, credit note, deposit, purchase/GRN, stock issue.
   - These are written to an `accounting_outbox` table in each tenant.
2. **Tally connector:**
   - generates Tally Prime XML vouchers (sales, receipt, purchase, journal) from the outbox;
   - offers a download per day or date range;
   - has ledger-mapping screens per tenant;
   - later, can push live through Tally's XML-over-HTTP port via a small on-site agent.
3. **ERPNext connector:**
   - runs a separate Frappe bench (ERPNext v16 + India Compliance + optional HRMS/India Payroll) with **one site per tenant** that opts in;
   - posts outbox events to the ERPNext REST API (Sales Invoice, Payment Entry, Purchase Receipt, Journal Entry), idempotently;
   - customers and suppliers are synced.
4. **Tenant choice:** each tenant picks `none`, `tally`, `erpnext` or both, in settings.
5. **Licensing and branding:**
   - ERPNext runs **unmodified** (GPLv3, network use).
   - Our connector stays on our side of the REST API.
   - We do not use the "ERPNext" or "Frappe" names in our branding.

## Task impact

| Task | Change |
|---|---|
| P2-T17 | Split into **P2-T17a** (accounting outbox + Tally XML export) and **P2-T17b** (ERPNext v16 stack + connector) |

## Consequences

- The ERPNext stack adds MariaDB and Frappe workers, which cost about 2–3 GB RAM. Run it only when testing P2-T17b, or on staging.
- The outbox pattern gives retries, an audit trail and the option to add more ERPs (Zoho Books, etc.) later.
