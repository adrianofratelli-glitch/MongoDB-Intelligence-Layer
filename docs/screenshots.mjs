// Captura screenshots das 4 abas da POC para o README.
// Uso: node docs/screenshots.mjs  (frontend :5173 e backend :8000 no ar)
import { chromium } from 'playwright';

const BASE = 'http://localhost:5173';
const OUT = new URL('./img/', import.meta.url).pathname;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto(BASE, { waitUntil: 'networkidle' });

const tab = (n) => page.locator('[aria-label="tabs da demo"] button').nth(n);

// Tab 1 — Schema War (documento carrega sozinho)
await tab(0).click();
await page.waitForTimeout(2500);
await page.screenshot({ path: OUT + 'tab1-schema-war.png' });

// Tab 2 — Model Swap: faz uma pergunta no mini-chat
await tab(1).click();
await page.waitForTimeout(1500);
const chatInput = page.locator('input[type="text"]').first();
await chatInput.fill('qual fone JBL você recomenda para home office?');
await chatInput.press('Enter');
await page.waitForTimeout(12000);
await page.screenshot({ path: OUT + 'tab2-model-swap.png' });

// Tab 3 — Session Memory: dois turnos para mostrar a memória
await tab(2).click();
await page.waitForTimeout(2000);
const sessInput = page.locator('input[type="text"]').first();
await sessInput.fill('Meu nome é Adriano e procuro um presente de até R$200');
await sessInput.press('Enter');
await page.waitForTimeout(15000);
await sessInput.fill('Qual é o meu nome e o meu orçamento?');
await sessInput.press('Enter');
await page.waitForTimeout(15000);
await page.screenshot({ path: OUT + 'tab3-session-memory.png' });

// Tab 4 — Intent + RAG: pipeline completo
await tab(3).click();
await page.waitForTimeout(1000);
const pipeInput = page.locator('input[type="text"]').first();
await pipeInput.fill('compare os fones JBL com cancelamento de ruído');
await pipeInput.press('Enter');
await page.waitForTimeout(30000);
await page.screenshot({ path: OUT + 'tab4-intent-rag.png', fullPage: true });

await browser.close();
console.log('screenshots salvos em docs/img/');
