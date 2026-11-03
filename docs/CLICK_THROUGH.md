# Click-through: what CARE gives us today (P0-T11)

**Goal:** you see, hands-on, what we are building on before we change it. About 20 minutes. Nothing you do here matters: all data is synthetic and on your PC only.

**Before you start:** the app must be running. If http://localhost:8080 does not open, run:

```bash
scripts/care-dev.sh up && scripts/care-dev.sh frontend
```

**Logins:** usernames below. The password is in `deploy/seed/TEST_USERS.md` (one password for all). Log out via your initials at the bottom-left.

Demo facility: **"Facility with Patients"**. Direct link: http://localhost:8080/facility/011edaeb-3c54-4e3d-a75f-bdd5eaf8d95b/overview

---

## 1. Front desk — `care-staff`

1. Log in at http://localhost:8080/login.
2. Open the facility, then **Patients** (left bar) → search phone `7964254593` → open **Nakul Chadha**.
3. Back on the patient search page, click **Add New Patient**. Note which fields exist: is anything missing for an Indian front desk (relation S/o/W/o, UHID, ABHA, MLC)? Cancel without saving, or save; either is fine.
4. Open **Appointments**: http://localhost:8080/facility/011edaeb-3c54-4e3d-a75f-bdd5eaf8d95b/appointments. Look at how slots and tokens work.

## 2. Doctor — `care-doctor`

1. Open **Encounters**: http://localhost:8080/facility/011edaeb-3c54-4e3d-a75f-bdd5eaf8d95b/encounters → **View Encounter** on any patient.
2. Try **Add Medication** (prescription), **Service Request** (lab/radiology order) and **Forms**.
   - Diagnosis and drug search **will not find anything**: the terminology service is switched off on purpose until P1-T10.
3. Look at the **Plots** and **Medicines** tabs.

## 3. Nurse — `care-nurse`

1. Open the same encounter. Check how vitals and medication administration are recorded.
2. Open **Bed availability**: http://localhost:8080/facility/011edaeb-3c54-4e3d-a75f-bdd5eaf8d95b/bed-availability

## 4. Pharmacist — `care-pharmacist`

1. Open the facility → **Services** (left bar) → the pharmacy / dispensing area. Look at how a prescription becomes a dispense, and how batches and expiry are shown.

## 5. Billing — `care-billing`, then `care-fac-admin`

1. As `care-billing`, open **Invoices**: http://localhost:8080/facility/011edaeb-3c54-4e3d-a75f-bdd5eaf8d95b/billing/invoices and **Payments**: …/billing/payments
2. As `care-fac-admin`, open **Accounts**: http://localhost:8080/facility/011edaeb-3c54-4e3d-a75f-bdd5eaf8d95b/billing/account. Only Facility Admin can create a patient's billing account in CARE today.
3. As `care-fac-admin`, open the facility **Settings** and look at billing/tariffs (charge item definitions) and departments.

---

## Please send me back

- Anything that **confused you** or felt **wrong for an Indian hospital**.
- Anything **missing** that a 10–100 bed hospital would expect on day one.
- Any **page that broke**. A screenshot plus the address bar is enough.

Your notes feed the Phase 2 module scope and P1-T14 (customer discovery).

## Known and expected (do not report these)

- Diagnosis/drug search returns nothing: P1-T10.
- The facility map in facility settings is blank: blocked on purpose (no OpenStreetMap calls).
- CARE logo/name and "Software Update" pop-up: branding is P1-T2.
- Most screens are English only; Hindi covers ~11%: P1-T7.
