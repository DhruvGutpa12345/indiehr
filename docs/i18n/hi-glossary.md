# Hindi glossary (P1-T7c)

Every Hindi string we add follows this list, so one English term always gets one Hindi term.
Owner's choices (2026-11-08): **everyday hospital Hindi**, the words staff actually use;
our strings live in `deploy/locale/hi.json` and only fill strings upstream has not translated.

Rows marked **❓** are our best guess and need your decision; change the row here and the
translations follow in the next batch (and are corrected in earlier ones).

## Rules

1. **Address the user as आप**, with polite forms: "सेव करें", "चुनें", "कृपया … दर्ज करें".
2. **Keep exactly:** `{{placeholders}}`, `<tags>` and `</tags>`, `$t(...)`, line breaks, and leading or trailing spaces and punctuation that the English has.
3. **Never translate:** IndieHR, ABHA, ABDM, UHID, OPD, IPD, ICU, OT, OTP, PIN, GST, GSTIN, PDF, CSV, ID, QR, URL, SMS, e-mail addresses, file types, units (mg, ml, mmHg, kg, cm, °C, °F, %, SpO₂), FHIR, SNOMED, ICD-10, LOINC.
4. **Nukta and spelling:** ज़, फ़, ख़, ड़ as standard (ज़रूरी, फ़ोन, फ़ाइल, तारीख़); "-एँ" for polite plural verbs (करें, जोड़ें, हटाएँ); full stop "।" only after a full sentence.
5. **Short labels stay short.** A button is a verb ("सेव करें"); a column heading is a noun ("स्थिति").
6. **Numbers and dates** are formatted by the app; never write them out in words.
7. **Flag for clinical review** every string that a clinician acts on: diagnoses, symptoms, scores and their levels (consciousness, pain, triage), medicine names, dose, route, frequency, instructions, lab and vital-sign meanings, allergy and consent wording.

## People and places

| English | Hindi | Note |
|---|---|---|
| patient | मरीज़ | not रोगी |
| doctor / practitioner | डॉक्टर | |
| nurse | नर्स | |
| staff | स्टाफ़ | |
| user | यूज़र | |
| administrator / admin | एडमिन | |
| hospital | अस्पताल | |
| facility | फ़ैसिलिटी | ❓ CARE's unit of a hospital or clinic. Alternative: केंद्र |
| department | विभाग | |
| organization | संगठन | |
| location | स्थान | |
| ward | वार्ड | |
| bed | बेड | |
| role | भूमिका | |
| care team | केयर टीम | |
| relative / next of kin | परिजन | |
| emergency contact | आपातकालीन संपर्क | |

## Visits and scheduling

| English | Hindi | Note |
|---|---|---|
| encounter | विज़िट | ❓ CARE's word for one OPD visit or IPD stay. Alternative: एनकाउंटर |
| visit | विज़िट | |
| appointment | अपॉइंटमेंट | |
| token | टोकन | |
| queue | कतार | |
| schedule | शेड्यूल | |
| slot | स्लॉट | |
| registration / register (a patient) | पंजीकरण / पंजीकरण करें | |
| admission / admit | भर्ती / भर्ती करें | |
| discharge | डिस्चार्ज | |
| transfer | ट्रांसफ़र | |
| referral / refer | रेफ़रल / रेफ़र करें | |
| follow-up | फ़ॉलो-अप | |
| walk-in | वॉक-इन | |
| check in | चेक-इन | |

## Clinical (flag every string that uses these)

| English | Hindi | Note |
|---|---|---|
| diagnosis | निदान | |
| symptom | लक्षण | |
| condition | बीमारी / स्थिति | "स्थिति" when it means a state, "बीमारी" when it means an illness |
| allergy / allergen | एलर्जी | |
| criticality (allergy) / severity | ख़तरे का स्तर / गंभीरता | two different things: future risk vs how bad a reaction was |
| medication statement | मरीज़ द्वारा ली जा रही दवा | medicines the patient reports taking |
| medicine / medication | दवा | |
| prescription | पर्चा | ❓ Alternative: प्रिस्क्रिप्शन |
| dose / dosage | खुराक | |
| route (of a medicine) | देने का तरीका | ❓ e.g. मुँह से, नस में |
| frequency | कितनी बार | |
| dispense | दवा देना | |
| pharmacy | फ़ार्मेसी | |
| vitals | वाइटल्स | |
| blood pressure | रक्तचाप (BP) | |
| pulse | नब्ज़ | |
| temperature | तापमान | |
| respiratory rate | साँस की दर | |
| weight / height | वज़न / लंबाई | |
| blood group | ब्लड ग्रुप | |
| test (lab) | जाँच | |
| specimen / sample | सैंपल | |
| result | परिणाम | |
| report | रिपोर्ट | |
| observation | ऑब्ज़र्वेशन | ❓ CARE's recorded value (vital, lab value) |
| procedure | प्रक्रिया | |
| consent | सहमति | |
| treatment | इलाज | |
| history (medical) | हिस्ट्री | ❓ Alternative: पिछला रिकॉर्ड |
| alert (consciousness) | सचेत | upstream wrongly says चेतावनी (warning) |

## Billing

| English | Hindi | Note |
|---|---|---|
| bill / invoice | बिल | |
| payment | भुगतान | |
| amount | राशि | |
| account | खाता | |
| charge | शुल्क | |
| discount | छूट | |
| tax | टैक्स | |
| refund | रिफ़ंड | |
| due / balance | बकाया | |
| paid | भुगतान हो गया | |
| insurance | बीमा | |

## Screens and actions

| English | Hindi | Note |
|---|---|---|
| save | सेव करें | |
| cancel (an action) | रद्द करें | |
| cancelled (status) | रद्द | |
| submit | जमा करें | |
| delete / remove | हटाएँ | |
| edit | बदलें | |
| add | जोड़ें | |
| create | बनाएँ | |
| update | अपडेट करें | |
| search | खोजें | |
| select / choose | चुनें | |
| view | देखें | |
| close | बंद करें | |
| back / next | वापस / आगे | |
| confirm | पुष्टि करें | |
| continue | जारी रखें | |
| upload / download / print | अपलोड करें / डाउनलोड करें / प्रिंट करें | |
| filter | फ़िल्टर | |
| clear | साफ़ करें | |
| reset | रीसेट करें | |
| enter (a value) | दर्ज करें | |
| sign in / sign out | साइन इन करें / साइन आउट करें | |
| password | पासवर्ड | |
| settings | सेटिंग्स | |
| dashboard | डैशबोर्ड | |
| details | विवरण | |
| name / first / last name | नाम / पहला नाम / उपनाम | |
| date / date of birth / age | तारीख़ / जन्म तिथि / उम्र | |
| gender: male / female / transgender / other | पुरुष / महिला / ट्रांसजेंडर / अन्य | |
| phone number | फ़ोन नंबर | |
| address / PIN code / state / district | पता / पिन कोड / राज्य / ज़िला | |
| file / document | फ़ाइल / दस्तावेज़ | |
| note / notes | नोट / नोट्स | |
| form / questionnaire | फ़ॉर्म / प्रश्नावली | |
| tag | टैग | |
| status | स्थिति | |
| active / inactive | सक्रिय / निष्क्रिय | |
| draft | ड्राफ़्ट | |
| completed (status) / complete (button) | पूरा हुआ / पूरा करें | |
| pending | बाकी | |
| on hold | होल्ड पर | a medicine on hold: अस्थायी रूप से रोकी गई (paused, may restart; never "stopped") |
| stopped / discontinued | बंद की गई / बीच में बंद की गई | |
| physical examination (exam) | शारीरिक परीक्षण | not जाँच, which is a lab test |
| substitute (a medicine) | दूसरी दवा दें | |
| clinician abbreviations | STAT (तुरंत), PRN, SOS, DNR, LAMA, CBC | Latin letters, Hindi in brackets where useful, as IV/IM |
| in progress | जारी है | |
| required / optional | ज़रूरी / वैकल्पिक | |
| error | त्रुटि | |
| loading… | लोड हो रहा है… | |
| no X found | कोई X नहीं मिला | |
| X created / updated / deleted successfully | X बन गया / अपडेट हो गया / हट गया | gender of the verb follows X |
| failed to … | … नहीं हो सका | |
| are you sure? | क्या आप पक्का हैं? | |
| today / yesterday / tomorrow | आज / कल / कल | Hindi uses कल for both; the context tells them apart |
