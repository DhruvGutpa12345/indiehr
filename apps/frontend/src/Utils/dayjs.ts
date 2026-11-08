import dayjs from "dayjs";
import customParseFormat from "dayjs/plugin/customParseFormat";
import duration from "dayjs/plugin/duration";
import isBetween from "dayjs/plugin/isBetween";
import relativeTime from "dayjs/plugin/relativeTime";

import indiaDayjsFormat from "@/Utils/ihr/indiaDayjs"; // IndieHR P1-T7b: DD-MM-YYYY, 12-hour time

dayjs.extend(relativeTime);
dayjs.extend(duration);
dayjs.extend(customParseFormat);
dayjs.extend(isBetween);
dayjs.extend(indiaDayjsFormat); // IndieHR P1-T7b

export default dayjs;
