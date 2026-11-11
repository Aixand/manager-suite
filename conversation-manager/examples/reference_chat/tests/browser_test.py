import os
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect

ROOT = Path(os.environ.get('REFERENCE_CHAT_ARTIFACTS', str(Path(__file__).resolve().parent.parent / '.artifacts')))
ROOT.mkdir(parents=True, exist_ok=True)


async def main():
    errors = []
    checks = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(os.environ.get('REFERENCE_CHAT_URL', 'http://127.0.0.1:8847/'))
        await page.wait_for_timeout(1200)
        await page.locator('#new-chat').click()
        await expect(page.locator('#start-dialog')).to_be_visible()
        await expect(page.locator('#agent')).to_have_value('demo')
        await page.locator('#start-submit').click()
        await expect(page.locator('#start-dialog')).not_to_be_visible(timeout=20000)
        await expect(page.locator('#connection-status')).to_have_text('Connected · ready', timeout=20000)
        checks.append('Offline SDK conversation starts through UI')

        async def send(text):
            await page.locator('#message').fill(text)
            await page.locator('#send').click()

        await send('Hello <b>SDK</b>')
        await expect(page.locator('#timeline')).to_contain_text('Hello <b>SDK</b>')
        await expect(page.locator('#timeline .turn-status').last).to_have_text('completed')
        assert await page.locator('#timeline b').count() == 0
        checks.append('Canonical completion; message markup rendered as text')

        await page.get_by_role('button', name='Ask for approval', exact=True).click()
        await page.locator('#send').click()
        await expect(page.locator('#requests')).to_contain_text('Permission requested')
        await page.locator('#requests').get_by_role('button', name='Deny', exact=True).click()
        await expect(page.locator('#timeline')).to_contain_text('denied')
        checks.append('Permission request surfaced and denied through SDK')

        await page.get_by_role('button', name='Ask a question', exact=True).click()
        await page.locator('#send').click()
        await expect(page.locator('#requests')).to_contain_text('Choose a name')
        await page.locator('#requests input').fill('Test operator')
        await page.get_by_role('button', name='Submit answer', exact=True).click()
        await expect(page.locator('#timeline')).to_contain_text('Test operator')
        checks.append('Agent question answered through SDK')

        await page.get_by_role('button', name='Long-running turn', exact=True).click()
        await page.locator('#send').click()
        await expect(page.locator('#connection-status')).to_have_text('Agent is working')
        await page.locator('#detach').click()
        await expect(page.locator('#connection-status')).to_have_text('Frontend detached')
        await page.locator('#detach').click()
        await expect(page.locator('#connection-status')).to_have_text('Agent is working')
        await page.locator('#cancel').click()
        await expect(page.locator('#timeline .turn-status').last).to_have_text('cancelled')
        checks.append('Detach/reconnect preserves active turn; cancellation observed')

        await page.locator('#commands').click()
        await expect(page.locator('#palette')).to_be_visible()
        await page.locator('#palette-list button').filter(has_text='/compact').click()
        await expect(page.locator('#message')).to_have_value('/compact ')
        await page.locator('#send').click()
        await expect(page.locator('#timeline')).to_contain_text('/compact')
        await expect(page.locator('#timeline .turn-status').last).to_have_text('completed')
        checks.append('Slash-command picker invokes a real SDK command')

        await page.locator('#inspector-toggle').click()
        await page.get_by_role('button', name='Settings', exact=True).click()
        await page.get_by_label('Model', exact=True).select_option('fixture-two')
        await expect(page.locator('#model-label')).to_have_text('fixture-two')
        checks.append('Advertised session setting updated and observed')
        users_before = await page.locator('#timeline .message.user').count()
        await page.locator('#message').fill('/model ')
        await expect(page.locator('#palette')).to_be_visible()
        await expect(page.locator('#palette-list')).to_contain_text('CURRENT')
        await page.locator('#palette-list button').filter(has_text='fixture-one').click()
        await expect(page.locator('#model-label')).to_have_text('fixture-one')
        await expect(page.locator('#message')).to_have_value('')
        await page.locator('#message').fill('/model tw')
        await expect(page.locator('#palette-list button')).to_have_count(1)
        await page.locator('#palette-list button').click()
        await expect(page.locator('#model-label')).to_have_text('fixture-two')
        await page.locator('#message').fill('/permissions ')
        await expect(page.locator('#palette-list')).to_contain_text('Deny')
        await page.locator('#palette-list button').filter(has_text='deny').click()
        await expect(page.get_by_label('Tool approvals', exact=True)).to_have_value('deny')
        await page.locator('#message').fill('/permissions ask')
        await page.locator('#message').press('Enter')
        await expect(page.get_by_label('Tool approvals', exact=True)).to_have_value('ask')
        await expect(page.locator('#timeline .message.user')).to_have_count(users_before)
        checks.append('Slash settings show current values, filter options and apply without chat messages')
        await page.locator('#inspector-close').click()
        await send('hold')
        await expect(page.locator('#connection-status')).to_have_text('Agent is working')
        await page.locator('#message').fill('/model ')
        await expect(page.locator('#palette-list button').filter(has_text='fixture-one')).to_be_disabled()
        await page.locator('#inspector-toggle').click()
        await page.get_by_role('button', name='Settings', exact=True).click()
        await expect(page.get_by_label('Model', exact=True)).to_be_disabled()
        await page.locator('#cancel').click()
        await expect(page.locator('#connection-status')).to_have_text('Connected · ready')
        await expect(page.get_by_label('Model', exact=True)).to_be_enabled()
        await page.locator('#message').fill('')
        checks.append('Setting controls disable during processing and re-enable when idle')

        await page.get_by_role('button', name='Activity', exact=True).click()
        await expect(page.locator('#inspector-body')).to_contain_text('turn.completed')
        await page.screenshot(path=str(ROOT / 'desktop.png'), full_page=True)

        count = await page.locator('#timeline .message.user').count()
        await page.reload()
        await expect(page.locator('#connection-status')).to_have_text('Connected · ready')
        await expect(page.locator('#timeline')).to_contain_text('Test operator')
        await expect(page.locator('#timeline .message.user')).to_have_count(count)
        checks.append('Browser reload restores recorded history without duplicate messages')
        await page.set_viewport_size({'width': 390, 'height': 844})
        await page.wait_for_timeout(300)
        assert await page.locator('#sidebar').evaluate('(el) => el.getBoundingClientRect().right <= 0')
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        await page.screenshot(path=str(ROOT / 'mobile.png'), full_page=True)
        checks.append('Mobile viewport has no horizontal overflow')
        await browser.close()
    assert not errors, errors
    report = {'passed': True, 'checks': checks, 'browser_errors': errors}
    (ROOT / 'browser-test-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


asyncio.run(main())
