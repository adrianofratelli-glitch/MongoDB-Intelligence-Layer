// Regressão visual do chrome estático das 4 abas.
// Regiões dinâmicas (counts do cluster, JSON vindo do Atlas, sessões) são
// mascaradas — o teste protege o layout, não os dados.
import { test, expect } from '@playwright/test';

const TABS = [
  { index: 0, name: 'tab1-schema-flexivel' },
  { index: 1, name: 'tab2-model-swap' },
  { index: 2, name: 'tab3-session-memory' },
  { index: 3, name: 'tab4-intent-rag' },
];

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.waitForLoadState('networkidle');
});

for (const tab of TABS) {
  test(`layout da ${tab.name}`, async ({ page }) => {
    await page.locator('.nav-pill').nth(tab.index).click();
    await page.waitForTimeout(1500); // fade-in + carga inicial dos cards

    await expect(page).toHaveScreenshot(`${tab.name}.png`, {
      fullPage: false,
      mask: [
        page.locator('.stat-bar'),          // counts mudam com o uso
        page.locator('.json-scroll'),       // documentos vivos do Atlas
        page.locator('.chat-box'),          // histórico de chat
        page.locator('.status-pill'),       // estado da conexão
        page.locator('.dim.mono'),          // _ids de sessão
      ],
      animations: 'disabled',
    });
  });
}
