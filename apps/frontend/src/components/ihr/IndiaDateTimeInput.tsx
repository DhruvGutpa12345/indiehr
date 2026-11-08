// IndieHR (P1-T7b): date-and-time box in the owner's India style, used in place of the
// browser's datetime-local box (which follows the browser's language, e.g. mm/dd/yyyy and a
// 24-hour clock). Staff type the date as DD-MM-YYYY and the time as hh:mm, then pick AM or PM
// (a new box has no AM/PM until one is chosen; a 24-hour time such as 21:30 sets it).
// Same props and value contract as upstream's DateTimeInput: `value` is an ISO string (or
// empty), `onDateChange` gets an ISO string, or undefined while the box is empty, half typed,
// impossible or missing AM/PM. Outside min/max ("yyyy-MM-ddTHH:mm", local time) the value is
// still sent with a red message, as the browser box did.
// Accessible name: the caller's label (FormControl passes `id`, so <label for> names the date
// box; or `aria-labelledby`); the generic "Date (DD-MM-YYYY)" is only a fallback. The format is
// read out through aria-describedby.
// The rules live in Utils/ihr/indiaDateTimeInput.ts (unit-tested).
import * as React from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { Input } from "@/components/ui/input";

import {
  DateTimeDraft,
  EMPTY_DRAFT,
  MeridiemChoice,
  autoColonTime,
  autoDashDate,
  describeBound,
  draftFromValue,
  evaluateDraft,
  sameShownValue,
  withTypedTime,
} from "@/Utils/ihr/indiaDateTimeInput";

type IndiaDateTimeInputProps = React.ComponentProps<typeof Input> & {
  onDateChange: (val: string | undefined) => void;
} & React.InputHTMLAttributes<HTMLInputElement>;

const isBlank = (v: unknown) => v === undefined || v === null || v === "";
const sameValue = (a: unknown, b: unknown) =>
  (isBlank(a) && isBlank(b)) || a === b;

// Fixed text of the owner's format (not words to translate).
const DATE_PLACEHOLDER = "DD-MM-YYYY";
const TIME_PLACEHOLDER = "hh:mm";
const MERIDIEMS: MeridiemChoice[] = ["AM", "PM"];
const NO_MERIDIEM_LABEL = "--";
const SELECT_CLASS =
  "h-9 rounded-md border border-gray-300 bg-white px-2 text-base shadow-xs focus:border-primary-500 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm";

const MESSAGES: Record<string, string> = {
  incomplete: "ihr_datetime_incomplete",
  invalid_date: "ihr_datetime_invalid_date",
  invalid_time: "ihr_datetime_invalid_time",
  choose_am_pm: "ihr_datetime_choose_am_pm",
  before_min: "ihr_datetime_before_min",
  after_max: "ihr_datetime_after_max",
};

export function IndiaDateTimeInput(props: IndiaDateTimeInputProps) {
  const { t } = useTranslation();
  const { value, onDateChange, min, max, disabled, readOnly } = props;
  const reactId = React.useId();
  const dateId = props.id ?? `${reactId}-date`;
  const messageId = `${reactId}-message`;
  const dateHintId = `${reactId}-date-hint`;
  const timeHintId = `${reactId}-time-hint`;
  const labelledBy = props["aria-labelledby"];
  // Only name the date box ourselves when nothing outside does.
  const namedOutside = Boolean(props.id || labelledBy);

  const [draft, setDraft] = React.useState<DateTimeDraft>(
    () => draftFromValue(value) ?? EMPTY_DRAFT,
  );
  const [touched, setTouched] = React.useState(false);
  // The last value we sent up, and the last `value` prop seen. Only a real change of the
  // prop from outside may replace what the user is typing: not our own value coming back,
  // not a parent that ignores `undefined`, not a parent that passes a fresh timestamp on
  // every render (compared to the minute), and never while the user is in the box.
  const lastSent = React.useRef<unknown>(value);
  const lastProp = React.useRef<unknown>(value);
  const focused = React.useRef(false);

  React.useEffect(() => {
    if (sameShownValue(value, lastProp.current)) return;
    lastProp.current = value;
    if (focused.current) return;
    if (sameShownValue(value, lastSent.current)) return;
    lastSent.current = value;
    const fromValue = draftFromValue(value);
    if (fromValue) {
      setDraft(fromValue);
    } else if (isBlank(value)) {
      // Cleared from outside (e.g. a form reset): empty a complete box, keep half-typed text.
      setDraft((d) => (evaluateDraft(d).iso ? EMPTY_DRAFT : d));
    }
  }, [value]);

  const result = evaluateDraft(draft, { min, max });

  const update = (next: DateTimeDraft) => {
    setDraft(next);
    const outcome = evaluateDraft(next, { min, max });
    // Sent for "ok" and also out of range (shown in red), never for blank/half-typed/impossible.
    const out = outcome.iso;
    if (sameValue(out, lastSent.current)) return;
    lastSent.current = out;
    onDateChange(out);
  };

  const showMessage =
    result.status !== "ok" &&
    result.status !== "empty" &&
    (touched ||
      (result.status !== "incomplete" && result.status !== "choose_am_pm"));
  const bound =
    result.status === "before_min"
      ? describeBound(min)
      : result.status === "after_max"
        ? describeBound(max)
        : "";
  const describedBy = (hintId: string) =>
    [props["aria-describedby"], hintId, showMessage ? messageId : undefined]
      .filter(Boolean)
      .join(" ");
  const invalid = showMessage || props["aria-invalid"] === true;

  return (
    <div
      className={cn("space-y-1", props.className)}
      onFocus={() => {
        focused.current = true;
      }}
      onBlur={(e) => {
        // Leaving the whole group (not moving between its boxes) counts as touched.
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
          focused.current = false;
          setTouched(true);
          props.onBlur?.(e as unknown as React.FocusEvent<HTMLInputElement>);
        }
      }}
    >
      <div
        role="group"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : t("ihr_datetime_group")}
        className="flex flex-wrap items-center gap-2"
      >
        <Input
          ref={props.ref}
          id={dateId}
          name={props.name}
          type="text"
          inputMode="numeric"
          autoComplete="off"
          placeholder={DATE_PLACEHOLDER}
          aria-labelledby={labelledBy}
          aria-label={namedOutside ? undefined : t("ihr_datetime_date")}
          aria-describedby={describedBy(dateHintId)}
          aria-invalid={invalid || undefined}
          required={props.required}
          disabled={disabled}
          readOnly={readOnly}
          maxLength={10}
          className="w-36"
          value={draft.date}
          onChange={(e) =>
            update({ ...draft, date: autoDashDate(e.target.value) })
          }
        />
        <Input
          type="text"
          inputMode="numeric"
          autoComplete="off"
          placeholder={TIME_PLACEHOLDER}
          aria-label={t("ihr_datetime_time")}
          aria-describedby={describedBy(timeHintId)}
          aria-invalid={invalid || undefined}
          disabled={disabled}
          readOnly={readOnly}
          maxLength={5}
          className="w-24"
          value={draft.time}
          onChange={(e) =>
            update(withTypedTime(draft, autoColonTime(e.target.value)))
          }
        />
        <select
          aria-label={t("ihr_datetime_am_pm")}
          aria-describedby={showMessage ? messageId : undefined}
          aria-invalid={
            (showMessage && result.status === "choose_am_pm") || undefined
          }
          disabled={disabled || readOnly}
          className={SELECT_CLASS}
          value={draft.meridiem}
          onChange={(e) =>
            update({
              ...draft,
              meridiem: e.target.value as MeridiemChoice,
              meridiemFromTime: false,
            })
          }
        >
          <option value="" disabled>
            {NO_MERIDIEM_LABEL}
          </option>
          {MERIDIEMS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <span id={dateHintId} className="sr-only">
          {DATE_PLACEHOLDER}
        </span>
        <span id={timeHintId} className="sr-only">
          {t("ihr_datetime_time_hint")}
        </span>
      </div>
      {showMessage && (
        <p id={messageId} className="text-sm text-red-600" aria-live="polite">
          {t(MESSAGES[result.status], { when: bound })}
        </p>
      )}
    </div>
  );
}
