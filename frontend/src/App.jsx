import { useEffect, useState } from 'react';
import FlexibleSchema from './tabs/FlexibleSchema.jsx';
import ModelSwap from './tabs/ModelSwap.jsx';
import SessionMemory from './tabs/SessionMemory.jsx';
import IntentRouting from './tabs/IntentRouting.jsx';
import { api } from './api.js';

const TABS = [
  '01 · Schema Flexível',
  '02 · Model Swap',
  '03 · Session Memory',
  '04 · Intent + RAG',
];

export default function App() {
  const [selected, setSelected] = useState(0);

  // Result state lives HERE (lifted state): switching tabs or re-rendering
  // any component never wipes pipeline/chat results.
  const [schemaState, setSchemaState] = useState({ doc: null, flash: 0 });
  const [modelSwapState, setModelSwapState] = useState({ config: null, messages: [] });
  const [sessionState, setSessionState] = useState({
    sessionId: null,
    doc: null,
    lastTurn: 0,
  });
  const [pipelineState, setPipelineState] = useState({ question: '', steps: null });

  // cluster health — feeds the status pill and the stat bar
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const h = await api.health();
        if (alive) {
          setHealth(h);
          setHealthError(false);
        }
      } catch {
        if (alive) setHealthError(true);
      }
    };
    tick();
    const id = setInterval(tick, 10_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const panes = [
    <FlexibleSchema state={schemaState} setState={setSchemaState} />,
    <ModelSwap state={modelSwapState} setState={setModelSwapState} />,
    <SessionMemory state={sessionState} setState={setSessionState} />,
    <IntentRouting state={pipelineState} setState={setPipelineState} />,
  ];

  const counts = health?.counts ?? {};

  return (
    <>
      <nav className="top-nav">
        <div className="nav-inner">
          <span className="nav-logo">
            <span className="leaf">●</span> MongoDB Intelligence Layer
          </span>
          <div className="nav-pills">
            {TABS.map((name, i) => (
              <button
                key={name}
                className={`nav-pill ${i === selected ? 'active' : ''}`}
                onClick={() => setSelected(i)}
              >
                {name}
              </button>
            ))}
          </div>
          <span className="status-pill">
            <span className={`status-dot ${healthError || !health ? 'err' : 'ok'}`} />
            {healthError ? 'sem conexão' : health ? 'Atlas · ping ok' : 'conectando…'}
          </span>
        </div>
      </nav>

      <main className="content">
        <div className="hero-kicker">POC · AI Orchestration Layer</div>
        <h1 className="page-title">
          A camada de AI vive em <span>documentos</span>
        </h1>
        <p className="page-subtitle">
          Prompts, memória de sessão, roteamento de intents e configuração de modelos
          mudam na velocidade dos LLMs — em MongoDB isso é um simples update.
        </p>

        <div className="stat-bar">
          <div className="stat-item">
            <div className="stat-val accent">{counts.prompt_templates ?? '—'}</div>
            <div className="stat-label">prompt_templates</div>
          </div>
          <div className="stat-item">
            <div className="stat-val accent">{counts.intent_registry ?? '—'}</div>
            <div className="stat-label">intent_registry</div>
          </div>
          <div className="stat-item">
            <div className="stat-val accent">{counts.session_memory ?? '—'}</div>
            <div className="stat-label">session_memory</div>
          </div>
          <div className="stat-item">
            <div className="stat-val">200K</div>
            <div className="stat-label">produtos vetorizados</div>
          </div>
          <div className="stat-item">
            <div className="stat-val" style={{ fontSize: '1rem', lineHeight: '1.9' }}>
              {health?.primary_model ?? '—'}
            </div>
            <div className="stat-label">modelo primário ativo</div>
          </div>
        </div>

        {panes.map((pane, i) => (
          <div key={i} style={{ display: i === selected ? 'block' : 'none' }}>
            <div className={i === selected ? 'fade-in' : ''}>{pane}</div>
          </div>
        ))}
      </main>

      <footer className="app-footer">
        <p>MongoDB Atlas · POC.produtos_vector — autoEmbed voyage-4</p>
      </footer>
    </>
  );
}
