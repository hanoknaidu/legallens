import { http, HttpResponse, delay } from "msw";
import { matters } from "../fixtures/matters";

export const matterHandlers = [
  http.get("/api/matters", async () => {
    await delay(120);
    return HttpResponse.json(matters);
  }),
];
