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

  // Tab 3
  createSession: () => request('/api/sessions', { method: 'POST' }),
  getSession: (id) => request(`/api/sessions/${id}`),
  sessionChat: (id, question) =>
    request(`/api/sessions/${id}/chat`, {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),

  // Tab 4
  classify: (question) =>
    request('/api/pipeline/classify', { method: 'POST', body: JSON.stringify({ question }) }),
  route: (intent) =>
    request('/api/pipeline/route', { method: 'POST', body: JSON.stringify({ intent }) }),
  search: (question, intent) =>
    request('/api/pipeline/search', {
      method: 'POST',
      body: JSON.stringify({ question, intent }),
    }),
  answer: (question, intent) =>
    request('/api/pipeline/answer', {
      method: 'POST',
      body: JSON.stringify({ question, intent }),
    }),

  // Tab 3 — Agent (autonomous loop via MongoDB MCP Server)
  agentScenarios: () => request('/api/agent/scenarios'),
  agentRun: (body) =>
    request('/api/agent/run', { method: 'POST', body: JSON.stringify(body) }),
};
