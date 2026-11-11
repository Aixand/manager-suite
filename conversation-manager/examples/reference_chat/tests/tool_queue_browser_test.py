import asyncio,json,os
from pathlib import Path
from playwright.async_api import async_playwright,expect
ROOT=Path(os.environ.get('REFERENCE_CHAT_ARTIFACTS',str(Path(__file__).resolve().parent.parent/'.artifacts')))
ROOT.mkdir(parents=True,exist_ok=True)

async def main():
 errors=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(args=['--no-sandbox']);page=await browser.new_page(viewport={'width':1440,'height':1000})
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto(os.environ.get('REFERENCE_CHAT_URL','http://127.0.0.1:8847/'));await page.wait_for_timeout(1000)
  await page.locator('#new-chat').click();await page.locator('#start-submit').click()
  await expect(page.locator('#start-dialog')).not_to_be_visible(timeout=20000)
  await expect(page.locator('#connection-status')).to_have_text('Connected · ready')
  await page.locator('#message').fill('tool-queue-demo');await page.locator('#send').click()
  await expect(page.locator('#connection-status')).to_have_text('Agent is working')
  await page.wait_for_function("!document.querySelector('#delivery-mode option[value=next_tool_completion]').disabled")
  await page.locator('#delivery-mode').select_option('next_tool_completion')
  await expect(page.locator('#queue-hint')).to_contain_text('agent may already have continued')
  await page.locator('#message').fill('Keep the change small');await page.locator('#send').click()
  await expect(page.locator('#queue-panel')).to_contain_text('At next tool completion')
  await page.locator('#detach').click();await expect(page.locator('#connection-status')).to_have_text('Frontend detached')
  await page.wait_for_timeout(3300);await page.locator('#detach').click()
  await expect(page.locator('#timeline .message.guidance')).to_contain_text('Keep the change small')
  await expect(page.locator('#timeline .turn')).to_have_count(1)
  await expect(page.locator('#timeline .message.user')).to_have_count(1)
  await expect(page.locator('#connection-status')).to_have_text('Agent is working')
  await page.screenshot(path=str(ROOT/'tool-queue-desktop.png'))
  await expect(page.locator('#connection-status')).to_have_text('Connected · ready',timeout=10000)
  await page.reload();await expect(page.locator('#connection-status')).to_have_text('Connected · ready')
  await expect(page.locator('#timeline .message.guidance')).to_have_count(1)
  await expect(page.locator('#timeline .turn')).to_have_count(1)
  # Cancellation before the observed boundary retains guidance, rather than making a new turn.
  await page.locator('#message').fill('tool-queue-demo');await page.locator('#send').click()
  await page.wait_for_function("!document.querySelector('#delivery-mode option[value=next_tool_completion]').disabled")
  await page.locator('#delivery-mode').select_option('next_tool_completion')
  await page.locator('#message').fill('Retain if cancelled');await page.locator('#send').click()
  await expect(page.locator('#queue-panel')).to_contain_text('Retain if cancelled')
  await page.locator('#cancel').click();await expect(page.locator('#connection-status')).to_have_text('Connected · ready')
  await expect(page.locator('#queue-panel')).to_contain_text('Target turn ended')
  await expect(page.locator('#timeline .turn')).to_have_count(2)
  await page.set_viewport_size({'width':390,'height':844});await page.wait_for_timeout(300)
  assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  await page.screenshot(path=str(ROOT/'tool-queue-mobile.png'))
  assert not errors,errors
  await browser.close()
 report={'passed':True,'checks':['Tool-completion guidance stays in original turn and delivers while detached',
  'Reload preserves one guidance input without creating another turn','Cancellation retains tool guidance without converting to an after-turn send',
  'Desktop/mobile tool queue controls'],'browser_errors':errors}
 (ROOT/'tool-queue-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
asyncio.run(main())
