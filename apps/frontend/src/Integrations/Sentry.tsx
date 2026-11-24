import careConfig from "@careConfig";
import { useEffect } from "react";

interface Props {
  disabled?: boolean;
}

export default function Sentry({ disabled }: Props) {
  useEffect(() => {
    if (disabled) return;
    if (!careConfig.sentry.dsn || !careConfig.sentry.environment) {
      console.error(
        "Sentry is not configured correctly. Please check your environment variables.",
      );
      return;
    }

    // IndieHR (P2-T0c): load only `init` (src/Utils/ihr/sentryInit.ts), not the whole package
    import("@/Utils/ihr/sentryInit").then(({ init }) => {
      init(careConfig.sentry);
    });
  }, [disabled]);

  return null;
}
