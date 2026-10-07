import { test, expect } from '@playwright/test';
import { authStatus, storeScope } from './fixtures.js';

test('domains summarize all cases and separate provisional signals from saved evidence', async ({page}) => {
  const requests=[];
  const errors=[];
  page.on('pageerror', e=>errors.push(e.message));
  await page.route('**/api/**', route=>{
    const url=new URL(route.request().url());
    let body={status:'ok',items:[],total:0};
    if(url.pathname.endsWith('/auth/status'))body=authStatus;
    if(url.pathname.endsWith('/store-scope'))body=storeScope;
    if(url.pathname.endsWith('/management/summary'))body={status:'ok',cases:{open:27},case_impacts:[{case_id:'impact-1',domain:'financial',metric_id:'revenue',status:'open',priority:'high',title:'Revenue exposure',first_observed:'2026-09-01T00:00:00',last_observed:'2026-09-20T00:00:00',observations:2,impact:{financial_impact_rs:0,confidence:'measured',detail:'Measured value; not confirmed loss.'},measurement:{current:50,unit:'currency',period:'2026-09'}},{case_id:'impact-2',domain:'inventory',metric_id:'stock',status:'open',title:'Stock exposure',observations:0,impact:{}}],case_domains:[
      {domain:'financial',status:'open',priority:'high',category:'critical',count:25},
      {domain:'financial',status:'verification_pending',priority:'high',category:'critical',count:3},
      {domain:'financial',status:'resolved',priority:'high',category:'critical',count:9},
      {domain:'inventory',status:'open',priority:'worth_pursuing',category:'opportunity',count:2},
    ]};
    if(url.pathname.endsWith('/cases')) {
      requests.push(url.searchParams.get('domain'));
      body={status:'ok',total:1,items:[{case_id:'case-1',domain:'financial',metric_id:'revenue',horizon:'monthly',status:'open',priority:'high',category:'critical',latest:{title:'Revenue declined',confirm:{current:50,unit:'currency'}}}]};
    }
    if(url.pathname.endsWith('/intelligence/current-period'))body={status:'ok',items:[
      {metric_id:'revenue',domain:'financial',metric:{label:'Revenue'},status:'unavailable'},
      {metric_id:'return_rate',domain:'order_customer',metric:{label:'Returns'},status:'unavailable'},
    ]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.goto('/investigate');
  const domains=page.getByRole('region',{name:'Business areas'});
  await expect(domains.getByRole('button',{name:'Revenue & Profitability: 25 active cases'})).toBeVisible();
  await domains.getByRole('button',{name:'Revenue & Profitability: 25 active cases'}).click();
  // Scope summary for the selected area replaces the old "Business impact and
  // duration" panel (removed -- it duplicated the case detail page's own
  // history/timing section; see CaseDetail.jsx's "Case history" block).
  const summary=page.getByRole('region',{name:'Revenue & Profitability summary'});
  await expect(summary.getByRole('button',{name:'Open cases 25'})).toBeVisible();
  await expect(page.getByRole('region',{name:'Needs attention'}).getByText('3 cases awaiting verification')).toBeVisible();
  await expect.poll(()=>requests.at(-1)).toBe('financial');
  await expect(page.getByRole('link',{name:/Revenue declined/})).toHaveAttribute('href','/investigate/case-1');
  // Open by default now, filtered to the selected domain (2026-10-01: surfacing
  // the in-progress month everywhere, not hidden behind a toggle) -- no click needed.
  await expect(page.getByRole('article',{name:'Revenue',exact:true})).toBeVisible();
  await expect(page.getByRole('article',{name:'Returns',exact:true})).toHaveCount(0);
  await page.screenshot({path:'test-results/investigate-domains-desktop.png',fullPage:true});
  await page.getByRole('button',{name:'All domains',exact:true}).click();
  // Selecting a different domain no longer force-closes the panel -- it stays
  // open, just unfiltered now, so the previously-hidden Returns item appears.
  await expect(page.getByRole('article',{name:'Returns',exact:true})).toBeVisible();
  // The user can still manually collapse it; collapsing it is not forced on them.
  await page.getByText('Early signals',{exact:true}).click();
  await expect(page.getByRole('region',{name:'Current period investigations'})).toHaveCount(0);
  await page.getByText('Early signals',{exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'test-results/investigate-domains-mobile.png',fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});
