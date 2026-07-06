// HTTP client. Backend errors arrive as {error: {kind, message}} (503) and
// become ApiError — the UI shows them in a yellow Banner, never a stack trace.

export class ApiError extends Error {
  constructor(kind, message) {
    super(message);
    this.kind = kind;
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch {
    throw new ApiError('rede', 'Backend não respondeu. O FastAPI está rodando na porta 8000?');
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = body.error || {};
    throw new ApiError(err.kind || 'erro', err.message || `Erro HTTP ${res.status}`);
  }
  return body;
}

export const api = {
  health: () => request('/api/health'),

  // Tab 1
  listTemplates: () => request('/api/templates'),
  getTemplate: (id) => request(`/api/templates/${id}`),
  addVariant: (id, modelName) =>
    request(`/api/templates/${id}/variant`, {
      method: 'POST',
      body: JSON.stringify({ model_name: modelName }),
    }),
  removeVariant: (id, modelName) =>
    request(`/api/templates/${id}/variant/${modelName}`, { method: 'DELETE' }),

  // Tab 2
  getModelConfig: () => request('/api/model-config'),
  swapModels: () => request('/api/model-config/swap', { method: 'POST' }),
  quickChat: (question) =>
    request('/api/chat/quick', { method: 'POST', body: JSON.stringify({ question }) }),

  // Tab 3 — Agent (autonomous loop via MongoDB MCP Server)
  users: () => request('/api/users'),
  agentScenarios: () => request('/api/agent/scenarios'),
  agentPlaylist: () => request('/api/agent/playlist'),
  agentTools: () => request('/api/agent/tools'),
  agentRun: (body) =>
    request('/api/agent/run', { method: 'POST', body: JSON.stringify(body) }),

  // Intelligence features — cache, memory, guardrails (inspect / reset)
  cacheInspect: () => request('/api/cache'),
  cacheClear: () => request('/api/cache', { method: 'DELETE' }),
  memoryInspect: (userKey) => request(`/api/memory/${encodeURIComponent(userKey)}`),
  memoryClear: (userKey) =>
    request(`/api/memory/${encodeURIComponent(userKey)}`, { method: 'DELETE' }),
  memoryShort: (conversationId) =>
    request(`/api/memory-short/${encodeURIComponent(conversationId)}`),
  guardrailsPolicy: () => request('/api/guardrails/policy'),
  guardrailsRules: (area) =>
    request(`/api/guardrails/rules${area ? `?area=${encodeURIComponent(area)}` : ''}`),
  guardrailsEvents: () => request('/api/guardrails/events'),
};
