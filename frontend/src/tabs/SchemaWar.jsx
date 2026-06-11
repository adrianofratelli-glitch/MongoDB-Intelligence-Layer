import { useEffect, useState } from 'react';
import Card from '@leafygreen-ui/card';
import Badge from '@leafygreen-ui/badge';
import Banner from '@leafygreen-ui/banner';
import Button from '@leafygreen-ui/button';
import {
  Table,
  TableHead,
  HeaderRow,
  HeaderCell,
  TableBody,
  Row,
  Cell,
} from '@leafygreen-ui/table';
import JsonViewer from '../components/JsonViewer.jsx';
import { api } from '../api.js';

const TEMPLATE_ID = 'tmpl_product_assistant_v2';
const NEW_MODEL = 'gemini-3-pro';

const CAPABILITIES = [
  {
    concern: 'Novo modelo de LLM',
    mdb: '$set de uma nova chave em variants',
  },
  {
    concern: 'A/B test de prompt',
    mdb: 'Outro documento com version: 3',
  },
  {
    concern: 'Turnos variáveis por sessão',
    mdb: 'Array turns[] dentro do documento',
  },
  {
    concern: 'Troca de modelo primário',
    mdb: 'update_one no model_config',
  },
];

export default function SchemaWar({ state, setState }) {
  const { doc, flash } = state;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = async (withFlash = false) => {
    try {
      const d = await api.getTemplate(TEMPLATE_ID);
      setState((s) => ({ doc: d, flash: withFlash ? s.flash + 1 : s.flash }));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    if (!doc) load();
  }, []);

  const hasNewVariant = !!doc?.variants?.[NEW_MODEL];

  const toggleVariant = async () => {
    setBusy(true);
    setError(null);
    try {
      const d = hasNewVariant
        ? await api.removeVariant(TEMPLATE_ID, NEW_MODEL)
        : await api.addVariant(TEMPLATE_ID, NEW_MODEL);
      setState((s) => ({ doc: d, flash: s.flash + 1 }));
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

      <Card darkMode>
        <div className="card-header">
          <span className="card-title">
            Prompt templates como documentos — ao vivo do Atlas
          </span>
          <span style={{ whiteSpace: 'nowrap' }}>
            <Badge variant="green">1 update, zero migration</Badge>
          </span>
        </div>
        <p className="dim" style={{ marginTop: 0 }}>
          Cada variante de modelo tem a estrutura que precisa — campos novos entram
          com um $set, sem alterar as variantes existentes. O documento abaixo vem
          direto do Atlas e atualiza na tela.
        </p>
        <div className="row" style={{ marginBottom: 12 }}>
          <Button
            darkMode
            variant="primary"
            onClick={toggleVariant}
            disabled={busy || !doc}
          >
            {busy
              ? 'Aplicando $set…'
              : hasNewVariant
                ? `Remover variante ${NEW_MODEL} ($unset)`
                : `Adicionar variante de modelo (${NEW_MODEL})`}
          </Button>
        </div>
        {doc ? (
          <JsonViewer doc={doc} flashKey={flash} />
        ) : (
          <div className="dim">carregando documento…</div>
        )}
      </Card>

      <Card darkMode>
        <div className="card-title" style={{ marginBottom: 12 }}>
          Evoluções comuns da camada de AI — e como ficam em documentos
        </div>
        <Table darkMode>
          <TableHead>
            <HeaderRow>
              <HeaderCell>Mudança</HeaderCell>
              <HeaderCell>Como resolve em MongoDB</HeaderCell>
            </HeaderRow>
          </TableHead>
          <TableBody>
            {CAPABILITIES.map((r) => (
              <Row key={r.concern}>
                <Cell>{r.concern}</Cell>
                <Cell>
                  <span style={{ color: 'var(--mdb-green)' }} className="mono">
                    {r.mdb}
                  </span>
                </Cell>
              </Row>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
