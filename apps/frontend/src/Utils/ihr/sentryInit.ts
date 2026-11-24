// IndieHR (P2-T0c): the only piece of @sentry/browser the app loads. A dynamic import of the
// whole package (`import("@sentry/browser")`) keeps every export, including
// diagnoseSdkConnectivity with its hard-coded *.ingest.sentry.io address, which our bundle
// egress check refuses. A named re-export lets the bundler keep `init` alone.
export { init } from "@sentry/browser";
