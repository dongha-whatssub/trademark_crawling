import asyncio
from playwright.async_api import async_playwright
import json
import re
from datetime import datetime

# ================= 설정 =================
START_DATE = "19500101"
END_DATE = "20260212"
KEYWORD = f"RD=[{START_DATE}~{END_DATE}]"

# [수정 필요] 수집이 누락된 페이지 번호들을 리스트에 삽입
REPAIR_PAGES = [1, 3, 8, 10]

# 저장 파일명 설정 (구분을 위해 리스트 기반 이름 생성)
page_str = "_".join(map(str, REPAIR_PAGES[:5])) # 너무 길면 앞 5개만 이름에 포함
DATA_FILE = f"kipris_FIX_PAGES_{page_str}.jsonl"
ERROR_FILE = f"kipris_FIX_PAGES_{page_str}_errors.txt"
# ========================================

# 시간 저장 함수
def get_now():
    return datetime.now().strftime("%H:%M:%S")

def save_data(data_list):
    if not data_list:
        return
    with open(DATA_FILE, "a", encoding="utf-8") as f:
        for r in data_list:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f" >> [SAVE] 데이터 저장 완료 (건수: {len(data_list)}건) {get_now()}")

async def run_list_repair():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(no_viewport=True)

        # 리소스 최적화
        await context.route(
            "**/*",
            lambda route: route.continue_()
            if any(x in route.request.url for x in ["remoteFile.do", ".js", "ajax"]) 
            or route.request.resource_type in ["document", "script", "xhr", "fetch"]
            else route.abort()
        )

        page = await context.new_page()
        page.on("dialog", lambda d: d.accept())

        try:
            print(f"🚀 [특정 페이지 복원 시작] 대상: {REPAIR_PAGES}")
            await page.goto(
                "https://www.kipris.or.kr/khome/search/searchResult.do?tab=trademark",
                wait_until="domcontentloaded"
            )

            await page.fill("#sd010301_g04_text", KEYWORD)
            await page.keyboard.press("Enter")
            await asyncio.sleep(7)

            # 필터 및 정렬 설정
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

            for page_no in REPAIR_PAGES:
                print(f"🎯 [P{page_no}] 타겟팅 수집 중... {get_now()}")
                temp_results = []

                try:
                    # 해당 페이지로 점프
                    await page.fill("#srchRsltPagingNum", str(page_no))
                    await page.click("button.btn-jump")
                    await asyncio.sleep(6)

                    # 데이터 로딩 대기
                    await page.wait_for_selector(
                        "article.result_item, div.item-area", timeout=20000
                    )

                    items = page.locator("article.result_item, div.item-area")
                    count = await items.count()

                    for i in range(count):
                        try:
                            item = items.nth(i)

                            # 1. 제목 및 등록번호
                            title_el = item.locator("h1.title button.link.under").first
                            if await title_el.count() == 0: continue
                            title = (await title_el.inner_text()).strip()

                            reg_el = item.locator(".head-title button.tit").first
                            reg_num = (await reg_el.inner_text()).strip() if await reg_el.count() > 0 else "N/A"

                            # 2. 등록 현황 (상태)
                            status_el = item.locator("span.badge[data-category='a']").first
                            status = (await status_el.inner_text()).strip() if await status_el.count() > 0 else "N/A"

                            # 3. 상품분류 (류)
                            category_el = item.locator("li:has-text('상품분류') button.link").first
                            category = (await category_el.inner_text()).strip() if await category_el.count() > 0 else "N/A"

                            # 4. 출원인 및 권리자
                            applicant_el = item.locator("li:has-text('출원인') button.link").first
                            applicant = (await applicant_el.inner_text()).strip() if await applicant_el.count() > 0 else "N/A"

                            owner_el = item.locator("li:has-text('최종권리자') button.link").first
                            owner = (await owner_el.inner_text()).strip() if await owner_el.count() > 0 else "N/A"

                            # 5. 이미지
                            img_el = item.locator("a.thumb img").first
                            img_src = await img_el.get_attribute("src") if await img_el.count() > 0 else None

                            temp_results.append({
                                "title": title,
                                "reg_num": reg_num,
                                "status": status,
                                "category": category,
                                "applicant": applicant,
                                "owner": owner,
                                "img_url": f"https://www.kipris.or.kr{img_src}" if img_src else "N/A",
                                "page": page_no
                            })
                        except:
                            continue

                    # 페이지 단위로 즉시 저장
                    save_data(temp_results)

                except Exception as e:
                    print(f"❌ P{page_no} 수집 실패: {e}")
                    with open(ERROR_FILE, "a", encoding="utf-8") as ef:
                        ef.write(f"P{page_no} 실패 - {e}\n")

        finally:
            await browser.close()
            print(f"🏁 지정 페이지 {REPAIR_PAGES} 복구 완료. {get_now()}")

if __name__ == "__main__":
    asyncio.run(run_list_repair())