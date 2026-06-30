const LEGACY_RUN_DIFY_URL_STORAGE_KEY = "dify-kb-eval:home:run-dify-url:v1";

export const RUN_DIFY_URL_STORAGE_KEY = "dify-kb-eval:verified-dify-url:v1";
export const RUN_DIFY_URL_CHANGED_EVENT = "dify-kb-eval:current-dify-url-changed";

export function readCurrentDifyUrl(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(RUN_DIFY_URL_STORAGE_KEY);
    return raw && raw.trim() ? raw.trim() : null;
  } catch {
    return null;
  }
}

export function writeCurrentDifyUrl(value: string | null) {
  if (typeof window === "undefined") return;
  const next = value?.trim() || "";
  try {
    if (next) {
      window.localStorage.setItem(RUN_DIFY_URL_STORAGE_KEY, next);
    } else {
      window.localStorage.removeItem(RUN_DIFY_URL_STORAGE_KEY);
    }
    window.localStorage.removeItem(LEGACY_RUN_DIFY_URL_STORAGE_KEY);
  } catch {
    // Quota / privacy-mode errors should not block the evaluation workflow.
  }
  window.dispatchEvent(new CustomEvent(RUN_DIFY_URL_CHANGED_EVENT, { detail: next || null }));
}
