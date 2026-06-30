import type { CreateRunPayload, DifyConnectionConfigItem } from "../types";

type DifyConnectionPair = Pick<DifyConnectionConfigItem, "dify_base_url" | "dify_api_key">;

export function resolveDifyConnectionCredentials(
  form: Pick<CreateRunPayload, "dify_base_url" | "dify_api_key">,
  config: DifyConnectionPair | null = null
): { difyBaseUrl: string; difyApiKey: string } {
  const source = config ?? form;
  return {
    difyBaseUrl: source.dify_base_url.trim(),
    difyApiKey: source.dify_api_key.trim()
  };
}

export function applyDifyConnectionConfig(
  form: CreateRunPayload,
  config: DifyConnectionPair | null
): CreateRunPayload {
  if (!config) return form;
  return {
    ...form,
    dify_base_url: config.dify_base_url,
    dify_api_key: config.dify_api_key,
    dataset_id: "",
    embedding_model: "",
    rerank_model: ""
  };
}

export function hasCompleteDifyConnection(
  form: Pick<CreateRunPayload, "dify_base_url" | "dify_api_key">
): boolean {
  return Boolean(form.dify_base_url.trim()) && Boolean(form.dify_api_key.trim());
}

export function isSameDifyConnection(
  form: Pick<CreateRunPayload, "dify_base_url" | "dify_api_key">,
  config: DifyConnectionPair | null
): boolean {
  if (!config) return false;
  return (
    form.dify_base_url.trim() === config.dify_base_url.trim() &&
    form.dify_api_key.trim() === config.dify_api_key.trim()
  );
}

export function findMatchingDifyConnection(
  form: Pick<CreateRunPayload, "dify_base_url" | "dify_api_key">,
  configs: DifyConnectionConfigItem[]
): DifyConnectionConfigItem | null {
  return configs.find((config) => isSameDifyConnection(form, config)) || null;
}

export function formatDifyConnectionOption(config: DifyConnectionConfigItem): string {
  const url = config.dify_base_url.trim() || "(未填写地址)";
  const maskedKey = config.dify_api_key_masked || "****";
  return `${url} · ${maskedKey}`;
}
