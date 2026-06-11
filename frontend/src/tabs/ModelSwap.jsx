import { useEffect, useState } from 'react';
import Card from '@leafygreen-ui/card';
import Badge from '@leafygreen-ui/badge';
import Banner from '@leafygreen-ui/banner';
import Button from '@leafygreen-ui/button';
import TextInput from '@leafygreen-ui/text-input';
import JsonViewer from '../components/JsonViewer.jsx';
import { api } from '../api.js';

const modelBadge = (model) => (model?.includes('sonnet') ? 'blue' : 'yellow');

export default function ModelSwap({ state, setState }) {
  const { config, messages } = state;
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [swapping, setSwapping] = useState(false);
  const [error, setError] = useState(null);
  const [flash, setFlash] = useState(0);

  const loadConfig = async () => {
    try {
      const c = await api.getModelConfig();
      setState((s) => ({ ...s, config: c }));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    if (!config) loadConfig();
  }, []);

  const swap = async () => {
    setSwapping(true);
    setError(null);
    try {
      const c = await api.swapModels();
      setState((s) => ({ ...s, config: c }));
      setFlash((f) => f + 1);
    } catch (e) {
      setError(e.message);
    } finally {
      setSwapping(false);
    }
  };

  const ask = async () => {
    if (!question.trim() || busy) return;
    const q = question.trim();
    setBusy(true);
    setError(null);
    setState((s) => ({ ...s, messages: [...s.messages, { role: 'user', text: q }] }));
    setQuestion('');
    try {
      const r = await api.quickChat(q);
      setState((s) => ({
        ...s,
        messages: [...s.messages, { role: 'assistant', text: r.text, meta: r }],
      }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      {error && (
        <Banner variant="warning" darkMode>
          {error}
        </Banner>
      )}

      <div className="two-col">
        <Card darkMode>
          <div className="card-header">
            <span className="card-title">ai_brain.model_config — lido a cada request</span>
            {config && <Badge variant={modelBadge(config.primary?.model)}>{config.primary?.model}</Badge>}
          </div>
          <div className="row" style={{ marginBottom: 12 }}>
            <Button darkMode variant="primary" onClick={swap} disabled={swapping || !config}>
              {swapping ? 'update_one no Atlas…' : 'Trocar primary: Sonnet ↔ Haiku'}
            </Button>
            <span className="dim">zero restart · zero deploy</span>
          </div>
          {config ? (
            <JsonViewer doc={config} flashKey={flash} />
          ) : (
            <div className="dim">carregando…</div>
          )}
        </Card>

        <Card darkMode>
          <div className="card-header">
            <span className="card-title">Mini-chat — quem responde é o doc</span>
          </div>
          <div className="chat-box" style={{ minHeight: 220 }}>
            {messages.length === 0 && (
              <div className="dim">
                Pergunte algo, troque o primary no documento e repita a mesma pergunta — o
                badge do modelo muda sem reiniciar nada.
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i}>
                <div className={`chat-msg ${m.role}`}>{m.text}</div>
                {m.meta && (
                  <div className="chat-meta">
                    <Badge variant={modelBadge(m.meta.model)}>{m.meta.model}</Badge>
                    <span className="dim mono">{m.meta.latency_ms} ms</span>
                    <span className="dim mono">
                      {m.meta.input_tokens}→{m.meta.output_tokens} tokens
                    </span>
                    {m.meta.route === 'fallback' && <Badge variant="red">fallback</Badge>}
                  </div>
                )}
              </div>
            ))}
            {busy && <div className="row"><div className="spinner" /> <span className="dim">chamando o modelo configurado…</span></div>}
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <div style={{ flex: 1 }}>
              <TextInput
                darkMode
                aria-label="pergunta"
                placeholder="ex.: qual a capital da Austrália?"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && ask()}
              />
            </div>
            <Button darkMode variant="primary" onClick={ask} disabled={busy}>
              Enviar
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
