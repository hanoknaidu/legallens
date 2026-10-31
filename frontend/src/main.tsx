import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { USE_MOCKS } from "./lib/constants";
import "./styles/globals.css";

async function bootstrap() {
  // The mock layer sits at the network boundary, so no component knows whether the
  // data is real. It is selected by env rather than by `DEV` so that running against
  // the real backend is a flag, not an edit — see .env.development.
  if (USE_MOCKS) {
    const { worker } = await import("./mocks/browser");
    await worker.start({ onUnhandledRequest: "bypass", quiet: true });
  }

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void bootstrap();
