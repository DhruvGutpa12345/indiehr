// IndieHR (P1-T4b, ADR-009): idle time-out in the web app. Real input (keyboard, mouse, touch)
// counts as activity, shared by this hospital's tabs; background polling does not. A warning
// shows shortly before the limit, then the person is signed out at every hospital (owner's
// decision). While someone works without API calls, a heartbeat keeps the sign-in alive.
import { t } from "i18next";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { idleState } from "@/Utils/ihr/idleRules";
import {
  heartbeat,
  idleSeconds,
  idleSettings,
  recordActivity,
} from "@/Utils/ihr/staffSession";

const CHECK_EVERY_MS = 10_000;
const HEARTBEAT_EVERY_MS = 4 * 60_000;
const INPUT_EVENTS = [
  "keydown",
  "pointerdown",
  "pointermove",
  "wheel",
  "touchstart",
];

export function useIdleSignOut(enabled: boolean, onIdle: () => void) {
  const warningId = useRef<string | number | null>(null);
  const lastBeat = useRef(Date.now());

  useEffect(() => {
    if (!enabled) return;
    const settings = idleSettings();
    recordActivity(true);

    const onInput = () => {
      recordActivity();
      if (warningId.current !== null) {
        toast.dismiss(warningId.current);
        warningId.current = null;
      }
    };
    for (const name of INPUT_EVENTS) {
      window.addEventListener(name, onInput, { passive: true });
    }

    const timer = window.setInterval(() => {
      const idle = idleSeconds();
      const state = idleState(idle, settings);
      if (state === "expired") {
        window.clearInterval(timer);
        onIdle();
        return;
      }
      if (state === "warn" && warningId.current === null) {
        const minutes = Math.max(
          1,
          Math.ceil((settings.idleSeconds - idle) / 60),
        );
        warningId.current = toast.warning(t("ihr_idle_warning", { minutes }), {
          duration: Infinity,
          action: { label: t("ihr_stay_signed_in"), onClick: () => onInput() },
        });
      }
      if (state === "active" && warningId.current !== null) {
        toast.dismiss(warningId.current); // input in another tab of this hospital
        warningId.current = null;
      }
      if (
        state === "active" &&
        Date.now() - lastBeat.current > HEARTBEAT_EVERY_MS
      ) {
        lastBeat.current = Date.now();
        heartbeat();
      }
    }, CHECK_EVERY_MS);

    return () => {
      window.clearInterval(timer);
      for (const name of INPUT_EVENTS) {
        window.removeEventListener(name, onInput);
      }
    };
  }, [enabled, onIdle]);
}
