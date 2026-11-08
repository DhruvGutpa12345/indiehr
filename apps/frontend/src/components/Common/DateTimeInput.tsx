// IndieHR P1-T7b: upstream's browser datetime-local box (mm/dd/yyyy or 24-hour, depending on
// the browser) is replaced by our DD-MM-YYYY + 12-hour AM/PM box. Same props, same ISO values.
import { IndiaDateTimeInput } from "@/components/ihr/IndiaDateTimeInput";

export const DateTimeInput = IndiaDateTimeInput;
