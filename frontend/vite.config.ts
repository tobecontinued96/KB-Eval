import { readFileSync } from "node:fs";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const packageJson = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf-8")
) as { version?: string };
const appVersion = packageJson.version?.trim() || "0.0.0";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiProxyTarget = (env.VITE_API_BASE_URL || "http://127.0.0.1:8200").replace(/\/$/, "");

  const devPort = Number(env.DEV_PORT) || 5598;

  return {
    plugins: [react()],
    define: {
      "import.meta.env.VITE_APP_VERSION": JSON.stringify(appVersion)
    },
    server: {
      host: "0.0.0.0",
      port: devPort,
      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true
        }
      }
    }
  };
});
