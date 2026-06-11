import { useState } from 'react';
import { Tabs, Tab } from '@leafygreen-ui/tabs';
import Sidebar from './components/Sidebar.jsx';
import SchemaWar from './tabs/SchemaWar.jsx';
import ModelSwap from './tabs/ModelSwap.jsx';
import SessionMemory from './tabs/SessionMemory.jsx';
import IntentRouting from './tabs/IntentRouting.jsx';

export default function App() {
  const [selected, setSelected] = useState(0);

  // Estado dos resultados vive AQUI (lifted state): trocar de tab ou
  // re-renderizar qualquer componente não apaga resultados de pipeline/chat.
  const [schemaWarState, setSchemaWarState] = useState({ doc: null, flash: 0 });
  const [modelSwapState, setModelSwapState] = useState({ config: null, messages: [] });
  const [sessionState, setSessionState] = useState({
    sessionId: null,
    doc: null,
    lastTurn: 0,
  });
  const [pipelineState, setPipelineState] = useState({ question: '', steps: null });

  const panes = [
    <SchemaWar state={schemaWarState} setState={setSchemaWarState} />,
    <ModelSwap state={modelSwapState} setState={setModelSwapState} />,
    <SessionMemory state={sessionState} setState={setSessionState} />,
    <IntentRouting state={pipelineState} setState={setPipelineState} />,
  ];

  return (
    <div className="app">
      <Sidebar />
      <main className="content">
        <h1 className="page-title">MongoDB Intelligence Layer</h1>
        <p className="page-subtitle">
          A camada de orquestração de AI muda na velocidade dos LLMs — em MongoDB isso é um
          update, não uma migration.
        </p>
        <Tabs
          darkMode
          aria-label="tabs da demo"
          selected={selected}
          setSelected={setSelected}
        >
          <Tab name="1 · Schema War" />
          <Tab name="2 · Model Swap ao vivo" />
          <Tab name="3 · Session Memory ao vivo" />
          <Tab name="4 · Intent Routing + RAG" />
        </Tabs>
        <div className="spacer" />
        {panes.map((pane, i) => (
          <div key={i} style={{ display: i === selected ? 'block' : 'none' }}>
            {pane}
          </div>
        ))}
      </main>
    </div>
  );
}
