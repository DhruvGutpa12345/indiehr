// IndieHR (P1-T7b): a dayjs plugin that passes every format pattern through the owner's
// India date table (indiaDateFormats.ts). Registered once in src/Utils/dayjs.ts. dayjs is a
// single shared object, so files that import "dayjs" directly get it too: src/Utils/dayjs.ts
// is in the static import graph of src/index.tsx (index -> i18n -> Utils/request/query ->
// Utils/utils -> Utils/dayjs), so it runs before anything renders.
// `format()` with no pattern (ISO output) and patterns not in the table are untouched.
import type { PluginFunc } from "dayjs";

import { mapDayjsFormat } from "./indiaDateFormats";

const indiaDayjsFormat: PluginFunc = (_option, dayjsClass) => {
  const proto = dayjsClass.prototype as {
    format: (template?: string) => string;
    __ihrIndiaFormat?: boolean;
  };
  if (proto.__ihrIndiaFormat) return; // registered twice: wrap only once
  const originalFormat = proto.format;
  proto.format = function (this: unknown, template?: string) {
    return originalFormat.call(this, mapDayjsFormat(template));
  };
  proto.__ihrIndiaFormat = true;
};

export default indiaDayjsFormat;
