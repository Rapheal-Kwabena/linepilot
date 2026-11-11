import { expect, test, type Page } from '@playwright/test';

async function waitForSchedule(page: Page, id: string) {
  await expect.poll(async () => {
    const response = await page.request.get(`/api/v1/schedules/${id}`);
    const data = await response.json();
    if (data.status === 'failed') throw new Error(`Schedule failed: ${JSON.stringify(data.diagnostics)}`);
    return data.status;
  }, { timeout: 90_000, intervals: [1000, 1500, 2000] }).toBe('completed');
}

test('navigation, theme persistence, and real workspace data', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'OPERATIONS OVERVIEW', level: 1 })).toBeVisible();
  await expect(page.getByText('Northline Components Plant').first()).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Facility' })).toBeVisible();
  for (const [route, heading] of [
    ['/planner', 'PRODUCTION PLANNER'], ['/scheduling', 'SCHEDULING STUDIO'],
    ['/simulation', 'FACTORY SIMULATION'], ['/factory', 'FACTORY MODEL'],
    ['/bottlenecks', 'BOTTLENECK ANALYSIS'], ['/utilization', 'RESOURCE UTILIZATION'],
    ['/scenarios', 'SCENARIO LAB'], ['/reports', 'ENGINEERING REPORTS'],
    ['/machines', 'MACHINES & WORK CENTERS'], ['/imports', 'DATA IMPORTS'],
    ['/settings', 'SETTINGS'],
  ] as const) {
    await page.goto(route);
    await expect(page.getByRole('heading', { name: heading, level: 1 })).toBeVisible();
  }
  await page.getByRole('button', { name: 'Industrial dark' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('button', { name: 'Industrial light' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('planner filters, sorts, and closes dialogs with Escape', async ({ page }) => {
  const response = await page.request.get('/api/v1/bootstrap');
  const bootstrap = await response.json();
  const firstJob = bootstrap.jobs[0].job_number as string;
  await page.goto('/planner');
  const filter = page.getByRole('textbox', { name: 'Filter jobs' });
  await filter.fill(firstJob);
  await expect(page.locator('.data-table tbody tr')).toHaveCount(1);
  await expect(page.getByRole('button', { name: firstJob, exact: true })).toBeVisible();
  await filter.fill('');
  const firstBefore = await page.locator('.data-table tbody tr').first().locator('td').nth(1).innerText();
  await page.getByRole('columnheader', { name: /Due date/ }).getByRole('button').click();
  const firstAfter = await page.locator('.data-table tbody tr').first().locator('td').nth(1).innerText();
  expect(firstAfter).not.toBe(firstBefore);
  const add = page.getByRole('button', { name: 'Add job' });
  await add.click();
  await expect(page.getByRole('dialog', { name: 'ADD PRODUCTION JOB' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'ADD PRODUCTION JOB' })).toHaveCount(0);
  await expect(add).toBeFocused();
});

test('production planner validates, edits, duplicates, deletes, and imports jobs', async ({ page }) => {
  const suffix = Date.now();
  const jobNumber = `E2E-${suffix}`;
  const importNumber = `E2E-CSV-${suffix}`;
  await page.goto('/planner');
  await page.getByRole('button', { name: 'Add job' }).click();
  const dialog = page.getByRole('dialog', { name: 'ADD PRODUCTION JOB' });
  await dialog.locator('#job-number').fill(jobNumber);
  await dialog.locator('#product').fill('E2E test bracket');
  await dialog.locator('#release').fill('2026-11-16T08:00');
  await dialog.locator('#due').fill('2026-11-17T16:00');
  await dialog.getByPlaceholder('e.g. CNC milling').fill('Cut and inspect');
  await dialog.getByRole('button', { name: 'Create job' }).click();
  await page.getByRole('textbox', { name: 'Filter jobs' }).fill(jobNumber);
  await expect(page.getByRole('button', { name: jobNumber, exact: true })).toBeVisible();
  await page.getByRole('button', { name: `Edit ${jobNumber}` }).click();
  const edit = page.getByRole('dialog', { name: `EDIT ${jobNumber}` });
  await edit.locator('#product').fill('E2E revised bracket');
  await edit.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByText('E2E revised bracket').first()).toBeVisible();
  await page.getByRole('button', { name: `Duplicate ${jobNumber}` }).click();
  await expect(page.getByRole('button', { name: `${jobNumber}-COPY`, exact: true })).toBeVisible();
  await page.getByRole('button', { name: `Delete ${jobNumber}-COPY` }).click();
  await page.getByRole('dialog', { name: 'DELETE PRODUCTION JOBS' }).getByRole('button', { name: 'Delete 1 job' }).click();
  await expect(page.getByRole('button', { name: `${jobNumber}-COPY`, exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: `Delete ${jobNumber}` }).click();
  await page.getByRole('dialog', { name: 'DELETE PRODUCTION JOBS' }).getByRole('button', { name: 'Delete 1 job' }).click();
  await expect(page.getByRole('button', { name: jobNumber, exact: true })).toHaveCount(0);

  const bootstrapResponse = await page.request.get('/api/v1/bootstrap');
  const bootstrap = await bootstrapResponse.json();
  const code = bootstrap.machines[0].machine_code as string;
  const csv = [
    'job_number,product_name,quantity,priority,release_time,due_time,operation_number,operation_name,machine_code,processing_minutes',
    `${importNumber},E2E imported bracket,1,3,2026-11-16T14:00:00Z,2026-11-18T00:00:00Z,10,Mill,${code},45`,
  ].join('\n');
  await page.goto('/imports');
  await page.locator('input[type=file]').setInputFiles({ name: 'e2e-jobs.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  await page.getByRole('button', { name: 'Validate import' }).click();
  await expect(page.getByText(/Validation passed for 1 rows/)).toBeVisible();
  await page.getByRole('button', { name: 'Confirm import' }).click();
  await expect(page.getByText(/Import completed: 1 rows processed/)).toBeVisible();
  await page.goto('/planner');
  await page.getByRole('textbox', { name: 'Filter jobs' }).fill(importNumber);
  await expect(page.getByRole('button', { name: importNumber, exact: true })).toBeVisible();
  await page.getByRole('button', { name: `Delete ${importNumber}` }).click();
  await page.getByRole('dialog', { name: 'DELETE PRODUCTION JOBS' }).getByRole('button', { name: 'Delete 1 job' }).click();
});

test('schedule, compare, scenario, simulation, and report flow', async ({ page }) => {
  await page.goto('/scheduling');
  await page.getByLabel('Algorithm').selectOption('fcfs');
  const fcfsResponse = page.waitForResponse((response) => response.url().endsWith('/api/v1/schedules') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Generate schedule' }).click();
  const fcfsId = (await (await fcfsResponse).json()).id as string;
  await waitForSchedule(page, fcfsId);
  await page.reload();
  await page.getByLabel('Algorithm').selectOption('cp_sat');
  const optimizedName = `E2E optimized ${Date.now()}`;
  await page.getByLabel('Run name / optional').fill(optimizedName);
  await page.getByLabel('CP-SAT search limit / s').fill('5');
  await page.getByLabel('CP-SAT seed').fill('73');
  await page.getByLabel('CP-SAT workers').fill('2');
  const optimizedResponse = page.waitForResponse((response) => response.url().endsWith('/api/v1/schedules') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Generate schedule' }).click();
  const optimizedId = (await (await optimizedResponse).json()).id as string;
  await waitForSchedule(page, optimizedId);
  await page.reload();
  await page.getByRole('combobox', { name: 'Select saved schedule' }).selectOption(optimizedId);
  await expect(page.locator('.toolbar strong').filter({ hasText: optimizedName })).toBeVisible();
  await expect(page.getByText('Seed 73', { exact: true })).toBeVisible();
  await expect(page.getByText('CP-SAT search limit 5 s', { exact: true })).toBeVisible();
  await expect(page.getByText('CP-SAT workers 2', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Compare runs' }).click();
  await page.getByRole('combobox', { name: 'Compare selected run with' }).selectOption(fcfsId);
  await expect(page.getByRole('table').filter({ hasText: 'Selected − baseline' })).toBeVisible();
  await expect(page.locator('.gantt-operation').first()).toBeVisible();

  await page.goto('/scenarios');
  const scenarioName = `E2E failure ${Date.now()}`;
  await page.locator('#scenario-name').fill(scenarioName);
  await page.locator('#scenario-baseline').selectOption(optimizedId);
  await page.locator('#scenario-type').selectOption('machine_failure');
  await page.locator('#scenario-duration').fill('45');
  await page.getByRole('button', { name: 'Create scenario' }).click();
  await expect(page.getByRole('button', { name: scenarioName })).toBeVisible();
  const scenarioResponse = page.waitForResponse((response) => response.url().includes('/api/v1/scenarios/') && response.url().endsWith('/evaluate') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Evaluate scenario' }).click();
  const scenarioRunId = (await (await scenarioResponse).json()).id as string;
  await waitForSchedule(page, scenarioRunId);
  await page.reload();
  await page.getByRole('button', { name: scenarioName }).click();
  await expect(page.getByRole('table').filter({ hasText: 'Baseline' })).toBeVisible();

  await page.goto('/simulation');
  await page.getByLabel('Schedule').selectOption(scenarioRunId);
  await page.getByRole('button', { name: 'Run simulation' }).click();
  await expect.poll(async () => {
    const response = await page.request.get(`/api/v1/simulations?schedule_run_id=${scenarioRunId}&page_size=1`);
    const data = await response.json();
    return data.items[0]?.status;
  }, { timeout: 90_000, intervals: [1000, 1500, 2000] }).toBe('completed');
  await expect(page.locator('.event-row').first()).toBeVisible();
  const savedSimulationId = await page.getByRole('combobox', { name: 'Saved simulation' }).inputValue();
  await page.getByRole('button', { name: 'Step' }).click();
  await expect(page.getByRole('slider', { name: 'Simulation event position' })).toHaveValue('1');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
  await page.getByRole('button', { name: 'Pause' }).click();
  await page.getByRole('button', { name: 'Reset' }).click();
  await expect(page.getByRole('slider', { name: 'Simulation event position' })).toHaveValue('0');
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Saved simulation' })).toHaveValue(savedSimulationId);

  await page.goto('/reports');
  await page.locator('#report-schedule').selectOption(scenarioRunId);
  await page.locator('#report-type').selectOption('schedule_summary');
  await page.getByRole('button', { name: 'Generate report' }).click();
  await expect(page.getByRole('table').getByRole('button', { name: 'CSV' }).first()).toBeEnabled();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('table').getByRole('button', { name: 'CSV' }).first().click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/linepilot-report-.*\.csv/);
});

test('infeasible schedule reports a real error', async ({ page }) => {
  await page.goto('/scheduling');
  await page.getByLabel('Algorithm').selectOption('fcfs');
  await page.getByLabel('Horizon / h').fill('1');
  const responsePromise = page.waitForResponse((response) => response.url().endsWith('/api/v1/schedules') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Generate schedule' }).click();
  const runId = (await (await responsePromise).json()).id as string;
  await expect.poll(async () => {
    const response = await page.request.get(`/api/v1/schedules/${runId}`);
    return (await response.json()).status;
  }, { timeout: 60_000, intervals: [1000, 1500, 2000] }).toBe('failed');
  await expect(page.locator('.error-state').first()).toBeVisible();
});
