import { test, expect } from '@playwright/test';
test('performance filters stay consistent and duplicate panels are removed', async ({page}) => {
 const requests=[];
 await page.route('**/api/**', route=>{
  const u=new URL(route.request().url());let body={status:'error'};
  if(u.pathname.endsWith('/auth/status'))body={status:'ok',mode:'local'};
  if(u.pathname.endsWith('/store-scope'))body={status:'ok',stores:[]};
  if(u.pathname.endsWith('/performance/summary')){
   requests.push(u.search);
   body={status:'ok',months:['2026-08','2026-09'],stores:[{id:1,name:'Colombo'}],start:'2026-09-01T09:00:00',end:'2026-09-27T10:00:00',source_through:'2026-09-27T10:00:00',latest_month_included:true,outlets:[{id:1,name:'Colombo',orders:10,revenue:2000,cancellation_rate_pct:0}],products:[{id:1,name:'Rice',revenue:2000}],departments:[{name:'Grocery',revenue:2000}],hours:Array.from({length:24},(_,hour)=>({hour,orders:hour}))};
  }
  return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('/dashboard');
 await expect(page.getByRole('heading',{name:'Business Performance',exact:true})).toBeVisible();
 await page.getByLabel('Sales period').selectOption('2026-09');
 await page.getByLabel('Outlet',{exact:true}).selectOption('1');
 await expect.poll(()=>requests.at(-1)).toBe('?month=2026-09&store_id=1');
 await expect(page.getByRole('heading',{name:'Business health',exact:true})).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'Customers',exact:true})).toHaveCount(0);
 await expect(page.getByText('Inventory unavailable.',{exact:true})).toBeVisible();
 await page.screenshot({path:'test-results/performance-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'test-results/performance-mobile.png',fullPage:true});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});
