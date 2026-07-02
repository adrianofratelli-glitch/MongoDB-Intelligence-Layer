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
  if (obj == null) return '';
  const s = typeof obj === 'string' ? obj : JSON.stringify(obj);
  if (s == null) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
};

const opTarget = (args = {}) => `${args.database ?? '?'}.${args.collection ?? '?'}`;
const opDetail = (args = {}) => {
  if (args.pipeline) return short(args.pipeline, 200);
  if (args.filter) return 'filter: ' + short(args.filter, 140);
  if (args.update) return 'update: ' + short(args.update, 140);
  if (args.query) return 'query: ' + short(args.query, 140);
  return '';
};

// user_key estável: "Nova conversa" zera a memória de curto prazo (agent_sessions),
// mas a de longo prazo (agent_memory) persiste sob esta mesma chave.
const USER_KEY = 'cliente-demo';

export default function Agent({ state, setState }) {
  const { run, step, iteration, conversationId, turns = [] } = state;
  const [scenarios, setScenarios] = useState([]);
  const [tools, setTools] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [walk, setWalk] = useState(false);
  const [showInspector, setShowInspector] = useState(false);
  const [playlist, setPlaylist] = useState([]);
  const [demo, setDemo] = useState({ active: false, idx: -1, paused: false });
  const timerRef = useRef(null);
  const demoTimerRef = useRef(null); // separado do replay: não é limpo pelo cleanup do replay
  // refs read inside the replay-end effect (avoid stale closures on chaining)
  const demoRef = useRef(demo);
  const playlistRef = useRef(playlist);
  useEffect(() => { demoRef.current = demo; }, [demo]);
  useEffect(() => { playlistRef.current = playlist; }, [playlist]);

  useEffect(() => {
    api.agentScenarios().then((d) => setScenarios(d.scenarios)).catch(() => {});
    api.agentPlaylist().then((d) => setPlaylist(d.playlist)).catch(() => {});
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
      // demo automática: ao terminar o replay de um script, toca o próximo —
      // exceto se estiver pausada (aí o usuário explora e retoma quando quiser).
      // Usa demoTimerRef (não timerRef) para o cleanup deste efeito não cancelá-lo.
      if (demoRef.current.active && !demoRef.current.paused) {
        clearTimeout(demoTimerRef.current);
        demoTimerRef.current = setTimeout(advanceDemo, 1500);
      }
    }
    return () => clearTimeout(timerRef.current);
  }, [playing, step, lastStep, walk]);

  const advanceDemo = () => {
    const next = demoRef.current.idx + 1;
    if (demoRef.current.active && next < playlistRef.current.length) {
      setDemo({ active: true, idx: next, paused: false });
      runScenario({ message: playlistRef.current[next].message });
    } else {
      setDemo({ active: false, idx: -1, paused: false });
    }
  };

  const startDemo = () => {
    if (busy || !playlist.length) return;
    setDemo({ active: true, idx: 0, paused: false });
    runScenario({ message: playlist[0].message });
  };

  // Pausar: congela a demo onde está — o replay para, o avanço é cancelado, e o
  // usuário pode explorar tudo (trace, ops, painéis) manualmente.
  const pauseDemo = () => {
    clearTimeout(demoTimerRef.current);
    setPlaying(false);
    setDemo((d) => ({ ...d, paused: true }));
  };

  // Continuar: retoma de onde parou. Se o replay do script atual não acabou,
  // segue reproduzindo; se já acabou, avança para o próximo script.
  const resumeDemo = () => {
    setDemo((d) => ({ ...d, paused: false }));
    if (step >= lastStep) advanceDemo();
    else setPlaying(true);
  };

  // Encerrar de vez (usado no reset/erro/nova conversa e no botão ⏹).
  const stopDemo = () => {
    clearTimeout(timerRef.current);
    clearTimeout(demoTimerRef.current);
    setDemo({ active: false, idx: -1, paused: false });
    setPlaying(false);
  };

  const runScenario = async (payload) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setPlaying(false);
    setWalk(false);
    const convId = conversationId ?? `conv_${Date.now()}`;
    try {
      const result = await api.agentRun({ ...payload, conversation_id: convId, user_key: USER_KEY });
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
      setDemo({ active: false, idx: -1, paused: false }); // interrompe a demo se um script falhar
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
    clearTimeout(demoTimerRef.current);
    setPlaying(false);
    setWalk(false);
    setDemo({ active: false, idx: -1, paused: false });
    setState((s) => ({ ...s, run: null, step: -1 }));
  };

  // Nova conversa: sessão nova — zera memória e replay
  const newConversation = () => {
    clearTimeout(timerRef.current);
    clearTimeout(demoTimerRef.current);
    setPlaying(false);
    setWalk(false);
    setDemo({ active: false, idx: -1, paused: false });
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
            <Button size="xsmall" darkMode onClick={() => setShowInspector((v) => !v)}>
              {showInspector ? '🔎 Ocultar dados' : '🔎 Inspecionar MongoDB'}
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

        {/* demo automática: 10 scripts variados tocados em sequência */}
        <div className="demo-bar">
          {!demo.active ? (
            <button className="demo-btn" onClick={startDemo} disabled={busy || !playlist.length}>
              ▶ Demo automática · {playlist.length} scripts
            </button>
          ) : demo.paused ? (
            <>
              <button className="demo-btn" onClick={resumeDemo} disabled={busy}>▶ Continuar demo</button>
              <button className="demo-btn stop" onClick={stopDemo}>⏹ Encerrar demo</button>
            </>
          ) : (
            <button className="demo-btn pause" onClick={pauseDemo}>⏸ Pausar demo</button>
          )}
          {demo.active && (
            <span className="demo-now mono">
              {demo.idx + 1}/{playlist.length} · {playlist[demo.idx]?.label ?? ''}
              {demo.paused && <span className="demo-paused"> · pausado (explore à vontade)</span>}
            </span>
          )}
          <div className="demo-track">
            {playlist.map((it, i) => (
              <span
                key={it.key}
                className={`demo-dot badge-${it.badge} ${demo.active && i < demo.idx ? 'done' : ''} ${demo.active && i === demo.idx ? 'now' : ''}`}
                title={`${i + 1}. ${it.label}`}
              />
            ))}
          </div>
        </div>

        {/* flags das features de inteligência (cache / guardrails / memória) */}
        {run && <FeatureFlags run={run} />}

        {/* inspetor das collections do MongoDB */}
        {showInspector && <MongoInspector userKey={USER_KEY} conversationId={conversationId} run={run} />}

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

// ---- Flags das features: o que cada camada de MongoDB fez neste turno ----
function FeatureFlags({ run }) {
  const cache = run.cache ?? {};
  const gIn = run.guardrail?.input ?? {};
  const gOut = run.guardrail?.output ?? {};
  const mem = run.memory ?? {};

  // Guardrail
  const blocked = gIn.allowed === false;
  const masked = gOut?.masked;
  let guardClass = 'ok';
  let guardTitle = '🛡️ Guardrails · aprovado';
  let guardDetail = 'Entrada e saída dentro das políticas (ai_brain.guardrail_policies).';
  if (blocked) {
    guardClass = 'block';
    guardTitle = '🛡️ Guardrails · BLOQUEADO';
    const v = gIn.violations?.[0];
    guardDetail = v ? `${v.rule}: ${v.detail}` : 'Pedido barrado pela política.';
  } else if (masked) {
    guardClass = 'warn';
    guardTitle = '🛡️ Guardrails · PII mascarada';
    guardDetail = 'Dado sensível removido da resposta antes de enviar ao cliente.';
  } else if (gIn.violations?.length) {
    guardClass = 'warn';
    guardTitle = '🛡️ Guardrails · alerta';
    guardDetail = `${gIn.violations.length} ocorrência(s) registrada(s) em guardrail_events.`;
  }

  // Cache
  let cacheClass = 'miss';
  let cacheTitle = '⚡ Cache semântico · MISS';
  let cacheDetail = cache.blocked
    ? 'Não consultado (pedido bloqueado).'
    : `Melhor score ${cache.score ?? 0} < ${cache.threshold ?? 0.92}. ${cache.stored ? 'Resposta gravada no cache.' : ''}`;
  if (cache.hit) {
    cacheClass = 'hit';
    cacheTitle = '⚡ Cache semântico · HIT';
    cacheDetail = `Servido do MongoDB sem LLM · score ${cache.score} ≥ ${cache.threshold} · ${cache.latency_ms} ms`;
  }

  // Memória
  const stFacts = mem.longterm?.facts?.length ?? 0;
  const newFacts = mem.new_facts?.length ?? 0;
  const memTitle = '🧠 Memória · curto + longo prazo';
  const memDetail = `Curto: agent_sessions (${run.turn_count} msgs). Longo: agent_memory (${stFacts} fatos${newFacts ? `, +${newFacts} novo(s)` : ''}).`;

  return (
    <div className="feat-flags">
      <div className={`feat-card cache-${cacheClass}`}>
        <div className="feat-title">{cacheTitle}</div>
        <div className="feat-detail mono">{cacheDetail}</div>
      </div>
      <div className={`feat-card guard-${guardClass}`}>
        <div className="feat-title">{guardTitle}</div>
        <div className="feat-detail mono">{guardDetail}</div>
      </div>
      <div className="feat-card mem">
        <div className="feat-title">{memTitle}</div>
        <div className="feat-detail mono">{memDetail}</div>
      </div>
    </div>
  );
}

// ---- Inspetor: lê as collections reais do MongoDB para mostrar ao cliente ----
const INSP_TABS = [
  { key: 'cache', label: 'POC.semantic_cache' },
  { key: 'short', label: 'Memória curta · agent_sessions' },
  { key: 'memory', label: 'Memória longa · agent_memory' },
  { key: 'rules', label: 'Guardrails · regras' },
  { key: 'events', label: 'Guardrails · log' },
];

function MongoInspector({ userKey, conversationId, run }) {
  const [tab, setTab] = useState('cache');
  const [data, setData] = useState({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  const load = async () => {
    setLoading(true);
    setErr(null);
    try {
      if (tab === 'cache') setData({ cache: await api.cacheInspect() });
      else if (tab === 'short')
        setData({ short: conversationId ? await api.memoryShort(conversationId) : { turns: [] } });
      else if (tab === 'memory') setData({ memory: await api.memoryInspect(userKey) });
      else if (tab === 'rules') setData({ rules: await api.guardrailsRules() });
      else setData({ events: await api.guardrailsEvents() });
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  // recarrega ao trocar de aba ou após cada turno do agente
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tab, run]);

  const clearCache = async () => { await api.cacheClear(); load(); };
  const clearMemory = async () => { await api.memoryClear(userKey); load(); };

  const policy = data.rules?.policy ?? {};

  return (
    <div className="inspector">
      <div className="inspector-tabs">
        {INSP_TABS.map((t) => (
          <button
            key={t.key}
            className={`insp-tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
        <span className="insp-spacer" />
        <button className="insp-mini" onClick={load} disabled={loading}>↻ Atualizar</button>
        {tab === 'cache' && <button className="insp-mini danger" onClick={clearCache}>Limpar cache</button>}
        {tab === 'memory' && <button className="insp-mini danger" onClick={clearMemory}>Esquecer cliente</button>}
      </div>

      {err && <div className="dim" style={{ padding: 8 }}>⚠ {err}</div>}
      {loading && <div className="dim" style={{ padding: 8 }}>carregando…</div>}

      {!loading && tab === 'cache' && (
        <div className="insp-body">
          <div className="dim mono insp-head">
            Índice {data.cache?.index} · HIT quando score ≥ {data.cache?.threshold}
          </div>
          {(data.cache?.entries ?? []).length === 0 && <div className="dim">Cache vazio.</div>}
          {(data.cache?.entries ?? []).map((e) => (
            <div key={e._id} className="insp-row">
              <div className="insp-q mono">Q: {e.question}</div>
              <div className="insp-a">A: {short(e.answer, 160)}</div>
              <div className="insp-meta mono dim">
                hits: {e.hits} · {e.model} ·{' '}
                {e.expires_at ? `expira ${short(e.expires_at, 19)} (TTL)` : 'FAQ — sem expiração'}
              </div>
            </div>
          ))}
        </div>
      )}

      {!loading && tab === 'short' && (
        <div className="insp-body">
          <div className="dim mono insp-head">
            session_id: {data.short?.session_id ?? conversationId ?? '—'} · turnos desta conversa
          </div>
          {(data.short?.turns ?? []).length === 0 && (
            <div className="dim">Sem turnos ainda nesta conversa. "Nova conversa" zera esta memória.</div>
          )}
          {(data.short?.turns ?? []).map((t, i) => (
            <div key={i} className={`insp-row short-${t.role}`}>
              <div className="insp-q">{t.role === 'user' ? '👤' : '🤖'} {short(t.content, 200)}</div>
              <div className="insp-meta mono dim">{t.role} · {t.at}</div>
            </div>
          ))}
        </div>
      )}

      {!loading && tab === 'memory' && (
        <div className="insp-body">
          <div className="dim mono insp-head">
            user_key: {data.memory?.user_key} · fatos consolidados entre sessões (persistem em "Nova conversa")
          </div>
          {(data.memory?.facts ?? []).length === 0 && (
            <div className="dim">Nada memorizado ainda. Diga seu nome ou uma preferência ao agente.</div>
          )}
          {(data.memory?.facts ?? []).map((f, i) => (
            <div key={i} className="insp-row">
              <div className="insp-q">🔖 {f.fact}</div>
              <div className="insp-meta mono dim">{f.category} · {f.at}</div>
            </div>
          ))}
        </div>
      )}

      {!loading && tab === 'rules' && (
        <div className="insp-body">
          <div className="dim mono insp-head">
            Os guardrails que aplicamos · política em {data.rules?.policy_collection}, denylist em {data.rules?.denylist_collection}
          </div>
          <div className="insp-row">
            <div className="insp-q">🚫 Denylist semântica (bloqueio por intenção via $vectorSearch)</div>
            {(data.rules?.denylist ?? []).map((d) => (
              <div key={d._id} className="insp-meta mono dim">• "{d.phrase}" — {d.category}</div>
            ))}
          </div>
          <div className="insp-row">
            <div className="insp-q">🔒 PII mascarada na saída</div>
            {(policy.pii_patterns ?? []).map((p, i) => (
              <div key={i} className="insp-meta mono dim">• {p.name} → {p.mask}</div>
            ))}
          </div>
          <div className="insp-row">
            <div className="insp-q">⛔ Termos banidos</div>
            {(policy.banned_terms ?? []).map((p, i) => (
              <div key={i} className="insp-meta mono dim">• {p.name}: {p.pattern}</div>
            ))}
            {(policy.banned_terms ?? []).length === 0 && <div className="insp-meta mono dim">—</div>}
          </div>
          <div className="insp-meta mono dim" style={{ padding: '4px 10px' }}>
            Limiar da denylist: {policy.denylist_threshold} · bloqueio → "{short(policy.block_message, 80)}"
          </div>
        </div>
      )}

      {!loading && tab === 'events' && (
        <div className="insp-body">
          <div className="dim mono insp-head">Log de auditoria dos guardrails (mais recentes primeiro)</div>
          {(data.events?.events ?? []).length === 0 && <div className="dim">Nenhum evento registrado.</div>}
          {(data.events?.events ?? []).map((e) => (
            <div key={e._id} className={`insp-row evt-${e.action}`}>
              <div className="insp-q mono">
                [{e.stage}] {e.action.toUpperCase()} — {short(e.text_sample, 90)}
              </div>
              {(e.violations ?? []).length > 0 && (
                <div className="insp-meta mono dim">
                  {e.violations.map((v) => `${v.rule}(${v.kind})`).join(', ')}
                </div>
              )}
              <div className="insp-meta mono dim">{e.at}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
