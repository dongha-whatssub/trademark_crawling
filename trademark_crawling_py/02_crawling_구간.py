import asyncio
from playwright.async_api import async_playwright
import json
import re
from datetime import datetime

# ================= 설정 =================
START_DATE = "19500101"
END_DATE = "20260212"
KEYWORD = f"RD=[{START_DATE}~{END_DATE}]"

# [수정 필요] 복원이 필요한 페이지 구간 설정
REPAIR_START_PAGE = 1
REPAIR_END_PAGE   = 20

SAVE_INTERVAL = 20

# 저장 파일명 (기존 파일과 섞이지 않게 구분)
DATA_FILE = f"kipris_REPAIR_{REPAIR_START_PAGE}_{REPAIR_END_PAGE}.jsonl"
ERROR_FILE = f"kipris_REPAIR_{REPAIR_START_PAGE}_{REPAIR_END_PAGE}_errors.txt"
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
    print(f" >> [SAVE] {len(data_list)}건 데이터 저장 완료")

async def run_range_repair():
    async with async_playwright() as p:
        # 브라우저 실행
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(no_viewport=True)

        # 리소스 최적화 (이미지 등 제외)
        await context.route(
            "**/*",
            lambda route: route.continue_()
            if any(x in route.request.url for x in ["remoteFile.do", ".js", "ajax"]) 
            or route.request.resource_type in ["document", "script", "xhr", "fetch"]
            else route.abort()
        )

        page = await context.new_page()
        page.on("dialog", lambda d: d.accept())

        temp_results = []

        try:
            print(f"🚀 [복구 시작] {REPAIR_START_PAGE} ~ {REPAIR_END_PAGE} 페이지")
            await page.goto(
                "https://www.kipris.or.kr/khome/search/searchResult.do?tab=trademark",
                wait_until="domcontentloaded"
            )

            # 검색어 입력 및 검색
            await page.fill("#sd010301_g04_text", KEYWORD)
            await page.keyboard.press("Enter")
            await asyncio.sleep(7)

            # 필터 및 정렬 설정 (행정상태: 등록 / 정렬: 90개씩 보기)
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

            # 구간 반복 수집
            for page_no in range(REPAIR_START_PAGE, REPAIR_END_PAGE + 1):
                print(f"🔍 [P{page_no}] 추출 시도 중... {get_now()}")

                try:
                    # 페이지 점프
                    await page.fill("#srchRsltPagingNum", str(page_no))
                    await page.click("button.btn-jump")
                    await asyncio.sleep(6)

                    # 결과 로딩 대기
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

                            # 2. 등록 현황 (상태) - 강화된 로직
                            status_el = item.locator("span.badge[data-category='a']").first
                            status = (await status_el.inner_text()).strip() if await status_el.count() > 0 else "N/A"

                            # 3. 상품분류 (류) - 강화된 로직
                            category_el = item.locator("li:has-text('상품분류') button.link").first
                            category = (await category_el.inner_text()).strip() if await category_el.count() > 0 else "N/A"
                            
                            # 4. 출원인 및 최종권리자
                            applicant_el = item.locator("li:has-text('출원인') button.link").first
                            applicant = (await applicant_el.inner_text()).strip() if await applicant_el.count() > 0 else "N/A"
                            
                            owner_el = item.locator("li:has-text('최종권리자') button.link").first
                            owner = (await owner_el.inner_text()).strip() if await owner_el.count() > 0 else "N/A"

                            # 5. 이미지 URL
                            img_el = item.locator("a.thumb img").first
                            img_src = await img_el.get_attribute("src") if await img_el.count() > 0 else None
                            img_url = f"https://www.kipris.or.kr{img_src}" if img_src else "N/A"

                            temp_results.append({
                                "title": title, 
                                "reg_num": reg_num,
                                "status": status,
                                "category": category,
                                "applicant": applicant,
                                "owner": owner, 
                                "img_url": img_url, 
                                "page": page_no
                            })
                        except:
                            continue

                    # 저장 간격 체크
                    if page_no % SAVE_INTERVAL == 0:
                        save_data(temp_results)
                        temp_results = []

                except Exception as e:
                    print(f"❌ P{page_no} 실패: {e}")
                    with open(ERROR_FILE, "a", encoding="utf-8") as ef:
                        ef.write(f"P{page_no} 실패 - {e}\n")

            # 루프 종료 후 남은 데이터 저장
            if temp_results:
                save_data(temp_results)

        finally:
            await browser.close()
            print(f"🏁 구간 복구 작업이 종료되었습니다. {get_now()}")

if __name__ == "__main__":
    asyncio.run(run_range_repair())