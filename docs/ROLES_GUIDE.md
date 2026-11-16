# Roles guide for hospital administrators

A short guide to the eight Indian roles: where to place each person, what each sees in the web
app, how the records clerk (MRD) asks for and ends records access, how an administrator ends
someone else's access, and the Owner's totals page.

It describes the system as built in P1-T11b and P1-T11c (decision record: docs/adr/018; designs:
docs/reviews/p1-t11/design-b.md and design-c.md). Dates are DD-MM-YYYY and times are India time
(IST) throughout.

## 1. The eight roles

| Role | Job | Clinical record? |
|---|---|---|
| **Receptionist** | registers patients, edits their details, books appointments, opens visits | never |
| **Cashier** | accounts, bills and payments | never |
| **TPA Desk** | reads accounts, bills and payments for insurance claims | never |
| **MRD** (medical records) | finds patients and visits; reads one patient's whole record only through audited, time-limited records access (§4) | only under records access |
| **Owner** | sees the hospital's totals: visits, beds and money; approves nothing | never |
| **Lab Technician** | lab requests, specimens and reports at the lab's places | only what arrives with a lab request |
| **Radiologist** | imaging requests and reports | yes, for visits where Radiology is involved |
| **Store Keeper** | stock, deliveries and purchase orders at the store's places | never |

Rights add up: a person who is both a Doctor and the Owner works as a Doctor, and also sees the
Owner's totals page.

## 2. Where to place each person

Add people in CARE's usual way (facility → Users), choosing the organisation below:

| Role | Place them in |
|---|---|
| Receptionist, Cashier, TPA Desk, MRD, Owner | the facility's own (top) organisation |
| Lab Technician | the "Laboratory" department, linked to the lab's places |
| Radiologist | the "Radiology" department, linked to the imaging places |
| Store Keeper | the "Stores" department, linked to the store's places |

The system refuses (an error on save):

- Lab Technician, Radiologist or Store Keeper in the facility's top organisation (they would
  see every patient with an open visit);
- any of the eight roles in a state, district or other government organisation;
- the hidden role "MRD Records Access" anywhere (only records access gives it, for 30 minutes);
- adding a front-desk, billing, MRD, Owner, lab or store person to one patient's care team.

When a person changes job (for example a nurse moves to the front desk), old care-team entries
from the earlier job give nothing any more. To list them so they can be removed, a technical
administrator runs `manage.py ihr_patient_user_check` for the hospital; it prints row numbers
only, never names.

## 3. What each role sees in the web app

Menus show only what a person can use. A page typed in by hand that is not part of the role
shows "This page is not part of your role" (the server refuses it anyway).

| Role | Menu | Patient screens | Our pages |
|---|---|---|---|
| Receptionist | Overview, Appointments, Queues, Patients (search and visit lists, no bed board), Users, Billing → Accounts | patient details (with Edit), appointments, visits, accounts; create a visit; a visit opens as a status page (no bed, no notes) | none |
| Cashier | Overview, Patients (search and visit lists), Users, Billing → Accounts, Invoices, Payments | patient details (read only), visits, accounts; the visit status page shows the current bed and bed stays (for room rent) and links to the visit's account | none |
| TPA Desk | as Cashier | as Cashier | none |
| MRD | Overview, Patients (search and visit lists), Users, **Records access** | patient details (read only) and visits; **Ask for records access** on the patient page. Under records access: that one patient's full record, read only, with a banner showing the time left and **End now** | Records access |
| Owner | Overview, Users, **Hospital totals** | none (patient search is not shown) | Hospital totals |
| Lab Technician | Overview, Users, **My places** → Laboratory requests, Inventory items | none of their own; patient details come with each lab request | My places |
| Store Keeper | Overview, Users, **My places** → Inventory: items, to receive, to dispatch, purchase orders | none | My places |
| Radiologist | CARE's own menus | CARE's own screens; the clinical record only where Radiology is on the visit | none |

Never shown to the front desk, billing desk, MRD (without records access) or Owner: blood
group, tags, department, care team, ward or bed occupants, notes, diagnoses, reports,
prescriptions and discharge advice. Settings (configuration) are for the Facility Admin.

## 4. MRD records access

Records access lets a records clerk read one patient's whole record, read only, for a short time
(30 minutes unless the hospital changed `IHR_RECORDS_GRANT_MINUTES`; the page shows the length in
force). Every page opened under it is recorded in the audit trail with the grant's number; the
reason and reference are in that grant's own audit entry (table `ihr_records_grant`).

**Asking (the clerk):**

1. Find the patient: Patients → Search patients, then confirm with the year of birth.
2. On the patient page, choose **Ask for records access**.
3. Pick a reason: patient's request, legal, insurer, internal audit, NABH review or continuity of
   care. Legal and insurer need a reference (the case or claim number: letters, digits, spaces
   and `. / _ : -`, up to 100 characters).
4. Choose **Ask**. The patient's clinical tabs appear, with a banner "Records access · mm:ss
   left · End now".

**Ending (the clerk):** choose **End now** on the banner or on the Records access page, or let the
time run out. When access ends, by any route, the web app clears every clinical page from the
screen and returns to the Records access page with "Records access ended".

**One at a time per patient:** asking again for a patient whose access is still live is refused
("end it first"). The Records access page lists the clerk's own live access only (patient name,
year of birth, reason, reference, start, time left); other clerks' access is never listed.

**Alerts** (deploy/logs/alerts/records.yml): a burst of refused requests, more than 40 grants
to one person in 24 hours, and any grant between 22:00 and 07:00 raise an alert for the
platform's operators, who pass it to the hospital's administrator. The alert names the hospital
and the person's number, never the patient or the grant.

## 5. Ending someone else's records access (hospital-wide administrator)

A hospital-wide administrator (an admin role on the hospital's top organisation) may end any
clerk's live records access. There is no screen for this yet; it is one API call.

1. Find the grant number in the audit trail, filtered by the person the alert names (or the
   clerk you were told about):
   `GET /api/ihr/audit/entries/?table=ihr_records_grant&op=INSERT&actor=user:<id>:&from=YYYY-MM-DD`
   (the audit viewer takes dates as YYYY-MM-DD; `actor` matches the start of `user:<id>:<login>`,
   so keep the colon after the number; each entry's `record` is the grant number and
   `patients` names the patient).
2. Signed in on the hospital's own address, open the browser's developer console on any page of
   the web app and run:

   ```js
   await fetch("/api/ihr/records-access/<grant number>/", {
     method: "DELETE",
     headers: { "X-IHR-CSRF": "1" },
   }).then((r) => r.status);
   ```

3. `204` means it ended: within about 35 seconds the clerk's screen clears and returns to the
   Records access page. `404` means there is no such live access (already ended or expired), or
   you are not a hospital-wide administrator.

The end is recorded in the audit trail and in the log line `ihr-records: … outcome=ended`.

## 6. The Owner's totals page

Menu → **Hospital totals** (facility switcher for an Owner of more than one facility).

- **Range:** From and To (DD-MM-YYYY), by day or by month; the default is this month, from the
  1st to today. At most 366 days; nothing before 01-01-2000.
- **Tiles:** visits, admissions, discharges, inpatients now, beds occupied of total, invoiced,
  collected, refunded and outstanding now. Money is shown in ₹ with lakh and crore (for example
  ₹4.56 lakh); the exact figure (₹4,56,250.00) is in the tile's hint and the "Exact figures"
  table.
- **Tables:** visits by class and status; bills by status (all bills, drafts and cancelled
  included, dated by issue or creation); collected by payment method; the day or month series.
- **What the figures mean:** collected is money received (completed payments and advances).
  Adjustments are not collected money but do lower outstanding. Amounts include tax. Outstanding
  now is today's balance, not the balance at the end of the range.
- No patient, bill or visit is ever listed, and there are no department, doctor or diagnosis
  breakdowns (those come with the MIS reports, P2-T16).
