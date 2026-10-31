import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    open: false,
    // `API_BASE` is the relative "/api", so the real backend is reached by proxying
    // that prefix rather than by changing a base URL in the app. Same-origin in dev
    // means CORS never enters the picture and no credentials question arises.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // SSE must not be buffered: the chat streams token by token, and a proxy that
        // waits for the response to finish turns a live stream into one late blob.
        ws: false,
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              delete proxyRes.headers["content-length"];
            }
          });
        },
      },
    },
  },
});
