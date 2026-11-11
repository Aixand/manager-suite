import os
import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright,expect
ROOT=Path(os.environ.get('REFERENCE_CHAT_ARTIFACTS', str(Path(__file__).resolve().parent.parent / '.artifacts')))
ROOT.mkdir(parents=True, exist_ok=True)

async def main():
 errors=[];checks=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(args=['--no-sandbox']);page=await browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto(os.environ.get('REFERENCE_CHAT_URL', 'http://127.0.0.1:8847/'));await page.wait_for_timeout(1000)
  await page.locator('#new-chat').click();await page.locator('#start-submit').click();await expect(page.locator('#start-dialog')).not_to_be_visible(timeout=20000);await expect(page.locator('#connection-status')).to_have_text('Connected · ready')
  async def send(text):
   await page.locator('#message').fill(text);await page.locator('#send').click()
  await send('queue-demo');await expect(page.locator('#connection-status')).to_have_text('Agent is working')
  assert await page.locator('#delivery-mode option[value=next_tool_completion]').evaluate('(el)=>el.disabled')
  await send('queued first');await send('queued second')
  await expect(page.locator('#queue-panel')).to_contain_text('2 queued messages')
  await page.locator('#detach').click();await expect(page.locator('#connection-status')).to_have_text('Frontend detached')
  await page.wait_for_timeout(3500);await page.locator('#detach').click();await expect(page.locator('#connection-status')).to_have_text('Connected · ready')
  await expect(page.locator('#queue-panel')).not_to_be_visible()
  await expect(page.locator('#timeline .message.user')).to_have_count(3)
  users=await page.locator('#timeline .message.user .message-text').all_text_contents()
  assert users==['queue-demo','queued first','queued second'],users
  await page.reload();await expect(page.locator('#connection-status')).to_have_text('Connected · ready');await expect(page.locator('#timeline .message.user')).to_have_count(3)
  checks.append('FIFO queue drains while detached; reload preserves delivered messages without duplicates')
  await send('hold');await expect(page.locator('#connection-status')).to_have_text('Agent is working')
  await send('edit me');await send('remove me');await expect(page.locator('#queue-panel .queue-item')).to_have_count(2)
  row=page.locator('#queue-panel .queue-item').filter(has_text='edit me');await row.get_by_role('button',name='Edit',exact=True).click();await row.get_by_label('Edit queued message').fill('edited queued message');await row.get_by_role('button',name='Save queued message').click();await expect(page.locator('#queue-panel')).to_contain_text('edited queued message')
  await page.locator('#queue-panel .queue-item').filter(has_text='remove me').get_by_role('button',name='Remove',exact=True).click();await expect(page.locator('#queue-panel .queue-item')).to_have_count(1)
  await page.locator('#cancel').click();await expect(page.locator('#connection-status')).to_have_text('Connected · ready');await expect(page.locator('#queue-panel')).to_contain_text('paused')
  await page.screenshot(path=str(ROOT/'queue-desktop.png'))
  await page.set_viewport_size({'width':390,'height':844});await page.wait_for_timeout(300);assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth');await page.screenshot(path=str(ROOT/'queue-mobile.png'))
  await page.locator('#queue-panel').get_by_role('button',name='Resume queue',exact=True).click();await expect(page.locator('#queue-panel')).not_to_be_visible();await expect(page.locator('#timeline .message.user')).to_have_count(5)
  users=await page.locator('#timeline .message.user .message-text').all_text_contents()
  assert users==['queue-demo','queued first','queued second','hold','edited queued message'],users
  checks.append('Queued messages can be edited/removed; cancellation pauses delivery until explicit resume; mixed turns stay chronological')
  assert not errors,errors
  await browser.close()
 report={'passed':True,'checks':checks,'browser_errors':errors};(ROOT/'queue-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
asyncio.run(main())
