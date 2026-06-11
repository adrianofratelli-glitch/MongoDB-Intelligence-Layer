import { useEffect, useState } from 'react';
import Card from '@leafygreen-ui/card';
import Badge from '@leafygreen-ui/badge';
import Banner from '@leafygreen-ui/banner';
import Button from '@leafygreen-ui/button';
import Code from '@leafygreen-ui/code';
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

const POSTGRES_DDL = `-- Modelar prompt_templates com variantes por modelo
-- exige normalizar... e prever TODAS as colunas futuras:

CREATE TABLE prompt_templates (
  id            VARCHAR(64) PRIMARY KEY,
  name          VARCHAR(128) NOT NULL,
  version       INT NOT NULL,
  tags          TEXT[]
);

CREATE TABLE prompt_variants (
  id            SERIAL PRIMARY KEY,
  template_id   VARCHAR(64) REFERENCES prompt_templates(id),
  model_name    VARCHAR(64) NOT NULL,
  system_prompt TEXT,
  user_template TEXT,
  -- campos que SÓ ALGUMAS variantes têm → colunas nullable:
  few_shot_examples JSONB NULL,
  output_format     VARCHAR(32) NULL,
  max_products      INT NULL
);

-- Toda leitura vira JOIN:
SELECT t.*, v.*
  FROM prompt_templates t
  JOIN prompt_variants v ON v.template_id = t.id
 WHERE t.name = 'product_assistant'
   AND v.model_name = 'claude-sonnet-4-5';`;

const POSTGRES_MIGRATION = `-- Saiu um modelo novo com estrutura diferente?
-- (ex.: gemini-3-pro precisa de 'safety_settings')

ALTER TABLE prompt_variants
  ADD COLUMN safety_settings JSONB NULL;   -- migration

-- + escrever script de migração
-- + revisar em code review
-- + rodar em staging
-- + janela de deploy em produção
-- + torcer para nenhum lock de tabela`;

const CONCERNS = [
  {
    concern: 'Novo modelo de LLM',
    pg: 'ALTER TABLE + migration + deploy',
    mdb: '$set de uma nova chave em variants',
  },
  {
    concern: 'A/B test de prompt',
    pg: 'Tabela de versões + JOINs condicionais',
    mdb: 'Outro documento com version: 3',
  },
  {
    concern: 'Turnos variáveis por sessão',
    pg: 'Tabela de turns + JOIN por sessão',
    mdb: 'Array turns[] dentro do documento',
  },
  {
    concern: 'Swap de provider (OpenAI → Anthropic)',
    pg: 'Migration de schema + backfill',
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

      <div className="two-col">
        <Card darkMode>
          <div className="card-header">
            <span className="card-title">PostgreSQL</span>
            <Badge variant="red">migration + deploy + downtime</Badge>
          </div>
          <Code language="sql" darkMode copyable={false}>
            {POSTGRES_DDL}
          </Code>
          <div className="spacer" style={{ height: 12 }} />
          <Code language="sql" darkMode copyable={false}>
            {POSTGRES_MIGRATION}
          </Code>
        </Card>

        <Card darkMode>
          <div className="card-header">
            <span className="card-title">MongoDB — documento real, ao vivo do Atlas</span>
            <span style={{ whiteSpace: 'nowrap' }}><Badge variant="green">1 update, zero migration</Badge></span>
          </div>
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
      </div>

      <Card darkMode>
        <div className="card-title" style={{ marginBottom: 12 }}>
          Mesmo concern, dois mundos
        </div>
        <Table darkMode>
          <TableHead>
            <HeaderRow>
              <HeaderCell>Concern</HeaderCell>
              <HeaderCell>PostgreSQL (relacional)</HeaderCell>
              <HeaderCell>MongoDB (documentos)</HeaderCell>
            </HeaderRow>
          </TableHead>
          <TableBody>
            {CONCERNS.map((r) => (
              <Row key={r.concern}>
                <Cell>{r.concern}</Cell>
                <Cell>
                  <span style={{ color: 'var(--mdb-red)' }}>{r.pg}</span>
                </Cell>
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
