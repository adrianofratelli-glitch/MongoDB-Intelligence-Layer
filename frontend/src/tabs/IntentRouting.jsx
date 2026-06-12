import { useState } from 'react';
import Badge from '@leafygreen-ui/badge';
import Banner from '@leafygreen-ui/banner';
import Button from '@leafygreen-ui/button';
import Code from '@leafygreen-ui/code';
import TextInput from '@leafygreen-ui/text-input';
import PipelineSteps from '../components/PipelineSteps.jsx';
import { api } from '../api.js';

const json = (o) => JSON.stringify(o, null, 2);

// preço por 1M tokens (input/output) — Anthropic API
const PRICES = { sonnet: { in: 3, out: 15 }, haiku: { in: 1, out: 5 } };
const priceFor = (model) => (model?.includes('haiku') ? PRICES.haiku : PRICES.sonnet);

function Funnel({ funnel }) {
  const stages = [
    { label: 'candidatos avaliados (ANN)', value: funnel.num_candidates },
    { label: 'top_k retornados', value: funnel.retrieved },
    { label: `≥ min_score ${funnel.min_score}`, value: funnel.passed_min_score },
  ];
  const max = Math.max(...stages.map((s) => s.value), 1);
  return (
    <div style={{ margin: '8px 0' }}>
      {stages.map((s) => (
        <div className="funnel-stage" key={s.label}>
          <span className="funnel-label">{s.label}</span>
          <div className="funnel-bar" style={{ width: `${Math.max((s.value / max) * 60, 3)}%` }} />
          <span className="funnel-count">{s.value}</span>
        </div>
      ))}
      {funnel.context_tokens_est != null && (
        <div className="funnel-stage">
          <span className="funnel-label">contexto p/ o LLM</span>
          <span className="funnel-count">~{funnel.context_tokens_est} tokens</span>
        </div>
      )}
    </div>
  );
}

function CostReveal({ answer, funnel }) {
  const p = priceFor(answer.model);
  const costPerQuery =
    (answer.input_tokens * p.in + answer.output_tokens * p.out) / 1_000_000;
  const daily = costPerQuery * 10_000;
  return (
    <div className="cost-reveal">
      <div className="cost-reveal-title">O que a precisão do retrieval vale em escala</div>
      <div className="cost-grid">
        <div className="cost-item">
          <div className="cost-val">${costPerQuery.toFixed(5)}</div>
          <div className="cost-label">custo real desta query</div>
        </div>
        <div className="cost-item">
          <div className="cost-val">${daily.toFixed(2)}/dia</div>
          <div className="cost-label">@ 10.000 queries/dia</div>
        </div>
        <div className="cost-item">
          <div className="cost-val">${(daily * 30).toFixed(0)}/mês</div>
          <div className="cost-label">projeção mensal</div>
        </div>
        {funnel && (
          <div className="cost-item">
            <div className="cost-val">
              {funnel.passed_min_score}/{funnel.num_candidates}
            </div>
            <div className="cost-label">chunks usados vs candidatos</div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function IntentRouting({ state, setState }) {
  const { question, steps } = state;
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const setStep = (key, patch) =>
    setState((s) => ({
      ...s,
      steps: s.steps.map((st) => (st.key === key ? { ...st, ...patch } : st)),
    }));

  const run = async () => {
    if (!input.trim() || busy) return;
    const q = input.trim();
    setBusy(true);
    setError(null);
    setState({
      question: q,
      steps: [
        { key: 'intent', index: 1, status: 'running', title: 'Classificação de intent', runningLabel: 'Haiku classificando…' },
        { key: 'template', index: 2, status: 'pending', title: 'Prompt template + variante do modelo ativo' },
        { key: 'rag', index: 3, status: 'pending', title: 'Vector Search em POC.produtos_vector', runningLabel: '$vectorSearch (autoEmbed voyage-4)…' },
        { key: 'answer', index: 4, status: 'pending', title: 'Resposta do LLM', runningLabel: 'gerando resposta…' },
      ],
    });

    try {
      // 1 — intent
      const cls = await api.classify(q);
      setStep('intent', {
        status: 'done',
        content: (
          <div className="stack" style={{ gap: 8 }}>
            <div className="row">
              <Badge variant="green">{cls.intent}</Badge>
              <span className="dim mono">confidence {Number(cls.confidence).toFixed(2)}</span>
              <span className="dim mono">{cls.latency_ms} ms · {cls.classifier_model}</span>
            </div>
            <Code language="json" darkMode copyable={false}>{json(cls.intent_doc)}</Code>
          </div>
        ),
      });

      // 2 — template/variante
      setStep('template', { status: 'running', runningLabel: 'resolvendo roteamento…' });
      const route = await api.route(cls.intent);
      setStep('template', {
        status: 'done',
        content: (
          <div className="stack" style={{ gap: 8 }}>
            <div className="row">
              <span className="dim">template</span>
              <Badge variant="darkgray">{route.template?._id}</Badge>
              <span className="dim">variante para</span>
              <Badge variant={route.variant_model?.includes('sonnet') ? 'blue' : 'yellow'}>
                {route.variant_model}
              </Badge>
            </div>
            <Code language="json" darkMode copyable={false}>{json(route.variant)}</Code>
          </div>
        ),
      });

      // 3 — vector search
      setStep('rag', { status: 'running' });
      const search = await api.search(q, cls.intent);
      setStep('rag', {
        status: 'done',
        content: (
          <div className="stack" style={{ gap: 8 }}>
            <div className="row">
              <span className="dim mono">
                top_k={search.rag_config?.top_k} · min_score={search.rag_config?.min_score} · index={search.rag_config?.index}
              </span>
            </div>
            {search.funnel && <Funnel funnel={search.funnel} />}
            {search.chunks.map((c, i) => (
              <div className="row" key={i} style={{ fontSize: 13 }}>
                <Badge variant="green">{Number(c.score).toFixed(3)}</Badge>
                <span>{c.nome ?? c._id}</span>
                {c.preco != null && <span className="dim mono">R$ {c.preco}</span>}
              </div>
            ))}
            {search.chunks.length === 0 && (
              <span className="dim">nenhum chunk acima do min_score</span>
            )}
          </div>
        ),
      });

      // 4 — resposta
      setStep('answer', { status: 'running' });
      const ans = await api.answer(q, cls.intent);
      setStep('answer', {
        status: 'done',
        content: (
          <div className="stack" style={{ gap: 8 }}>
            <div className="row">
              <Badge variant={ans.answer.model?.includes('sonnet') ? 'blue' : 'yellow'}>
                {ans.answer.model}
              </Badge>
              <span className="dim mono">{ans.answer.latency_ms} ms</span>
              <span className="dim mono">{ans.answer.input_tokens}→{ans.answer.output_tokens} tokens</span>
              <span className="dim mono">{ans.chunks_used} chunks no contexto</span>
            </div>
            <div className="chat-msg assistant" style={{ maxWidth: '100%' }}>
              {ans.answer.text}
            </div>
            <CostReveal answer={ans.answer} funnel={ans.funnel} />
          </div>
        ),
      });
    } catch (e) {
      setError(e.message);
      setState((s) => ({
        ...s,
        steps: s.steps?.map((st) => (st.status === 'running' ? { ...st, status: 'pending' } : st)),
      }));
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

      <div className="card">
        <div className="card-title" style={{ marginBottom: 4 }}>
          Pergunta livre → pipeline orquestrado por documentos
        </div>
        <p className="dim" style={{ marginTop: 0 }}>
          Todo o roteamento mora em documentos: mudar a estratégia de RAG ou o modelo de um
          intent é um update, não um deploy.
        </p>
        <div className="row">
          <div style={{ flex: 1 }}>
            <TextInput
              darkMode
              aria-label="pergunta"
              placeholder="ex.: compare os fones JBL com cancelamento de ruído"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && run()}
            />
          </div>
          <Button darkMode variant="primary" onClick={run} disabled={busy}>
            {busy ? 'Executando…' : 'Executar pipeline'}
          </Button>
        </div>
      </div>

      {steps && (
        <div className="card">
          {question && (
            <div className="dim" style={{ marginBottom: 8 }}>
              pergunta: <span className="mono">{question}</span>
            </div>
          )}
          <PipelineSteps steps={steps} />
        </div>
      )}
    </div>
  );
}
