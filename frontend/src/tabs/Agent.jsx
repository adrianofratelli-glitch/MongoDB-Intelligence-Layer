import { useEffect, useRef, useState } from 'react';
import Badge from '@leafygreen-ui/badge';
import Banner from '@leafygreen-ui/banner';
import Button from '@leafygreen-ui/button';
import TextInput from '@leafygreen-ui/text-input';
import { api } from '../api.js';

// As 6 fases do loop agêntico (mesma narrativa do Perceive→Reason→Act→Store).
const PHASES = [
  { key: 'perceive', icon: '👁️', label: 'Perceber', sub: 'Usuário → Agente' },
  { key: 'retrieve', icon: '🔍', label: 'Recuperar', sub: 'Agente ↔ MongoDB' },
  { key: 'reason', icon: '🧠', label: 'Raciocinar', sub: 'Agente → LLM' },
  { key: 'act', icon: '⚡', label: 'Agir', sub: 'Agente → MongoDB' },
  { key: 'store', icon: '💾', label: 'Salvar', sub: 'Agente → MongoDB' },
  { key: 'loop', icon: '🔁', label: 'Repetir', sub: 'Próximo turno' },
];

const PHASE_CAPTION = {
  perceive: 'O agente recebe a mensagem do cliente — início do turno.',
  retrieve: 'O agente consulta o MongoDB pelo MCP Server (find / $vectorSearch).',
  reason: 'O Claude raciocina sobre qual será a próxima ação.',
  act: 'O agente grava a decisão no MongoDB (update do pedido).',
  store: 'O turno é salvo em agent_sessions — memória persistida da conversa.',
  loop: 'Turno concluído. O agente está pronto para continuar a conversa.',
};

const short = (obj, n = 160) => {
  const s = typeof obj === 'string' ? obj : JSON.stringify(obj);
  return s.length > n ? s.slice(0, n) + '…' : s;
};

const opTarget = (args = {}) => `${args.database ?? '?'}.${args.collection ?? '?'}`;
const opDetail = (args = {}) => {
  if (args.pipeline) return short(args.pipeline, 200);
  if (args.filter) return 'filter: ' + short(args.filter, 140);
  if (args.update) return 'update: ' + short(args.update, 140);
  return '';
};

export default function Agent({ state, setState }) {
  const { run, step, iteration, conversationId, turns = [] } = state;
  const [scenarios, setScenarios] = useState([]);
  const [tools, setTools] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [walk, setWalk] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    api.agentScenarios().then((d) => setScenarios(d.scenarios)).catch(() => {});
    api.agentTools().then((d) => setTools(d.tools)).catch(() => {});
  }, []);

  const events = run?.trace ?? [];
  const lastStep = events.length - 1;
  const visible = step >= 0 ? events.slice(0, step + 1) : [];
  const current = step >= 0 ? events[step] : null;
  const activePhase = current?.phase;

  // auto-avanço do replay (Pausar/Continuar e Tour guiado)
  useEffect(() => {
    clearTimeout(timerRef.current);
    if (playing && step < lastStep) {
      timerRef.current = setTimeout(
        () => setState((s) => ({ ...s, step: s.step + 1 })),
        walk ? 1700 : 850,
      );
    } else if (playing && step >= lastStep) {
      setPlaying(false);
      setWalk(false);
    }
    return () => clearTimeout(timerRef.current);
  }, [playing, step, lastStep, walk]);

  const runScenario = async (payload) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setPlaying(false);
    setWalk(false);
    const convId = conversationId ?? `conv_${Date.now()}`;
    try {
      const result = await api.agentRun({ ...payload, conversation_id: convId });
      setState((s) => ({
        ...s,
        conversationId: result.conversation_id ?? convId,
        run: result,
        step: 0,
        iteration: (s.iteration ?? 0) + 1,
        turns: [
          ...(s.turns ?? []),
          { role: 'user', text: result.user_message },
          { role: 'agent', text: result.answer },
        ],
      }));
      setPlaying(true); // auto-reproduz o trace
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const send = () => {
    if (!input.trim()) return;
    runScenario({ message: input.trim() });
    setInput('');
  };

  // Reset: limpa só o replay atual; a conversa (memória) permanece
  const reset = () => {
    clearTimeout(timerRef.current);
    setPlaying(false);
    setWalk(false);
    setState((s) => ({ ...s, run: null, step: -1 }));
  };

  // Nova conversa: sessão nova — zera memória e replay
  const newConversation = () => {
    clearTimeout(timerRef.current);
    setPlaying(false);
    setWalk(false);
    setState({ run: null, step: -1, iteration: 0, conversationId: null, turns: [] });
  };

  const go = (n) => {
    setPlaying(false);
    setWalk(false);
    setState((s) => ({ ...s, step: Math.max(0, Math.min(lastStep, n)) }));
  };

  // métricas acumuladas até o passo atual (sensação "ao vivo")
  const calls = visible.filter((e) => e.kind === 'tool_call');
  const metrics = {
    reads: calls.filter((e) => e.phase === 'retrieve').length,
    writes: calls.filter((e) => e.phase === 'act' || e.phase === 'store').length,
    tools: calls.length,
    latency: visible.reduce((a, e) => a + (e.latency_ms || 0), 0),
  };

  const ops = visible.filter((e) => e.kind === 'tool_call');
  const reasonings = visible.filter((e) => e.kind === 'reasoning');
  const usedTools = new Set(ops.map((e) => e.tool));
  const thought = [...reasonings].reverse()[0]?.text;

  return (
    <div className="stack">
      {error && (
        <Banner variant="warning" darkMode>
          {error}
        </Banner>
      )}

      <div className="card neutral agent-shell">
        {/* cabeçalho + controles */}
        <div className="agent-head">
          <div className="agent-title">
            <span className="agent-emoji">🤖</span>
            <div>
              <div className="card-title">Agente de Suporte</div>
              <div className="dim mono" style={{ fontSize: 11 }}>Powered by MongoDB MCP Server</div>
            </div>
          </div>
          <div className="agent-controls">
            <span className="dim mono" style={{ fontSize: 12 }}>
              Iteração <span className="accent-num">{iteration ?? 0}</span>
            </span>
            <Button size="xsmall" darkMode onClick={reset} disabled={!run}>Reset</Button>
            <Button size="xsmall" darkMode onClick={() => go(step - 1)} disabled={!run || step <= 0}>◀ Anterior</Button>
            <Button size="xsmall" darkMode onClick={() => go(step + 1)} disabled={!run || step >= lastStep}>Próximo ▶</Button>
            <Button
              size="xsmall"
              darkMode
              onClick={() => { setWalk(false); setPlaying((p) => !p); }}
              disabled={!run || step >= lastStep}
            >
              {playing ? '⏸ Pausar' : '▶ Continuar'}
            </Button>
            <Button
              size="xsmall"
              darkMode
              variant="primary"
              onClick={() => { setState((s) => ({ ...s, step: 0 })); setWalk(true); setPlaying(true); }}
              disabled={!run}
            >
              📖 Tour guiado
            </Button>
          </div>
        </div>

        {/* ferramentas MCP disponíveis + memória da sessão */}
        <div className="agent-meta">
          <div className="tools-row">
            <span className="dim mono">Ferramentas MCP:</span>
            {tools.map((t) => (
              <span
                key={t.name}
                className={`tool-pill ${t.kind} ${usedTools.has(t.name) ? 'used' : ''}`}
                title={usedTools.has(t.name) ? 'usada neste turno' : 'disponível'}
              >
                {t.name}
              </span>
            ))}
          </div>
          <div className="memory-pill" title="POC.agent_sessions">
            🧠 <span className="accent-num">{turns.length}</span> mensagens na memória
            <Button size="xsmall" darkMode onClick={newConversation} disabled={busy}>Nova conversa</Button>
          </div>
        </div>

        {/* strip das 6 fases */}
        <div className="phase-strip">
          {PHASES.map((p, i) => (
            <div key={p.key} className="phase-wrap">
              <div className={`phase-card ${activePhase === p.key ? 'active' : ''} ${visible.some((e) => e.phase === p.key) ? 'done' : ''}`}>
                <div className="phase-icon">{p.icon}</div>
                <div className="phase-label">{p.label}</div>
                <div className="phase-sub">{p.sub}</div>
              </div>
              {i < PHASES.length - 1 && (
                <span className="phase-arrow">{i === PHASES.length - 2 ? '↩' : '→'}</span>
              )}
            </div>
          ))}
        </div>

        {walk && current && (
          <div className="walk-caption">
            <span className="mono">{PHASE_CAPTION[activePhase]}</span>
          </div>
        )}

        {/* 3 colunas */}
        <div className="agent-grid">
          <div className="agent-col">
            <div className="col-head"><span className="leaf">🍃</span> MongoDB</div>
            {ops.length === 0 && <div className="dim">Clique numa fase ou envie uma mensagem para começar…</div>}
            {ops.map((e, i) => (
              <div key={i} className={`op-item ${e.phase === 'act' || e.phase === 'store' ? 'write' : 'read'} ${current === e ? 'pulse' : ''}`}>
                <div className="op-head">
                  <Badge variant={e.phase === 'act' || e.phase === 'store' ? 'yellow' : 'green'}>{e.tool}</Badge>
                  <span className="mono dim">{opTarget(e.args)}</span>
                  {e.latency_ms != null && <span className="mono dim">{e.latency_ms} ms</span>}
                </div>
                {opDetail(e.args) && <div className="op-detail mono">{opDetail(e.args)}</div>}
                <div className="op-result mono">{short(e.result, 180)}</div>
              </div>
            ))}
          </div>

          <div className="agent-col">
            <div className="col-head"><span className="brain">🧠</span> Raciocínio do LLM
              {run && <Badge variant="blue" className="ml">{run.model}</Badge>}
            </div>
            {reasonings.length === 0 && <div className="dim">O raciocínio aparece na fase "Raciocinar".</div>}
            {reasonings.map((e, i) => (
              <div key={i} className={`reason-item ${current === e ? 'pulse' : ''}`}>
                <div className="reason-text">{e.text}</div>
                <div className="reason-meta mono dim">{e.tokens} tokens · {e.latency_ms} ms</div>
              </div>
            ))}
          </div>

          <div className="agent-col">
            <div className="col-head"><span className="agent-emoji">🤖</span> Agente</div>
            <div className="thought-box">
              <div className="thought-label mono">💭 Pensamento atual</div>
              <div className="thought-text">{thought ?? 'Aguardando entrada do usuário…'}</div>
            </div>
            <div className="agent-chat">
              {turns.length === 0 && <div className="dim">Sem mensagens ainda. Experimente uma sugestão abaixo.</div>}
              {turns.map((t, i) => (
                <div key={i} className={`chat-msg ${t.role === 'user' ? 'user' : 'assistant'}`}>{t.text}</div>
              ))}
              {busy && (
                <div className="row"><div className="spinner" /> <span className="dim">o agente está trabalhando…</span></div>
              )}
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <div style={{ flex: 1 }}>
                <TextInput
                  darkMode
                  aria-label="mensagem"
                  placeholder="Digite uma mensagem ou clique num cenário"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && send()}
                />
              </div>
              <Button darkMode variant="primary" onClick={send} disabled={busy}>
                {busy ? 'Rodando…' : 'Enviar'}
              </Button>
            </div>
            <div className="chip-row">
              {scenarios.map((s) => (
                <button key={s.key} className="agent-chip" onClick={() => runScenario({ scenario: s.key })} disabled={busy}>
                  {s.label}
                </button>
              ))}
              <button
                className="agent-chip memory"
                onClick={() => runScenario({ message: 'Pode consolidar todas as perguntas que eu fiz nesta sessão?' })}
                disabled={busy || turns.length === 0}
                title="Demonstra a persistência: o agente faz um find em agent_sessions"
              >
                🧠 Consolidar minhas perguntas
              </button>
            </div>
          </div>
        </div>

        {/* métricas */}
        <div className="metric-bar">
          <div className="metric"><div className="metric-val">{metrics.reads}</div><div className="metric-label">reads</div></div>
          <div className="metric"><div className="metric-val">{metrics.writes}</div><div className="metric-label">writes</div></div>
          <div className="metric"><div className="metric-val">{metrics.tools}</div><div className="metric-label">tools usadas</div></div>
          <div className="metric"><div className="metric-val">{metrics.latency}<span className="metric-unit">ms</span></div><div className="metric-label">latência</div></div>
        </div>
      </div>
    </div>
  );
}
