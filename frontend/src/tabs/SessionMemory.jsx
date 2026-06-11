import { useEffect, useRef, useState } from 'react';
import Card from '@leafygreen-ui/card';
import Badge from '@leafygreen-ui/badge';
import Banner from '@leafygreen-ui/banner';
import Button from '@leafygreen-ui/button';
import TextInput from '@leafygreen-ui/text-input';
import JsonViewer from '../components/JsonViewer.jsx';
import { api } from '../api.js';

export default function SessionMemory({ state, setState }) {
  const { sessionId, doc, lastTurn } = state;
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const chatEndRef = useRef(null);
  const initRef = useRef(false); // StrictMode monta efeitos 2x em dev — evita sessão duplicada

  const newSession = async () => {
    setError(null);
    try {
      const { session_id } = await api.createSession();
      const d = await api.getSession(session_id);
      setState({ sessionId: session_id, doc: d, lastTurn: 0 });
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    if (!sessionId && !initRef.current) {
      initRef.current = true;
      newSession();
    }
  }, []);

  // polling de 2s: o documento cru à direita acompanha o Atlas
  useEffect(() => {
    if (!sessionId) return;
    const id = setInterval(async () => {
      try {
        const d = await api.getSession(sessionId);
        setState((s) =>
          s.sessionId === sessionId && JSON.stringify(d) !== JSON.stringify(s.doc)
            ? { ...s, doc: d }
            : s,
        );
      } catch {
        /* banner já cobre erros das ações do usuário */
      }
    }, 2000);
    return () => clearInterval(id);
  }, [sessionId]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [doc?.turns?.length]);

  const send = async () => {
    if (!question.trim() || busy || !sessionId) return;
    const q = question.trim();
    setBusy(true);
    setError(null);
    setQuestion('');
    try {
      const { session } = await api.sessionChat(sessionId, q);
      setState((s) => ({
        ...s,
        doc: session,
        lastTurn: session.turns?.length ?? 0,
      }));
    } catch (e) {
      setError(e.message);
      if (e.message.includes('Sessão não encontrada')) await newSession();
    } finally {
      setBusy(false);
    }
  };

  const turns = doc?.turns ?? [];

  return (
    <div className="stack">
      {error && (
        <Banner variant="warning" darkMode>
          {error}
        </Banner>
      )}

      <div className="row">
        <Button darkMode variant="primary" onClick={newSession}>
          Nova sessão (insert_one)
        </Button>
        {sessionId && (
          <span className="dim mono" style={{ fontSize: 12 }}>
            _id: {sessionId}
          </span>
        )}
      </div>

      <div className="two-col">
        <Card darkMode>
          <div className="card-header">
            <span className="card-title">Chat</span>
            <Badge variant="darkgray">{turns.length} turnos</Badge>
          </div>
          <div className="chat-box" style={{ minHeight: 300 }}>
            {turns.length === 0 && (
              <div className="dim">
                Converse: cada turno vira um $push no array turns[] do documento ao lado —
                sem JOIN, sem tabela de turns, sem cache layer separado.
              </div>
            )}
            {turns.map((t) => (
              <div key={t.turn}>
                <div className={`chat-msg ${t.role}`}>{t.content}</div>
                {t.role === 'assistant' && (
                  <div className="chat-meta">
                    <Badge variant={t.model_used?.includes('sonnet') ? 'blue' : 'yellow'}>
                      {t.model_used}
                    </Badge>
                    <span className="dim mono">{t.tokens_used} tokens</span>
                  </div>
                )}
              </div>
            ))}
            {busy && (
              <div className="row">
                <div className="spinner" /> <span className="dim">gerando resposta…</span>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <div style={{ flex: 1 }}>
              <TextInput
                darkMode
                aria-label="mensagem"
                placeholder="ex.: meu nome é Adriano, procuro um presente"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && send()}
              />
            </div>
            <Button darkMode variant="primary" onClick={send} disabled={busy || !sessionId}>
              Enviar
            </Button>
          </div>
        </Card>

        <Card darkMode>
          <div className="card-header">
            <span className="card-title">ai_brain.session_memory — documento cru</span>
            <span style={{ whiteSpace: 'nowrap' }}><Badge variant="green">turns[] crescendo ao vivo</Badge></span>
          </div>
          {doc ? (
            <JsonViewer doc={doc} flashKey={lastTurn} />
          ) : (
            <div className="dim">criando sessão…</div>
          )}
        </Card>
      </div>
    </div>
  );
}
