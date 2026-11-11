import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright, expect

ROOT=Path(os.environ.get('REFERENCE_CHAT_ARTIFACTS', str(Path(__file__).resolve().parent.parent / '.artifacts')))
ROOT.mkdir(parents=True, exist_ok=True)


async def main():
    errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto(os.environ.get('REFERENCE_CHAT_URL','http://127.0.0.1:8847/'))
        await page.wait_for_timeout(1000)
        await page.locator('#new-chat').click()
        await page.locator('#start-submit').click()
        await expect(page.locator('#start-dialog')).not_to_be_visible(timeout=20000)
        await expect(page.locator('#connection-status')).to_have_text('Connected · ready')
        await page.locator('#message').fill('hold')
        await page.locator('#send').click()
        await expect(page.locator('#connection-status')).to_have_text('Agent is working')
        await page.locator('#message').fill('recovery retained')
        await page.locator('#send').click()
        await expect(page.locator('#queue-panel')).to_contain_text('recovery retained')
        await page.locator('#queue-panel').get_by_role('button',name='Inspect delivery').click()
        await expect(page.locator('#inspector-body')).to_contain_text('Delivery: Not sent')
        await expect(page.get_by_role('button',name='Reconcile confirmed delivery')).to_have_count(0)
        await page.locator('#inspector-close').click()
        await page.locator('#stop').click()
        await expect(page.locator('#connection-status')).to_have_text('Backend stopped')
        await page.reload()
        await expect(page.locator('#connection-status')).to_have_text('Backend stopped')
        await expect(page.locator('#timeline .message.user')).to_have_count(1)
        await expect(page.locator('#queue-panel')).to_contain_text('recovery retained')
        await page.locator('#queue-panel').get_by_role('button',name='Inspect delivery').click()
        await expect(page.locator('#inspector-body')).to_contain_text('Delivery: Not sent')
        await expect(page.locator('#inspector-body')).to_contain_text('This records startup, not current worker availability.')
        await expect(page.locator('#detach')).to_be_disabled()
        await expect(page.get_by_role('button',name='Reconcile confirmed delivery')).to_have_count(0)
        await page.screenshot(path=str(ROOT/'recovery-desktop.png'))
        await page.set_viewport_size({'width':390,'height':844})
        await page.wait_for_timeout(300)
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=str(ROOT/'recovery-mobile.png'))
        assert not errors,errors
        await browser.close()
    report={'passed':True,'checks':['Pending delivery inspection creates no new turn',
        'Stopped backend is distinguished from frontend detach',
        'History, retained queue and delivery evidence remain readable after stop and reload',
        'Unconfirmed delivery has no reconcile/resend action','Desktop/mobile recovery views'], 'browser_errors':errors}
    (ROOT/'recovery-browser-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


asyncio.run(main())
