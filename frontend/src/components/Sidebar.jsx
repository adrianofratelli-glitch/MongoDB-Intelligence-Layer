import { useEffect, useState } from 'react';
import Badge from '@leafygreen-ui/badge';
import { api } from '../api.js';

const COLLECTIONS = ['prompt_templates', 'model_config', 'intent_registry', 'session_memory'];

export default function Sidebar() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const h = await api.health();
        if (alive) {
          setHealth(h);
          setError(false);
        }
      } catch {
        if (alive) setError(true);
      }
    };
    tick();
    const id = setInterval(tick, 10_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <aside className="sidebar">
      <div className="logo">
        <span className="leaf">●</span> MongoDB
        <br />
        Intelligence Layer
      </div>

      <div className="sidebar-section">
        <div className="sidebar-label">Cluster</div>
        <div className="sidebar-row">
          <span>
            <span className={`status-dot ${error || !health ? 'err' : 'ok'}`} />
            {error ? 'sem conexão' : health ? 'conectado (ping ok)' : 'conectando…'}
          </span>
        </div>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-label">ai_brain — collections</div>
        {COLLECTIONS.map((c) => (
          <div className="sidebar-row" key={c}>
            <span className="mono" style={{ fontSize: 12 }}>
              {c}
            </span>
            <span className="count">{health?.counts?.[c] ?? '—'}</span>
          </div>
        ))}
      </div>

      <div className="sidebar-section">
        <div className="sidebar-label">Modelo primário ativo</div>
        {health ? (
          <Badge variant={health.primary_model.includes('sonnet') ? 'blue' : 'yellow'}>
            {health.primary_model}
          </Badge>
        ) : (
          <span className="dim">—</span>
        )}
        {health && (
          <div className="dim" style={{ fontSize: 12 }}>
            fallback: <span className="mono">{health.fallback_model}</span>
          </div>
        )}
      </div>

      <div style={{ marginTop: 'auto' }} className="dim">
        Atlas · cluster Inter
        <br />
        POC.produtos_vector — 200K docs
      </div>
    </aside>
  );
}
