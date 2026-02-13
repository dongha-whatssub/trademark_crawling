# 페이지 구간 누락시, 구간 복원 코드

import asyncio
from playwright.async_api import async_playwright
import json

# ================= 설정 =================
START_DATE = "19500101"
END_DATE = "20260212"
KEYWORD = f"RD=[{START_DATE}~{END_DATE}]"

# 구간 복원 페이지 설정
REPAIR_START_PAGE = 1
REPAIR_END_PAGE   = 20

SAVE_INTERVAL = 20

# 저장 파일명 설정
DATA_FILE = "kipris_REPAIR_1_20.jsonl"
ERROR_FILE = "kipris_REPAIR_1_20_errors.txt"
# ========================================

def save_data(data_list):
    if not data_list:
        return
    with open(DATA_FILE, "a", encoding="utf-8") as f:
        for r in data_list:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f" >> [SAVE] {len(data_list)}건 저장")

async def run_range_repair():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(no_viewport=True)

        await context.route(
            "**/*",
            lambda route: route.continue_()
            if route.request.resource_type in ["document", "script", "xhr", "fetch"]
            else route.abort()
        )

        page = await context.new_page()
        page.on("dialog", lambda d: d.accept())

        temp_results = []

        try:
            await page.goto(
                "https://www.kipris.or.kr/khome/search/searchResult.do?tab=trademark",
                wait_until="domcontentloaded"
            )

            await page.fill("#sd010301_g04_text", KEYWORD)
            await page.keyboard.press("Enter")
            await asyncio.sleep(7)

            await page.evaluate("""() => {
                if (document.querySelector('#allCheckMs')?.checked)
                    document.querySelector('label[for="allCheckMs"]').click();
                if (!document.querySelector('#measure08')?.checked)
                    document.querySelector('label[for="measure08"]').click();
                if (typeof filterSearch === 'function') filterSearch();
                setTimeout(() => {
                    const sel = document.querySelector('#sortCondition03');
                    if (sel) { sel.value = '90'; optionSearch(); }
                }, 1000);
            }""")

            await asyncio.sleep(12)

            for page_no in range(REPAIR_START_PAGE, REPAIR_END_PAGE + 1):
                print(f"[RANGE] P{page_no} 수집 중")

                try:
                    await page.fill("#srchRsltPagingNum", str(page_no))
                    await page.click("button.btn-jump")
                    await asyncio.sleep(6)

                    await page.wait_for_selector(
                        "article.result_item, div.item-area", timeout=20000
                    )

                    items = page.locator("article.result_item, div.item-area")
                    count = await items.count()

                    for i in range(count):
                        try:
                            item = items.nth(i)

                            title = (await item.locator(
                                "h1.title button.link.under"
                            ).first.inner_text()).strip()

                            reg_num = await item.locator(
                                ".head-title button.tit"
                            ).first.inner_text()

                            applicant = await item.locator(
                                "li:has-text('출원인') button.link"
                            ).first.inner_text()

                            owner = await item.locator(
                                "li:has-text('최종권리자') button.link"
                            ).first.inner_text()

                            img_src = await item.locator(
                                "a.thumb img"
                            ).first.get_attribute("src")

                            temp_results.append({
                                "title": title,
                                "reg_num": reg_num,
                                "applicant": applicant,
                                "owner": owner,
                                "img_url": f"https://www.kipris.or.kr{img_src}" if img_src else None,
                                "page": page_no
                            })
                        except:
                            continue

                    if page_no % SAVE_INTERVAL == 0:
                        save_data(temp_results)
                        temp_results = []

                except Exception as e:
                    with open(ERROR_FILE, "a", encoding="utf-8") as ef:
                        ef.write(f"P{page_no} 실패\n")

            if temp_results:
                save_data(temp_results)

        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run_range_repair())